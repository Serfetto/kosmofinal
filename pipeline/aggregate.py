"""Агрегация детекций в гексы H3, зоны, статусы, оценки концентрации и файлы для веб-сервиса.

python -m pipeline.aggregate [aoi ...]

Результат: data/web/<aoi>/ — hexes.geojson (геометрия + сводные метрики), series.json (покрытие,
статусы и качество по датам), conc_<профиль>.json (концентрация, шт./км², интервалы, статусы),
<date>/{debris.png, quality.png, rgb.jpg, points.json, zones.geojson}.

Покрытие (м² мусора на км²) — вспомогательный показатель детектора. Концентрация в шт./км²
берётся из модели по полевым данным (pipeline/concentration.py) и из площади маски не выводится.
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
from scipy.ndimage import maximum as ndi_maximum

from . import status as ST
from .config import AOIS, DATA, GROUPS, H3_RES, PROCESSED
from .config import MODELS as MODELS_DIR
from .detect import dates, load_water
from .provenance import load_yaml, run_meta

WEB = DATA / "web"
DCFG = load_yaml("detector.yaml")
P_DET = DCFG["p_det"]
ALL_FLAGS = DCFG["all_flags"]
# При ветре ≥ 8 м/с море покрыто барашками: одиночные срабатывания не отличить от пены,
# оставляем только крупные скопления, а сцену исключаем из статистики по времени
STORM_WIND = DCFG["storm_wind_ms"]
ROUGH_WIND = DCFG["rough_wind_ms"]
SHIP = GROUPS.index("ship")
# Классы покрытия (вспомогательный слой), м² плавающего мусора на км² акватории
CLASS_EDGES = [0.0, 15.0, 40.0, 100.0]  # >0 низкий, ≥15 умеренный, ≥40 высокий, ≥100 очень высокий


def pollution_class(conc: np.ndarray, ndet: np.ndarray) -> np.ndarray:
    c = np.zeros(conc.shape, np.int8)
    for i, e in enumerate(CLASS_EDGES):
        c[(conc > e) & (ndet > 0)] = i + 1
    return c


ICE_FRAC = DCFG["ice_frac"]  # >0,5% акватории подо льдом — сцена ненадёжна


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


def aoi_context(aoi_id: str) -> dict:
    """Бассейн и тип вод акватории — от них зависит, какие профили концентрации применимы."""
    a = AOIS[aoi_id]
    lon0, lat0, lon1, lat1 = a["bbox"]
    cx, cy = (lon0 + lon1) / 2, (lat0 + lat1) / 2
    basin = None
    for name, (x0, y0, x1, y1) in load_yaml("profiles.yaml")["basins"].items():
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            basin = name
    water_type = "inland" if a["kind"] == "inland" else "marine"
    return {"basin": basin if water_type == "marine" else None, "water_type": water_type}


def ui_profiles() -> dict:
    return {k: v for k, v in load_yaml("profiles.yaml")["profiles"].items() if v.get("show_in_ui", True)}


def cloud_zone(src_dir, d: str) -> np.ndarray:
    """Облака, тени и перистые с буфером: края облаков SCL не маскирует, а они дают ложные «скопления»."""
    with rasterio.open(src_dir / d / "scl.tif") as s:
        scl = s.read(1)
    cloud = np.isin(scl, [3, 8, 9, 10])
    return binary_dilation(cloud, iterations=DCFG["cloud_buffer_px"]) if cloud.any() else cloud


def quality_codes(src_dir, d: str, water: np.ndarray, ice: np.ndarray, static: np.ndarray,
                  near_ship: np.ndarray) -> np.ndarray:
    """Код пригодности пикселя (pipeline/status.py QUALITY). Решение «мусор / не мусор» — отдельно."""
    with rasterio.open(src_dir / d / "scl.tif") as s:
        scl = s.read(1)
    with rasterio.open(src_dir / d / "bands.tif") as s:
        b11 = s.read(10).astype(np.float32)
    q = np.zeros(water.shape, np.uint8)
    q[~water] = 1
    q[water & ((scl == 0) | (scl == 1) | (b11 == -9999))] = 2
    q[water & np.isin(scl, [8, 9])] = 3
    q[water & (scl == 3)] = 4
    q[water & (scl == 10)] = 5
    q[water & (q == 0) & (b11 / 10000 > DCFG["glint_b11"])] = 6
    q[water & (ice | (scl == 11))] = 7
    q[water & near_ship] = 8
    q[water & static] = 9
    return q


def quality_png(q: np.ndarray, path) -> None:
    lut = np.zeros((256, 4), np.uint8)
    for code, (_, _, rgba) in ST.QUALITY.items():
        lut[code] = rgba
    Image.fromarray(lut[q[::2, ::2]]).save(path, optimize=True)


def static_objects(src_dir, ds: list[str], water: np.ndarray) -> np.ndarray:
    """Постоянные объекты (причалы, буи, садки, стоящие суда): срабатывают на многих датах ряда."""
    hits = np.zeros(water.shape, np.uint16)
    seen = np.zeros(water.shape, np.uint16)
    for d in ds:
        with rasterio.open(src_dir / d / "det.tif") as s:
            P, frac, G, valid, flags = s.read()
        valid = valid.astype(bool) & ~binary_dilation(ice_mask(src_dir, d), iterations=30)
        hits += ((P >= P_DET * 255) | (G == SHIP)) & valid
        seen += valid
    static = (hits >= 3) & (hits >= 0.2 * np.maximum(seen, 1))
    return binary_dilation(static, iterations=1)


def scene_masks(src_dir, d: str, water: np.ndarray, static: np.ndarray) -> dict | None:
    """Кандидаты в мусор и все маски отбраковки на снимке; None — видимой воды меньше min_scene_valid."""
    with rasterio.open(src_dir / d / "det.tif") as s:
        P8, frac8, G, valid, flags = s.read()
    ice = ice_mask(src_dir, d)
    ice_frac = float((ice & water).sum() / max(water.sum(), 1))
    valid = valid.astype(bool) & ~binary_dilation(ice, iterations=5)
    if valid.sum() < DCFG["min_scene_valid"] * water.sum():
        return None
    P = P8 / 255.0
    # Вокруг льдин (300 м) детекции не считаем: обломки льда и шуга похожи на мусор
    # Кильватерные следы тянутся за судами на 1–3 км: детекции рядом с судами не считаем
    ships = (G == SHIP) & valid & ~static
    near_ship = binary_dilation(ships, iterations=40) if ships.any() else ships
    ice_zone = binary_dilation(ice, iterations=30)
    cloud = cloud_zone(src_dir, d)
    cand = (P >= P_DET) & valid
    det = cand & (flags == ALL_FLAGS) & ~static & ~near_ship & ~ice_zone & ~cloud
    q = quality_codes(src_dir, d, water, ice_zone, static, near_ship)
    glint_px = (q == 6) & water
    return {"P": P, "frac": frac8 / 255.0, "G": G, "valid": valid, "flags": flags, "ice_frac": ice_frac,
            "static": static, "near_ship": near_ship, "ice_zone": ice_zone, "cloud": cloud, "cand": cand, "det": det,
            "q": q, "glint_px": glint_px,
            "glinty": float(glint_px.sum() / max(water.sum(), 1)) > DCFG["glint_scene_frac"]}


# Отбраковка кандидатов в мусор: причина — первая сработавшая по порядку. Подписи — для API и интерфейса
REJECT_REASONS = [
    ("static", "постоянный объект: причал, буй, садок, стоящее судно (≥3 снимков ряда)"),
    ("ship", "судно и кильватерный след: ближе 400 м к судну"),
    ("ice", "лёд и шуга: ближе 300 м ко льду"),
    ("cloud", "край облака или тень: ближе 100 м"),
    ("foam", "пена и барашки: в видимом диапазоне ярче, чем в NIR"),
    ("isolated", "одиночный пиксель: рябь, шум"),
    ("cfar", "блик и рябь: аномалия NIR слабее 5σ локального шума (CFAR)"),
    ("small", "шторм, лёд или сильный блик на сцене: оставлены только скопления от 4 пикселей"),
]
# Классы модели для пикселей с аномалией относительно воды (побеждает класс с наибольшей вероятностью)
MODEL_CLASSES = {
    "debris": "плавающий мусор", "organic": "водоросли, саргассум, плавник и органика",
    "ship": "суда", "foam": "пена, волны, кильватер", "cloud": "облака и тени",
}


def separation_stats(m: dict, det: np.ndarray, water: np.ndarray) -> dict:
    """Чем снимок отличает мусор от похожих объектов: классы модели для аномальных пикселей и отбраковка
    кандидатов фильтрами (каждый кандидат — одной, первой сработавшей причине). kept — итоговые детекции."""
    valid, G, flags = m["valid"], m["G"], m["flags"]
    classes = {k: int(((G == GROUPS.index(k)) & valid).sum()) for k in MODEL_CLASSES}
    left = m["cand"].copy()
    masks = {"static": m["static"], "ship": m["near_ship"], "ice": m["ice_zone"], "cloud": m["cloud"],
             "foam": (flags & 1) == 0, "isolated": (flags & 2) == 0, "cfar": (flags & 4) == 0}
    rejected = {}
    for key, _ in REJECT_REASONS[:-1]:
        hit = left & masks[key]
        rejected[key] = int(hit.sum())
        left &= ~hit
    rejected["small"] = int((left & ~det).sum())
    return {"valid_px": int(valid.sum()), "water_px": int(water.sum()),
            "glint_frac": round(float(m["glint_px"].sum() / max(water.sum(), 1)), 4),
            "model_classes": classes, "candidates": int(m["cand"].sum()), "rejected": rejected,
            "kept": int(det.sum())}


def write_separation(out_dir, stats: dict) -> None:
    doc = {"unit": "пиксели 10 м", "model_classes": MODEL_CLASSES, "reasons": dict(REJECT_REASONS),
           "dates": sorted(stats), "by_date": {d: stats[d] for d in sorted(stats)}}
    (out_dir / "separation.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def separation(aoi_id: str) -> dict:
    """Только статистика различения объектов по готовым det.tif — без пересборки остальных файлов карты."""
    src_dir = PROCESSED / aoi_id
    series = json.loads((WEB / aoi_id / "series.json").read_text(encoding="utf-8"))
    water = load_water(aoi_id)
    ds = [d for d in dates(aoi_id) if (src_dir / d / "det.json").exists()]
    static = static_objects(src_dir, ds, water)
    stats = {}
    for d, sc in zip(series["dates"], series["scenes"]):
        m = scene_masks(src_dir, d, water, static)
        det = drop_small(m["det"], 4) if sc.get("storm") else m["det"]  # шторм, лёд или блик — как в run
        stats[d] = separation_stats(m, det, water)
        if stats[d]["kept"] != sc["n_det"]:
            raise RuntimeError(f"{aoi_id} {d}: детекций {stats[d]['kept']}, в series.json {sc['n_det']}")
    write_separation(WEB / aoi_id, stats)
    return stats


def zones(det: np.ndarray, P: np.ndarray, frac: np.ndarray, prof, merge_px: int) -> list[dict]:
    """Зоны вероятного скопления: связные группы детекций (ближе merge_px пикселей — одна зона)."""
    from pyproj import Transformer
    from rasterio.features import shapes
    from shapely.geometry import mapping, shape
    from shapely.ops import transform as shp_transform
    from shapely.ops import unary_union

    if not det.any():
        return []
    grown = binary_dilation(det, iterations=merge_px) if merge_px else det
    lab, n = label(grown, structure=np.ones((3, 3)))
    lab_det = np.where(det, lab, 0)
    idx = np.arange(1, n + 1)
    npx = np.bincount(lab_det.ravel(), minlength=n + 1)[1:]
    psum = np.bincount(lab_det.ravel(), weights=P.ravel(), minlength=n + 1)[1:]
    cover = np.bincount(lab_det.ravel(), weights=(frac * 100).ravel(), minlength=n + 1)[1:]
    pmax = np.asarray(ndi_maximum(P, lab_det, idx))
    rr, cc = np.nonzero(lab_det)
    lab_px = lab_det[rr, cc]
    rsum = np.bincount(lab_px, weights=rr, minlength=n + 1)[1:]
    csum = np.bincount(lab_px, weights=cc, minlength=n + 1)[1:]
    polys: dict[int, list] = {}
    for g, v in shapes(lab.astype(np.int32), mask=lab > 0, transform=prof["transform"]):
        polys.setdefault(int(v), []).append(shape(g))
    to_wgs = Transformer.from_crs(prof["crs"], "EPSG:4326", always_xy=True).transform
    out = []
    for k in idx:
        if npx[k - 1] == 0:
            continue
        poly = unary_union(polys[k])
        lon, lat = pixel_lonlat(prof, np.array([rsum[k - 1] / npx[k - 1]]), np.array([csum[k - 1] / npx[k - 1]]))
        out.append({
            "geometry": mapping(shp_transform(to_wgs, poly).simplify(0.00005)),
            "lon": round(float(lon[0]), 5), "lat": round(float(lat[0]), 5),
            "n_pixels": int(npx[k - 1]), "zone_area_km2": round(poly.area / 1e6, 5),
            "cover_m2": round(float(cover[k - 1]), 1), "p_mean": round(float(psum[k - 1] / npx[k - 1]), 3),
            "p_max": round(float(pmax[k - 1]), 3),
        })
    # Детерминированный порядок: с севера на юг, с запада на восток
    out.sort(key=lambda z: (-z["lat"], z["lon"]))
    return out


def nearest_field(lon: np.ndarray, lat: np.ndarray, day: str) -> list[dict]:
    """Ближайшее полевое измерение (любой профиль) — расстояние и разница дат."""
    import pandas as pd

    from .splits import haversine_km

    path = DATA / "field" / "events.csv"
    if not path.exists() or len(lon) == 0:
        return [{} for _ in lon]
    ev = pd.read_csv(path)
    d = haversine_km(np.asarray(lon)[:, None], np.asarray(lat)[:, None], ev["lon"].to_numpy()[None],
                     ev["lat"].to_numpy()[None])
    j = d.argmin(1)
    t = Date.fromisoformat(day)
    return [{"field_event_id": ev["event_id"][k], "field_profile": ev["profile"][k],
             "field_date": ev["date_utc"][k], "field_distance_km": round(float(d[i, k]), 1),
             "field_date_gap_days": abs((t - Date.fromisoformat(ev["date_utc"][k])).days),
             "field_conc_items_km2": round(float(ev["conc_items_km2"][k]), 2)} for i, k in enumerate(j)]


def conc_estimates(lon: np.ndarray, lat: np.ndarray, t_scene: str, base: dict | None = None) -> dict:
    """Оценки концентрации по профилям (модель по полевым данным) для точек на момент снимка."""
    from . import concentration, covariates

    out = {}
    for pid in ui_profiles():
        if not (MODELS_DIR / f"conc_{pid}.json").exists():
            continue
        f = dict(base) if base else {"lon": lon, "lat": lat, "dist_coast_km": covariates.dist_coast_km(lon, lat)}
        f["log_dist_coast_km"] = np.log1p(f["dist_coast_km"])
        f["wind24_ms"] = covariates.wind24(lat, lon, [t_scene] * len(lon))
        out[pid] = concentration.predict(pid, lon, lat, [t_scene] * len(lon), features=f)
    return out


def _none(v, nd: int = 1):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), nd)


def run(aoi_id: str) -> None:
    from . import covariates

    src_dir = PROCESSED / aoi_id
    out_dir = WEB / aoi_id
    ds = [d for d in dates(aoi_id) if (src_dir / d / "det.json").exists()] if src_dir.exists() else []
    if not ds:
        print(f"{aoi_id}: нет обработанных снимков", flush=True)
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    water = load_water(aoi_id)
    with rasterio.open(src_dir / "water.tif") as s:
        prof = s.profile
    ids, idx = hex_index(aoi_id, water, prof)
    n_hex = len(ids)
    flat = idx[water]
    water_px = np.bincount(flat, minlength=n_hex)
    ctx = aoi_context(aoi_id)
    hex_ll = np.array([h3.cell_to_latlng(c)[::-1] for c in ids])  # lon, lat
    hex_base = {"lon": hex_ll[:, 0], "lat": hex_ll[:, 1],
                "dist_coast_km": covariates.dist_coast_km(hex_ll[:, 0], hex_ll[:, 1])}

    # Проход 1: постоянные объекты (причалы, буи, садки, стоящие суда) — срабатывают на многих датах
    static = static_objects(src_dir, ds, water)
    scenes = {}
    for d in ds:
        det_json = src_dir / d / "det.json"
        sc = json.loads(det_json.read_text(encoding="utf-8"))
        meta = json.loads((src_dir / d / "meta.json").read_text(encoding="utf-8"))
        sc.update(datetime=meta["datetime"], platform=meta.get("platform"), tile=meta.get("tile"),
                  scene_id=meta.get("id"), tile_cloud_pct=meta.get("cloud_cover"))
        if "wind_max" not in sc:
            sc["wind_max"] = scene_wind_max(aoi_id, sc)
            det_json.write_text(json.dumps(sc), encoding="utf-8")
        scenes[d] = sc

    # Проход 2: итоговые детекции, зоны, маска качества и метрики гексов по датам
    good_dates, cover_t, ndet_t, valid_t, storm_t, status_t = [], [], [], [], [], []
    conc_t = {pid: {"value": [], "lo80": [], "hi80": [], "status": []} for pid in ui_profiles()}
    conc_meta: dict = {}
    sep = {}
    for d in ds:
        m = scene_masks(src_dir, d, water, static)
        if m is None:
            continue  # облака или сплошной лёд
        P, frac, valid, q, glint_px, glinty = m["P"], m["frac"], m["valid"], m["q"], m["glint_px"], m["glinty"]
        ice_frac, det = m["ice_frac"], m["det"]
        sc = scenes[d]
        wind = sc.get("wind_max") or sc.get("wind") or 0.0
        storm = wind >= STORM_WIND
        iced = ice_frac > ICE_FRAC
        # Сильный блик: CFAR поднимает порог, мелкие детекции ненадёжны, а отсутствие мусора не подтверждается
        if storm or iced or glinty:
            det = drop_small(det, 4)
        unreliable = storm or iced or glinty  # дальше «ненадёжная сцена»: исключается из статистики по времени
        sep[d] = separation_stats(m, det, water)
        vpx = np.bincount(flat, weights=valid[water], minlength=n_hex)
        nd = np.bincount(flat, weights=det[water], minlength=n_hex)
        area = np.bincount(flat, weights=(frac * 100 * det)[water], minlength=n_hex)
        cover = np.where(vpx > 0, area / np.maximum(vpx * 1e-4, 1e-9), 0)  # м²/км²
        vfrac = vpx / np.maximum(water_px, 1)
        hex_glint = np.bincount(flat, weights=glint_px[water], minlength=n_hex) / np.maximum(water_px, 1)
        # Статус детекции гекса: отсутствие подтверждаем только при достаточной видимости, без блика и в спокойное море
        st = np.where(nd > 0, ST.DETECTION_CODE[ST.DETECTED],
                      np.where((vfrac < DCFG["min_hex_valid"]) | (hex_glint > 0.5) | unreliable,
                               ST.DETECTION_CODE[ST.INSUFFICIENT], ST.DETECTION_CODE[ST.NOT_DETECTED]))
        storm = unreliable
        good_dates.append(d)
        cover_t.append(cover)
        ndet_t.append(nd)
        valid_t.append(vfrac)
        storm_t.append(storm)
        status_t.append(st)

        dd = out_dir / d
        dd.mkdir(exist_ok=True)
        heat_png(P, det, dd / "debris.png")
        shutil.copy(src_dir / d / "rgb.jpg", dd / "rgb.jpg")
        quality_png(q, dd / "quality.png")
        qw = q[water]
        qstat = {ST.QUALITY[c][0]: round(float((qw == c).mean()), 4) for c in ST.QUALITY if c != 1}
        # Точки детекций — затравки для дрейфа и маршрута
        rr, cc = np.nonzero(det)
        lon, lat = pixel_lonlat(prof, rr, cc) if len(rr) else (np.array([]), np.array([]))
        pts = [[round(a, 5), round(b, 5), round(float(p), 2), round(float(f), 2)]
               for a, b, p, f in zip(lon, lat, P[rr, cc], frac[rr, cc])]
        (dd / "points.json").write_text(json.dumps(pts), encoding="utf-8")

        # Концентрация по профилям: модель по полевым данным в центрах гексов на момент снимка
        est = conc_estimates(hex_base["lon"], hex_base["lat"], sc["datetime"], base=hex_base)
        for pid, e in est.items():
            conc_t[pid]["value"].append(e["value"])
            conc_t[pid]["lo80"].append(e["lo80"])
            conc_t[pid]["hi80"].append(e["hi80"])
            conc_t[pid]["status"].append([ST.CONCENTRATION_CODE[x] for x in e["status"]])
            conc_meta[pid] = {"model_version": e["model_version"], "model_type": e["model_type"],
                              "nearest_field_km": np.round(e["nearest_field_km"], 1).tolist()}

        # Зоны: геометрия, площадь зоны отдельно от покрытия, концентрация профиля в центре зоны
        zs = zones(det, P, frac, prof, DCFG["zone_merge_px"])
        feats = []
        if zs:
            zl, zb = np.array([z["lon"] for z in zs]), np.array([z["lat"] for z in zs])
            zest = conc_estimates(zl, zb, sc["datetime"])
            near = nearest_field(zl, zb, d)
            for k, z in enumerate(zs):
                props = {"zone_id": f"{aoi_id}-{d}-{k + 1:03d}", "aoi": aoi_id, "date": d,
                         "scene_id": sc.get("scene_id"), "scene_datetime_utc": sc["datetime"],
                         "detection_status": ST.DETECTED, "detection_status_ru": ST.DETECTION[ST.DETECTED],
                         **{k2: v for k2, v in z.items() if k2 != "geometry"},
                         "cover_m2_km2": round(z["cover_m2"] / max(z["zone_area_km2"], 1e-9), 1),
                         "h3": h3.latlng_to_cell(z["lat"], z["lon"], H3_RES),
                         "valid_frac_scene": round(sc["valid_frac"], 3), "wind_ms": wind,
                         "sea": "шторм" if wind >= STORM_WIND else "волнение" if wind >= ROUGH_WIND else "спокойное",
                         "scene_reliable": not unreliable,
                         **near[k]}
                for pid, e in zest.items():
                    stt = e["status"][k]
                    props[f"conc_{pid}_items_km2"] = _none(e["value"][k])
                    for b in ("lo80", "hi80", "lo95", "hi95"):
                        props[f"conc_{pid}_{b}"] = _none(e[b][k])
                    props[f"conc_{pid}_status"] = stt
                    props[f"conc_{pid}_status_ru"] = ST.CONCENTRATION[stt]
                    props[f"conc_{pid}_reasons"] = "; ".join(e["reasons"][k])
                    props[f"conc_{pid}_model"] = e["model_version"]
                feats.append({"type": "Feature", "geometry": z["geometry"], "properties": props})
        (dd / "zones.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                                                     ensure_ascii=False), encoding="utf-8")

        sc.update(storm=bool(storm), ice_frac=round(ice_frac, 4), wind=wind,
                  reason=("лёд на акватории" if iced else "шторм" if wind >= STORM_WIND else "сильный блик" if glinty else None),
                  sea=("лёд" if iced else "шторм" if wind >= STORM_WIND else "волнение" if wind >= ROUGH_WIND else "спокойное"),
                  glinty=bool(glinty),
                  n_det=int(det.sum()), n_zones=len(zs), area_m2=round(float(area.sum()), 1),
                  cover=round(float(area.sum() / max(vpx.sum() * 1e-4, 1e-9)), 3), quality=qstat,
                  status={k: int((st == c).sum()) for k, c in ST.DETECTION_CODE.items()})
        print(f"  {aoi_id} {d} det={sc['n_det']} zones={len(zs)} area={sc['area_m2']:.0f} м²", flush=True)

    if not good_dates:
        # Все снимки закрыты облаками: пустой ряд сервис не показывает
        (out_dir / "series.json").unlink(missing_ok=True)
        print(f"{aoi_id}: нет снимков с видимой водой ≥ {DCFG['min_scene_valid']:.0%} — акватория не публикуется",
              flush=True)
        return
    cover_t, ndet_t, valid_t = map(np.asarray, (cover_t, ndet_t, valid_t))
    ok = (valid_t > 0.5) & ~np.asarray(storm_t)[:, None]
    n_obs = ok.sum(0)
    persistence = np.where(n_obs > 0, ((ndet_t > 0) & ok).sum(0) / np.maximum(n_obs, 1), 0)
    mean_cover = np.where(n_obs > 0, (cover_t * ok).sum(0) / np.maximum(n_obs, 1), 0)
    max_cover = (cover_t * ok).max(0)
    # Тренд: наклон покрытия, м²/км² в месяц
    t = np.array([(Date.fromisoformat(d) - Date.fromisoformat(good_dates[0])).days / 30.4 for d in good_dates])
    trend = np.zeros(n_hex)
    for i in range(n_hex):
        m = ok[:, i]
        if m.sum() >= 4 and np.ptp(t[m]) > 0:
            trend[i] = np.polyfit(t[m], cover_t[m, i], 1)[0]
    hot = persistence * np.log1p(mean_cover)

    feats = []
    for i, cell in enumerate(ids):
        lat, lng = h3.cell_to_latlng(cell)
        feats.append({
            "type": "Feature", "id": i, "geometry": hex_geometry(cell),
            "properties": {
                "i": i, "h3": cell, "lon": round(lng, 5), "lat": round(lat, 5),
                "water_km2": round(water_px[i] * 1e-4, 3),
                "dist_coast_km": round(float(hex_base["dist_coast_km"][i]), 1),
                "persistence": round(float(persistence[i]), 3), "mean_cover": round(float(mean_cover[i]), 3),
                "max_cover": round(float(max_cover[i]), 3), "trend_cover": round(float(trend[i]), 4),
                "hot": round(float(hot[i]), 4), "n_obs": int(n_obs[i]),
            },
        })
    (out_dir / "hexes.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    series = {
        "aoi": aoi_id, **ctx,
        "dates": good_dates,
        "scenes": [scenes[d] for d in good_dates],
        "cover": np.round(cover_t, 3).tolist(),
        "ndet": ndet_t.astype(int).tolist(),
        "valid": np.round(valid_t, 3).tolist(),
        "status": np.asarray(status_t, int).tolist(),
        "status_codes": {str(c): k for k, c in ST.DETECTION_CODE.items()},
        "cover_class_edges": CLASS_EDGES,
        "detector": {"p_det": P_DET, "config": run_meta("detector.yaml")["configs"]},
    }
    (out_dir / "series.json").write_text(json.dumps(series), encoding="utf-8")
    write_separation(out_dir, {d: sep[d] for d in good_dates})
    for pid, v in conc_t.items():
        st_arr = np.asarray(v["status"], int).reshape(len(v["status"]), -1)
        available = bool((st_arr != ST.CONCENTRATION_CODE[ST.UNAVAILABLE]).any())
        doc = {"profile": pid, "unit": "шт./км²", "dates": good_dates, "available": available,
               "status_codes": {str(c): k for k, c in ST.CONCENTRATION_CODE.items()}, **conc_meta.get(pid, {})}
        if available:
            for k in ("value", "lo80", "hi80"):
                doc[k] = [[_none(x) for x in row] for row in v[k]]
            doc["status"] = st_arr.tolist()
        else:
            doc["reason"] = "профиль не применим к этой акватории: другой бассейн или пресные воды"
            doc.pop("nearest_field_km", None)
        (out_dir / f"conc_{pid}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    print(f"{aoi_id}: {n_hex} гексов, {len(good_dates)} дат", flush=True)


if __name__ == "__main__":
    # --separation — только статистика различения объектов (separation.json) по готовым картам
    only_sep = "--separation" in sys.argv
    for a in [x for x in sys.argv[1:] if not x.startswith("-")] or list(AOIS):
        if not only_sep:
            run(a)
        elif (WEB / a / "series.json").exists():
            separation(a)
            print(f"{a}: separation.json", flush=True)
