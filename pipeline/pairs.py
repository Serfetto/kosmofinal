"""Реестр сопоставления полевых событий со снимками Sentinel-2.

python -m pipeline.pairs build [--offline]   # кандидаты, сдвиги, дрейфовый буфер, качество, решения
python -m pipeline.pairs detect              # признаки детектора по следу принятых пар (скачивает фрагменты)

Выход: data/registry/pairs.csv (все кандидаты с решениями и причинами), pairs.geojson (следы событий),
summary.json. Ответы STAC и измерения качества кешируются в data/registry/cache — с --offline реестр
пересобирается идентично без сети.

Концентрация полевого события относится ко всей обследованной площади, поэтому качество и признаки
считаются по следу события с буфером на дрейф, а не по центральному пикселю.
"""
from __future__ import annotations

import json
import sys
import warnings
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import rasterio
from global_land_mask import globe
from pyproj import CRS, Transformer
from rasterio.features import geometry_mask
from rasterio.warp import transform_geom
from rasterio.windows import from_bounds
from shapely import wkt
from shapely.geometry import mapping, shape
from shapely.ops import transform as shp_transform

from .config import DATA
from .covariates import event_reference_time, wind24
from .field import load_events
from .provenance import load_yaml, run_meta, write_json

warnings.filterwarnings("ignore")

REG = DATA / "registry"
CACHE = REG / "cache"
CONFIG = "pairs.yaml"
TIER_RANK = {"A": 0, "B": 1, "C": 2}


