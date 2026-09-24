"""Агрегация детекций в гексы H3, временные метрики и файлы для веб-сервиса.

python -m pipeline.aggregate [aoi ...]

Результат: data/web/<aoi>/ — hexes.geojson (геометрия + сводные метрики),
series.json (метрики гексов по датам), <date>/debris.png, <date>/points.json.
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import date as Date

import h3
import numpy as np
import rasterio
import requests
from PIL import Image
from rasterio.warp import transform as warp_transform
from scipy.ndimage import binary_dilation, label

from .config import AOIS, DATA, GROUPS, H3_RES, PROCESSED
from .detect import dates, load_water

WEB = DATA / "web"
P_DET = 0.5
ALL_FLAGS = 7
# При ветре ≥ 8 м/с море покрыто барашками: одиночные срабатывания не отличить от пены,
# оставляем только крупные скопления, а сцену исключаем из статистики по времени
STORM_WIND = 8.0
ROUGH_WIND = 6.0
SHIP = GROUPS.index("ship")
# Классы загрязнения по концентрации, м² плавающего мусора на км² акватории
CLASS_EDGES = [0.0, 15.0, 40.0, 100.0]  # >0 низкий, ≥15 умеренный, ≥40 высокий, ≥100 очень высокий


def pollution_class(conc: np.ndarray, ndet: np.ndarray) -> np.ndarray:
    c = np.zeros(conc.shape, np.int8)
    for i, e in enumerate(CLASS_EDGES):
        c[(conc > e) & (ndet > 0)] = i + 1
    return c


ICE_FRAC = 0.005  # >0,5% акватории подо льдом — сцена ненадёжна


def ice_mask(src_dir, d) -> np.ndarray:
    """Лёд и снег на воде: яркий в видимом и NIR, тёмный в SWIR (NDSI). Взвесь рек тусклее в NIR."""
    with rasterio.open(src_dir / d / "bands.tif") as s:
        b3, b8, b11 = (s.read(i).astype(np.float32) / 10000 for i in (3, 8, 10))
    ndsi = (b3 - b11) / (b3 + b11 + 1e-6)
    return (b3 > 0.15) & (b8 > 0.10) & (b11 < 0.08) & (ndsi > 0.6)


def scene_wind_max(aoi_id: str, sc: dict) -> float | None:
    """Максимум ветра ERA5 по сетке 3×3 над акваторией в час пролёта (у берега ветер слабее, чем в море)."""
    lon0, lat0, lon1, lat1 = AOIS[aoi_id]["bbox"]
    lons = [lon0, (lon0 + lon1) / 2, lon1] * 3
    lats = [lat0] * 3 + [(lat0 + lat1) / 2] * 3 + [lat1] * 3
    d, hour = sc["datetime"][:10], int(sc["datetime"][11:13])
    try:
        r = requests.get("https://archive-api.open-meteo.com/v1/archive", timeout=30, params=dict(
            latitude=",".join(f"{v:.3f}" for v in lats), longitude=",".join(f"{v:.3f}" for v in lons),
            hourly="wind_speed_10m", start_date=d, end_date=d, wind_speed_unit="ms", timezone="GMT"))
        vals = [x["hourly"]["wind_speed_10m"][hour] for x in r.json()]
        vals = [v for v in vals if v is not None]
        return round(max(vals), 1) if vals else None
    except Exception:
        return None


def drop_small(mask: np.ndarray, min_size: int) -> np.ndarray:
    lab, n = label(mask, structure=np.ones((3, 3)))
    if n == 0:
        return mask
    sizes = np.bincount(lab.ravel())
    keep = sizes >= min_size
    keep[0] = False
    return keep[lab]


def pixel_lonlat(prof, rows: np.ndarray, cols: np.ndarray):
    t = prof["transform"]
    xs = t.c + (cols + 0.5) * t.a
    ys = t.f + (rows + 0.5) * t.e
    lon, lat = warp_transform(prof["crs"], "EPSG:4326", xs.tolist(), ys.tolist())
    return np.asarray(lon), np.asarray(lat)


def hex_index(aoi_id: str, water: np.ndarray, prof, step: int = 5):
    """Номер гекса для каждого пикселя (-1 — не вода). Считается на прореженной сетке."""
    h, w = water.shape
    r = np.minimum(np.arange(-(-h // step)) * step + step // 2, h - 1)
    c = np.minimum(np.arange(-(-w // step)) * step + step // 2, w - 1)
    R, C = np.meshgrid(r, c, indexing="ij")
    lon, lat = pixel_lonlat(prof, R.ravel(), C.ravel())
    cells = [h3.latlng_to_cell(a, b, H3_RES) for a, b in zip(lat, lon)]
    coarse = np.asarray(cells).reshape(R.shape)
    full = np.repeat(np.repeat(coarse, step, 0), step, 1)[:h, :w]
    ids, inv = np.unique(full[water], return_inverse=True)
    idx = np.full((h, w), -1, np.int32)
    idx[water] = inv
    return list(ids), idx


def hex_geometry(cell: str) -> dict:
    ring = [[round(lng, 6), round(lat, 6)] for lat, lng in h3.cell_to_boundary(cell)]
    ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def heat_png(P: np.ndarray, det: np.ndarray, path) -> None:
    heat = np.zeros((*P.shape, 4), np.uint8)
    t = np.clip((P[det] - P_DET) / (1 - P_DET), 0, 1)
    heat[det, 0] = 255
    heat[det, 1] = (190 * (1 - t)).astype(np.uint8)
    heat[det, 3] = 255
    Image.fromarray(heat).save(path, optimize=True)


def run(aoi_id: str) -> None:
    src_dir = PROCESSED / aoi_id
    out_dir = WEB / aoi_id
    out_dir.mkdir(parents=True, exist_ok=True)
    ds = [d for d in dates(aoi_id) if (src_dir / d / "det.json").exists()]
    water = load_water(aoi_id)
    with rasterio.open(src_dir / "water.tif") as s:
        prof = s.profile
    ids, idx = hex_index(aoi_id, water, prof)
    n_hex = len(ids)
    flat = idx[water]
    water_px = np.bincount(flat, minlength=n_hex)

    # Проход 1: постоянные объекты (причалы, буи, садки, стоящие суда) — срабатывают на многих датах
    hits = np.zeros(water.shape, np.uint16)
    seen = np.zeros(water.shape, np.uint16)
    scenes = {}
    for d in ds:
        with rasterio.open(src_dir / d / "det.tif") as s:
            P, frac, G, valid, flags = s.read()
        valid = valid.astype(bool) & ~binary_dilation(ice_mask(src_dir, d), iterations=30)
        hits += ((P >= P_DET * 255) | (G == SHIP)) & valid
        seen += valid
        det_json = src_dir / d / "det.json"
        sc = json.loads(det_json.read_text(encoding="utf-8"))
        meta = json.loads((src_dir / d / "meta.json").read_text(encoding="utf-8"))
        sc.update(datetime=meta["datetime"], platform=meta.get("platform"), tile=meta.get("tile"))
        if "wind_max" not in sc:
            sc["wind_max"] = scene_wind_max(aoi_id, sc)
            det_json.write_text(json.dumps(sc), encoding="utf-8")
        scenes[d] = sc
    static = (hits >= 3) & (hits >= 0.2 * np.maximum(seen, 1))
    static = binary_dilation(static, iterations=1)

    # Проход 2: итоговые детекции и метрики гексов по датам
    good_dates, conc_t, ndet_t, area_t, valid_t, storm_t = [], [], [], [], [], []
    for d in ds:
        with rasterio.open(src_dir / d / "det.tif") as s:
            P8, frac8, G, valid, flags = s.read()
        ice = ice_mask(src_dir, d)
        ice_frac = float((ice & water).sum() / max(water.sum(), 1))
        valid = valid.astype(bool) & ~binary_dilation(ice, iterations=5)
        if valid.sum() < 0.3 * water.sum():
            continue  # облака или сплошной лёд
        P = P8 / 255.0
        frac = frac8 / 255.0
        # Вокруг льдин (300 м) детекции не считаем: обломки льда и шуга похожи на мусор
        # Кильватерные следы тянутся за судами на 1–3 км: детекции рядом с судами не считаем
        ships = (G == SHIP) & valid & ~static
        near_ship = binary_dilation(ships, iterations=40) if ships.any() else ships
        det = ((P >= P_DET) & valid & (flags == ALL_FLAGS) & ~static & ~near_ship
               & ~binary_dilation(ice, iterations=30))
        wind = scenes[d].get("wind_max") or scenes[d].get("wind") or 0.0
        storm = wind >= STORM_WIND
        iced = ice_frac > ICE_FRAC
        if storm or iced:
            det = drop_small(det, 4)
        storm = storm or iced  # дальше «ненадёжная сцена»: исключается из статистики по времени
        vpx = np.bincount(flat, weights=valid[water], minlength=n_hex)
        nd = np.bincount(flat, weights=det[water], minlength=n_hex)
        area = np.bincount(flat, weights=(frac * 100 * det)[water], minlength=n_hex)
        conc = np.where(vpx > 0, area / np.maximum(vpx * 1e-4, 1e-9), 0)  # м²/км²
        good_dates.append(d)
        conc_t.append(conc)
        ndet_t.append(nd)
        area_t.append(area)
        valid_t.append(vpx / np.maximum(water_px, 1))
        storm_t.append(storm)

        dd = out_dir / d
        dd.mkdir(exist_ok=True)
        heat_png(P, det, dd / "debris.png")
        shutil.copy(src_dir / d / "rgb.jpg", dd / "rgb.jpg")
        # Точки детекций — затравки для дрейфа и маршрута
        rr, cc = np.nonzero(det)
        lon, lat = pixel_lonlat(prof, rr, cc) if len(rr) else (np.array([]), np.array([]))
        pts = [[round(a, 5), round(b, 5), round(float(p), 2), round(float(f), 2)]
               for a, b, p, f in zip(lon, lat, P[rr, cc], frac[rr, cc])]
        (dd / "points.json").write_text(json.dumps(pts), encoding="utf-8")
        sc = scenes[d]
        sc.update(storm=bool(storm), ice_frac=round(ice_frac, 4), wind=wind,
                  reason=("лёд на акватории" if iced else "шторм" if storm else None),
                  sea=("лёд" if iced else "шторм" if storm else "волнение" if wind >= ROUGH_WIND else "спокойное"),
                  n_det=int(det.sum()), area_m2=round(float(area.sum()), 1),
                  conc=round(float(area.sum() / max(vpx.sum() * 1e-4, 1e-9)), 3))
        print(f"  {aoi_id} {d} det={sc['n_det']} area={sc['area_m2']:.0f} м²", flush=True)

    conc_t, ndet_t, valid_t = map(np.asarray, (conc_t, ndet_t, valid_t))
    ok = (valid_t > 0.5) & ~np.asarray(storm_t)[:, None]
    n_obs = ok.sum(0)
    persistence = np.where(n_obs > 0, ((ndet_t > 0) & ok).sum(0) / np.maximum(n_obs, 1), 0)
    mean_conc = np.where(n_obs > 0, (conc_t * ok).sum(0) / np.maximum(n_obs, 1), 0)
    max_conc = (conc_t * ok).max(0)
    # Тренд: наклон концентрации, м²/км² в месяц
    t = np.array([(Date.fromisoformat(d) - Date.fromisoformat(good_dates[0])).days / 30.4 for d in good_dates])
    trend = np.zeros(n_hex)
    for i in range(n_hex):
        m = ok[:, i]
        if m.sum() >= 4 and np.ptp(t[m]) > 0:
            trend[i] = np.polyfit(t[m], conc_t[m, i], 1)[0]
    hot = persistence * np.log1p(mean_conc)

    feats = []
    for i, cell in enumerate(ids):
        lat, lng = h3.cell_to_latlng(cell)
        feats.append({
            "type": "Feature", "id": i, "geometry": hex_geometry(cell),
            "properties": {
                "i": i, "h3": cell, "lon": round(lng, 5), "lat": round(lat, 5),
                "water_km2": round(water_px[i] * 1e-4, 3),
                "persistence": round(float(persistence[i]), 3), "mean_conc": round(float(mean_conc[i]), 3),
                "max_conc": round(float(max_conc[i]), 3), "trend": round(float(trend[i]), 4),
                "hot": round(float(hot[i]), 4), "n_obs": int(n_obs[i]),
            },
        })
    (out_dir / "hexes.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    series = {
        "dates": good_dates,
        "scenes": [scenes[d] for d in good_dates],
        "conc": np.round(conc_t, 3).tolist(),
        "ndet": ndet_t.astype(int).tolist(),
        "valid": np.round(valid_t, 3).tolist(),
        "class_edges": CLASS_EDGES,
    }
    (out_dir / "series.json").write_text(json.dumps(series), encoding="utf-8")
    print(f"{aoi_id}: {n_hex} гексов, {len(good_dates)} дат", flush=True)


if __name__ == "__main__":
    for a in sys.argv[1:] or list(AOIS):
        run(a)
