"""Веб-сервис: API + статика фронтенда.

uvicorn backend.app:app --port 8000
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

import h3
import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.case_api import DATE_RE, router
from backend.report import router as report_router
from pipeline.aggregate import WEB
from pipeline.config import AOIS, H3_RES, ROOT
from pipeline.drift import accumulation, simulate
from pipeline.route import plan

app = FastAPI(title="AquaFlow – мониторинг океанического пластика")
FRONTEND_DIST = ROOT / "frontend" / "dist"


def _json(path):
    if not path.exists():
        raise HTTPException(404, f"нет данных: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _check(aoi: str, date: str | None = None):
    if aoi not in AOIS or not (WEB / aoi / "series.json").exists():
        raise HTTPException(404, f"акватория {aoi!r} не найдена")
    if date is not None:
        if not re.match(DATE_RE, date):
            raise HTTPException(422, "дата должна быть в формате YYYY-MM-DD")
        if not (WEB / aoi / date / "points.json").exists():
            raise HTTPException(404, f"нет обработанного снимка {aoi} на {date}")


@app.get("/api/aois")
def aois():
    out = []
    for k, a in AOIS.items():
        p = WEB / k / "series.json"
        if not p.exists():
            continue
        s = _json(p)
        conc = {}
        for c in sorted((WEB / k).glob("conc_*.json")):
            j = _json(c)
            conc[j["profile"]] = {"available": j["available"], "reason": j.get("reason")}
        out.append({"id": k, "name": a["name"], "bbox": a["bbox"], "kind": a["kind"], "port": a["port"], "tz": a["tz"],
                    "rivers": a["rivers"], "note": a.get("note"), "dates": s["dates"],
                    "basin": s.get("basin"), "water_type": s.get("water_type"), "concentration": conc,
                    "total_area": [sc["area_m2"] for sc in s["scenes"]]})
    return out


@app.get("/api/aois/{aoi}/hexes")
def hexes(aoi: str):
    _check(aoi)
    return FileResponse(WEB / aoi / "hexes.geojson", media_type="application/geo+json")


@app.get("/api/aois/{aoi}/series")
def series(aoi: str):
    _check(aoi)
    return FileResponse(WEB / aoi / "series.json", media_type="application/json")


@app.get("/api/aois/{aoi}/{date}/points")
def points(aoi: str, date: str):
    _check(aoi, date)
    return FileResponse(WEB / aoi / date / "points.json", media_type="application/json")


@lru_cache(maxsize=64)
def _drift_all(aoi: str, date: str, hours: int, max_seeds: int = 300, n_ens: int = 4):
    pts = np.asarray(_json(WEB / aoi / date / "points.json"), float).reshape(-1, 4)
    if len(pts) == 0:
        return {"frames": [], "hexes": {}, "n": 0}
    if len(pts) > max_seeds:
        pts = pts[np.argsort(-pts[:, 2] * pts[:, 3])[:max_seeds]]
    res = simulate(aoi, date, pts[:, 0], pts[:, 1], hours=hours, n_ens=n_ens, seed=1)
    tr = res["track"]
    weight = np.repeat(pts[:, 2] * pts[:, 3] * 100 / n_ens, n_ens)  # м² на частицу
    snap = {}
    for h in (24, 48, hours):
        if h > hours:
            continue
        cells = {}
        for (lon, lat), w in zip(tr[:, h], weight):
            c = h3.latlng_to_cell(float(lat), float(lon), H3_RES)
            cells[c] = cells.get(c, 0.0) + float(w)
        snap[str(h)] = cells
    return {
        "n": int(tr.shape[0]), "hours": hours, "t0": res["t0"],
        "frames": np.round(tr.astype(float), 5).transpose(1, 0, 2).tolist(),  # [hour][particle][lon,lat]
        "beached": res["beached"].astype(int).tolist(),
        "hexes": snap,
    }


@app.get("/api/aois/{aoi}/{date}/drift")
def drift_all(aoi: str, date: str, hours: int = Query(72, ge=6, le=90)):
    """Прогноз распространения всех детекций на дату (ансамбль частиц)."""
    _check(aoi, date)
    return _drift_all(aoi, date, hours)


@app.get("/api/aois/{aoi}/{date}/drift_point")
def drift_point(aoi: str, date: str, lon: float, lat: float, hours: int = Query(72, ge=6, le=90), n: int = 40):
    """Ансамбль из одной точки — конус неопределённости."""
    _check(aoi, date)
    res = simulate(aoi, date, [lon], [lat], hours=hours, n_ens=n, seed=2)
    tr = res["track"]
    center = tr.mean(0)
    spread = np.sqrt(((tr - center[None]) ** 2 * np.array([np.cos(np.radians(lat)) ** 2, 1.0])).sum(-1).mean(0)) * 111.32
    return {"tracks": np.round(tr.astype(float), 5).tolist(), "center": np.round(center.astype(float), 5).tolist(),
            "spread_km": np.round(spread, 2).tolist(), "beached_frac": float(res["beached"].mean()), "t0": res["t0"]}


@lru_cache(maxsize=32)
def _accum(aoi: str, date: str):
    cache = WEB / aoi / date / "accumulation.json"
    if cache.exists():
        return _json(cache)
    a = accumulation(aoi, date)
    start, end = {}, {}
    for lon, lat in a["start"]:
        c = h3.latlng_to_cell(float(lat), float(lon), H3_RES)
        start[c] = start.get(c, 0) + 1
    for lon, lat in a["end"]:
        c = h3.latlng_to_cell(float(lat), float(lon), H3_RES)
        end[c] = end.get(c, 0) + 1
    mean_start = np.mean(list(start.values()))
    factor = {c: round(n / mean_start, 3) for c, n in end.items()}
    out = {"factor": factor, "beached_frac": round(float(a["beached"].mean()), 3), "n": len(a["start"])}
    cache.write_text(json.dumps(out), encoding="utf-8")
    return out


@app.get("/api/aois/{aoi}/{date}/accumulation")
def accum(aoi: str, date: str):
    """Зоны вероятного скопления: куда собираются частицы, засеянные равномерно, за 72 ч."""
    _check(aoi, date)
    return _accum(aoi, date)


@app.get("/api/aois/{aoi}/{date}/route")
def route(aoi: str, date: str, n: int = Query(8, ge=1, le=20), speed: float = Query(12, gt=1, le=40),
          delay: float = Query(6, ge=0, le=48)):
    _check(aoi, date)
    return plan(aoi, date, n_stops=n, speed_kn=speed, delay_h=delay)


app.mount("/data", StaticFiles(directory=WEB), name="data")
app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True, check_dir=False), name="frontend")