def _utm(lon: float, lat: float) -> CRS:
    zone = int((lon + 180) // 6) + 1
    return CRS.from_epsg((32600 if lat >= 0 else 32700) + zone)


def footprint(ev: pd.Series, buffer_km: float):
    """След события (полоса, сегменты или точка с радиусом неопределённости) с буфером, WGS84."""
    g = wkt.loads(ev["geometry_wkt"])
    crs = _utm(ev["lon"], ev["lat"])
    fwd = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
    inv = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
    half_w = (ev["width_m"] / 2 if pd.notna(ev["width_m"]) else 5.0)
    r = half_w + 1000 * (float(ev["footprint_radius_km"] or 0) + buffer_km)
    return shp_transform(inv, shp_transform(fwd, g).buffer(max(r, 10.0)))


# ---------- каталог ----------

_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        import planetary_computer as pc
        from pystac_client import Client

        _CLIENT = Client.open(load_yaml(CONFIG)["catalog"], modifier=pc.sign_inplace)
    return _CLIENT


def _retry(fn, attempts: int = 5):
    """Каталог иногда отвечает 503 — повторяем с паузой."""
    import time

    for k in range(attempts):
        try:
            return fn()
        except Exception:
            if k == attempts - 1:
                raise
            time.sleep(3 * (k + 1))


def search(ev: pd.Series, collection: str, days: int, offline: bool) -> list[dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"stac_{collection}_{ev['event_id'].replace(':', '_')}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["items"]
    if offline:
        raise RuntimeError(f"нет кеша STAC для {ev['event_id']} ({collection}); запустите без --offline")
    d = datetime.fromisoformat(ev["date_utc"]).date()
    bbox = footprint(ev, 0).bounds
    query = dict(collections=[collection], bbox=list(bbox),
                 datetime=f"{d - timedelta(days=days)}/{d + timedelta(days=days)}T23:59:59Z")
    items = []
    for it in _retry(lambda: list(_client().search(**query).items())):
        p = it.properties
        items.append({"id": it.id, "datetime": it.datetime.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "platform": p.get("platform"), "tile": p.get("s2:mgrs_tile") or p.get("landsat:wrs_path"),
                      "cloud": p.get("eo:cloud_cover"), "processing_baseline": p.get("s2:processing_baseline"),
                      "geometry": it.geometry})
    items.sort(key=lambda x: (x["datetime"], x["id"]))
    path.write_text(json.dumps({"query": query, "retrieved_utc": datetime.now(timezone.utc).isoformat(),
                                "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    return items


def _signed_item(collection: str, item_id: str):
    return _retry(lambda: _client().get_collection(collection).get_item(item_id))


# ---------- качество по следу ----------

def scene_quality(ev: pd.Series, item: dict, fp, collection: str, cfg: dict, offline: bool) -> dict:
    """Покрытие, чистая вода и блик по следу с буфером — по SCL и B11 сцены."""
    CACHE.mkdir(parents=True, exist_ok=True)
    key = f"q_{ev['event_id'].replace(':', '_')}__{item['id']}__{round(fp.area, 8)}"
    path = CACHE / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    if offline:
        raise RuntimeError(f"нет кеша качества для {ev['event_id']} / {item['id']}")
    out = _retry(lambda: _read_quality(item, fp, collection))
    path.write_text(json.dumps(out), encoding="utf-8")
    return out


def _read_quality(item: dict, fp, collection: str) -> dict:
    from .s2 import GDAL_ENV

    it = _signed_item(collection, item["id"])
    out = {}
    with rasterio.Env(**GDAL_ENV), rasterio.open(it.assets["SCL"].href) as src:
        g = transform_geom("EPSG:4326", src.crs, mapping(fp))
        b = shape(g).bounds
        win = from_bounds(*b, transform=src.transform).round_offsets().round_lengths()
        scl = src.read(1, window=win, boundless=True, fill_value=0)
        tr = src.window_transform(win)
        inside = ~geometry_mask([g], out_shape=scl.shape, transform=tr)
        rows, cols = np.nonzero(inside)
        xs, ys = rasterio.transform.xy(tr, rows, cols)
        lon, lat = Transformer.from_crs(src.crs, "EPSG:4326", always_xy=True).transform(np.asarray(xs), np.asarray(ys))
        marine = np.zeros(scl.shape, bool)
        marine[rows, cols] = globe.is_ocean(np.asarray(lat), np.asarray(lon))
        has = inside & (scl != 0)
        clear = marine & (scl == 6)
        out["footprint_coverage"] = float(has.sum() / max(inside.sum(), 1))
        out["marine_px"] = int((marine & has).sum())
        out["clear_water_frac"] = float(clear.sum() / max((marine & has).sum(), 1))
        out["cloud_frac"] = float((np.isin(scl, [8, 9, 10]) & marine).sum() / max((marine & has).sum(), 1))
        out["land_frac"] = float(((~marine) & inside).sum() / max(inside.sum(), 1))
    with rasterio.Env(**GDAL_ENV), rasterio.open(it.assets["B11"].href) as src:
        b11 = src.read(1, window=win, boundless=True, fill_value=0).astype(np.float32)
    off = -1000.0 if float(item.get("processing_baseline") or 0) >= 4.0 else 0.0
    v = (b11[clear] + off) / 10000.0
    out["glint_b11"] = float(np.median(v)) if v.size else None
    out["n_valid_px_20m"] = int(clear.sum())
    return out


# ---------- реестр ----------

def dt_hours(t_scene: datetime, t0: datetime, t1: datetime) -> tuple[float, float, float, float]:
    """(dt_min, dt_max, dt_repr, max|dt|), ч; dt = t_снимка − t_наблюдения."""
    dmin = (t_scene - t1).total_seconds() / 3600
    dmax = (t_scene - t0).total_seconds() / 3600
    drep = (t_scene - (t0 + (t1 - t0) / 2)).total_seconds() / 3600
    return dmin, dmax, drep, max(abs(dmin), abs(dmax))


def build(offline: bool = False) -> pd.DataFrame:
    cfg = load_yaml(CONFIG)
    ev_all = load_events()
    rows, feats = [], []
    t_ref = [event_reference_time(a, b, k) for a, b, k in zip(ev_all["t_start_utc"], ev_all["t_end_utc"],
                                                             ev_all["time_known"])]
    winds = wind24(ev_all["lat"], ev_all["lon"], t_ref)
    for k, ev in ev_all.iterrows():
        t0, t1 = datetime.fromisoformat(ev["t_start_utc"]), datetime.fromisoformat(ev["t_end_utc"])
        base = {
            "event_id": ev["event_id"], "sample_ids": ev["sample_id"], "source_id": ev["source_id"],
            "profile": ev["profile"], "role": ev["role"], "geometry_type": ev["geometry_type"],
            "event_start_utc": ev["t_start_utc"], "event_end_utc": ev["t_end_utc"], "time_known": ev["time_known"],
            "lon": ev["lon"], "lat": ev["lat"], "wind_era5_ms": round(float(winds[k]), 2),
            "current_ms": cfg["drift"]["current_ms"], "current_source": cfg["drift"]["current_source"],
            "label_source": f"field:{ev['source_id']} {ev['target_scope']} {ev['measurement_profile']} "
                            f"(плотность по полосе, не пиксельная разметка)",
        }
        fp0 = footprint(ev, 0)
        feats.append({"type": "Feature", "geometry": mapping(fp0),
                      "properties": {"event_id": ev["event_id"], "profile": ev["profile"]}})
        items = search(ev, cfg["collection"], cfg["search_window_days"], offline)
        if not items:
            other = []
            for coll in cfg["unsupported_collections"]:
                other += [(coll, it) for it in search(ev, coll, cfg["search_window_days"], offline)]
            if other:
                coll, it = other[0]
                rows.append({**base, "collection": coll, "scene_id": it["id"], "scene_datetime_utc": it["datetime"],
                             "platform": it["platform"], "tile_cloud_pct": it["cloud"], "decision": "rejected",
                             "reason_code": "SENSOR_UNSUPPORTED",
                             "reason_text": f"есть {len(other)} сцен(ы) {coll}, но детектору нужен красный край Sentinel-2"})
            else:
                rows.append({**base, "collection": cfg["collection"], "decision": "rejected", "reason_code": "NO_SCENE",
                             "reason_text": f"нет сцен Sentinel-2 L2A в окне ±{cfg['search_window_days']} сут."})
            continue
        cands = []
        for it in items:
            ts = datetime.fromisoformat(it["datetime"].replace("Z", "+00:00")).replace(tzinfo=None)
            dmin, dmax, drep, amax = dt_hours(ts, t0, t1)
            tier = "A" if (ev["time_known"] and amax <= cfg["sync"]["tier_a_max_h"]) else \
                "B" if amax <= cfg["sync"]["tier_b_max_h"] else "C"
            drift_km = amax * 3600 * (cfg["drift"]["current_ms"] + cfg["drift"]["windage"] * winds[k]) / 1000
            buf = min(drift_km, cfg["drift"]["max_feature_buffer_km"])
            fp = footprint(ev, buf)
            rec = {**base, "collection": cfg["collection"], "scene_id": it["id"], "platform": it["platform"],
                   "tile": it["tile"], "scene_datetime_utc": it["datetime"], "tile_cloud_pct": it["cloud"],
                   "dt_min_h": round(dmin, 2), "dt_max_h": round(dmax, 2), "dt_repr_h": round(drep, 2),
                   "abs_dt_max_h": round(amax, 2), "sync_tier": tier, "drift_buffer_km": round(drift_km, 2),
                   "feature_buffer_km": round(buf, 2), "footprint_wkt": fp.wkt,
                   "footprint_area_km2": None}
            # Площадь следа в км² (равновеликая проекция UTM)
            crs = _utm(ev["lon"], ev["lat"])
            fwd = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
            rec["footprint_area_km2"] = round(shp_transform(fwd, fp).area / 1e6, 3)
            if tier == "C":
                rec.update(decision="context", reason_code="UNRELIABLE_SYNC",
                           reason_text=f"сдвиг до {amax:.0f} ч > {cfg['sync']['tier_b_max_h']} ч; дрейф до {drift_km:.0f} км")
            else:
                q = scene_quality(ev, it, fp, cfg["collection"], cfg, offline)
                rec.update(q)
                qc = cfg["quality"]
                if q["footprint_coverage"] < qc["min_coverage"]:
                    rec.update(decision="rejected", reason_code="INSUFFICIENT_COVERAGE",
                               reason_text=f"данные сцены покрывают {q['footprint_coverage']:.0%} следа")
                elif q["clear_water_frac"] < qc["min_clear_water"]:
                    rec.update(decision="rejected", reason_code="UNUSABLE_PIXELS",
                               reason_text=f"чистой воды {q['clear_water_frac']:.0%} (облака {q['cloud_frac']:.0%})")
                elif q["glint_b11"] is not None and q["glint_b11"] > qc["max_glint_b11"]:
                    rec.update(decision="rejected", reason_code="GLINT",
                               reason_text=f"медиана B11 {q['glint_b11']:.3f} > {qc['max_glint_b11']}")
                elif tier == "A":
                    rec.update(decision="accepted", reason_code="ACCEPTED", reason_text="сдвиг ≤ 3 ч, чистая вода")
                else:
                    why = "время наблюдения неизвестно" if not ev["time_known"] else f"сдвиг {amax:.1f} ч"
                    rec.update(decision="accepted", reason_code="ACCEPTED_UNCERTAIN_TIME",
                               reason_text=f"{why}: только для исследовательского сравнения")
            if ev["geometry_type"] == "point":
                rec["reason_text"] += "; известна только середина полосы (GEOMETRY_POINT_ONLY)"
            cands.append(rec)
        # Одна лучшая пара на событие; остальные принятые — DUPLICATE_BETTER_SCENE
        ok = [c for c in cands if c["decision"] == "accepted"]
        ok.sort(key=lambda c: (TIER_RANK[c["sync_tier"]], c["abs_dt_max_h"], -c.get("clear_water_frac", 0), c["scene_id"]))
        for c in ok[1:]:
            c.update(decision="rejected", reason_code="DUPLICATE_BETTER_SCENE",
                     reason_text=f"выбрана сцена {ok[0]['scene_id']}")
        rows += cands

    reg = pd.DataFrame(rows)
    reg.insert(0, "pair_id", [f"{e}__{s}" if isinstance(s, str) else f"{e}__none"
                              for e, s in zip(reg["event_id"], reg.get("scene_id", [None] * len(reg)))])
    REG.mkdir(parents=True, exist_ok=True)
    meta = run_meta(CONFIG, "profiles.yaml")
    reg["config_hash"] = meta["configs"][CONFIG]
    reg.to_csv(REG / "pairs.csv", index=False, encoding="utf-8")
    (REG / "pairs.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False),
                                       encoding="utf-8")
    ev_best = reg.sort_values("decision").groupby("event_id")["decision"].agg(
        lambda s: "accepted" if (s == "accepted").any() else "context" if (s == "context").any() else "rejected")
    summary = {
        "events": int(reg["event_id"].nunique()), "candidates": int(reg["scene_id"].notna().sum()),
        "events_by_outcome": ev_best.value_counts().to_dict(),
        "rows_by_reason": reg["reason_code"].value_counts().to_dict(),
        "accepted_by_profile_tier": {f"{p}/{t}": int(n) for (p, t), n in
                                     reg[reg["decision"] == "accepted"].groupby(["profile", "sync_tier"]).size().items()},
        "no_scene_by_source": reg[reg["reason_code"] == "NO_SCENE"]["source_id"].value_counts().to_dict(),
        "meta": meta,
    }
    write_json(REG / "summary.json", summary)
    return reg


# ---------- признаки детектора по следу ----------

def detect_pairs() -> pd.DataFrame:
    """Для принятых пар: фрагмент снимка вокруг следа → детектор → доля детекций внутри следа."""
    from PIL import Image
    from scipy.ndimage import label

    from .detect import BAD_SCL, P_DET, detect_array
    from .s2 import Scene, bbox_grid, load_scene

    cfg = load_yaml(CONFIG)
    reg = pd.read_csv(REG / "pairs.csv")
    acc = reg[reg["decision"] == "accepted"]
    out_path = REG / "pair_features.csv"
    done = pd.read_csv(out_path) if out_path.exists() else pd.DataFrame(columns=["pair_id"])
    chips = REG / "chips"
    chips.mkdir(parents=True, exist_ok=True)
    res = [r for r in done.to_dict("records") if r["pair_id"] in set(acc["pair_id"])]
    for _, p in acc.iterrows():
        if p["pair_id"] in set(done["pair_id"]):
            continue
        fp = wkt.loads(p["footprint_wkt"])
        lon0, lat0, lon1, lat1 = fp.bounds
        half = cfg["chip_min_km"] / 2 / 111.32
        cx, cy = (lon0 + lon1) / 2, (lat0 + lat1) / 2
        k = np.cos(np.radians(cy))
        bbox = (min(lon0, cx - half / k), min(lat0, cy - half), max(lon1, cx + half / k), max(lat1, cy + half))
        grid = bbox_grid(bbox)
        item = _signed_item(p["collection"], p["scene_id"])
        refl, scl = load_scene(Scene([item], 1.0, 0.0), grid)
        # Маска воды по одной сцене: SCL = вода и глобальная маска океана
        rows_, cols_ = np.mgrid[0:grid.height, 0:grid.width]
        xs = grid.transform.c + (cols_ + 0.5) * grid.transform.a
        ys = grid.transform.f + (rows_ + 0.5) * grid.transform.e
        lon, lat = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True).transform(xs, ys)
        water = (scl == 6) & globe.is_ocean(lat, lon)
        valid = water & np.isfinite(refl[1]) & ~np.isin(scl, list(BAD_SCL))
        r = detect_array(refl, valid)
        g = transform_geom("EPSG:4326", grid.crs.to_wkt(), mapping(fp))
        inside = ~geometry_mask([g], out_shape=valid.shape, transform=grid.transform)
        v = valid & inside
        det = (r["P"] >= P_DET) & (r["flags"] == 7) & v
        lab, n = label(det, structure=np.ones((3, 3)))
        rec = {"pair_id": p["pair_id"], "event_id": p["event_id"], "scene_id": p["scene_id"],
               "valid_px": int(v.sum()), "det_px": int(det.sum()), "det_frac": float(det.sum() / max(v.sum(), 1)),
               "det_per_km2": float(det.sum() / max(v.sum() * 1e-4, 1e-9)), "n_zones": int(n),
               "p95": float(np.percentile(r["P"][v], 95)) if v.any() else None,
               "fdi_anom_mean": float(r["d_fdi"][v].mean()) if v.any() else None,
               "cover_m2_km2": float((r["frac"] * 100 * det).sum() / max(v.sum() * 1e-4, 1e-9))}
        res.append(rec)
        # Быстрый просмотр: снимок, след события и детекции
        rgb = np.nan_to_num(refl[[3, 2, 1]]).transpose(1, 2, 0)
        rgb = (np.clip(rgb / 0.22, 0, 1) ** (1 / 1.4) * 255).astype(np.uint8)
        edge = inside & ~np.roll(inside, 1, 0) | inside & ~np.roll(inside, 1, 1)
        rgb[edge] = [0, 255, 255]
        rgb[det] = [255, 40, 40]
        Image.fromarray(rgb).save(chips / f"{p['pair_id'].replace(':', '_')}.jpg", quality=85)
        pd.DataFrame(res).to_csv(out_path, index=False)
        print(f"  {p['event_id']} {p['scene_id'][:38]} valid={rec['valid_px']} det={rec['det_px']}", flush=True)
    feats = pd.DataFrame(res)
    feats.to_csv(out_path, index=False)
    return feats


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        reg = build(offline="--offline" in sys.argv)
        print(json.dumps({k: v for k, v in json.loads((REG / "summary.json").read_text(encoding="utf-8")).items()
                          if k != "meta"}, ensure_ascii=False, indent=1))
    elif cmd == "detect":
        print(detect_pairs().to_string())
    else:
        raise SystemExit(f"неизвестная команда {cmd}")
