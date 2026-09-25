"""Планирование маршрута обследования с учётом дрейфа.

Судно выходит из порта через `delay_h` часов после пролёта спутника. К моменту прибытия
пятно уже сместилось — поэтому целевые точки берутся из прогноза дрейфа на ETA.
Выбор следующей точки — жадная эвристика «ценность / время» (задача ориентирования).
"""
from __future__ import annotations

import json
from datetime import timedelta

import h3
import numpy as np

from .aggregate import WEB
from .config import AOIS, H3_RES
from .drift import scene_time, simulate

KNOT = 1.852  # км/ч


def haversine_km(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def candidates(aoi_id: str, date: str, k: int = 25) -> list[dict]:
    """Скопления детекций по гексам: центр масс и эквивалентная площадь мусора."""
    pts = json.loads((WEB / aoi_id / date / "points.json").read_text(encoding="utf-8"))
    groups: dict[str, list] = {}
    for lon, lat, p, f in pts:
        groups.setdefault(h3.latlng_to_cell(lat, lon, H3_RES), []).append((lon, lat, p * f * 100))
    out = []
    for cell, g in groups.items():
        a = np.asarray(g)
        w = a[:, 2] + 1e-6
        out.append({"h3": cell, "lon": float((a[:, 0] * w).sum() / w.sum()), "lat": float((a[:, 1] * w).sum() / w.sum()),
                    "area_m2": float(a[:, 2].sum()), "n": len(g)})
    out.sort(key=lambda c: -c["area_m2"])
    return out[:k]


def plan(aoi_id: str, date: str, n_stops: int = 8, speed_kn: float = 12.0, delay_h: float = 6.0,
         survey_h: float = 0.25, shift_h: float = 12.0) -> dict:
    cands = candidates(aoi_id, date)
    port = AOIS[aoi_id]["port"]
    if not cands:
        return {"stops": [], "line": [list(port)], "note": "на эту дату детекций нет", "port": list(port),
                "total_km": 0.0, "duration_h": 0.0, "speed_kn": speed_kn, "delay_h": delay_h,
                "pass_time": scene_time(aoi_id, date).isoformat(timespec="minutes"), "covered_m2": 0.0, "total_m2": 0.0}
    horizon = int(np.ceil(delay_h + shift_h)) + 1
    sim = simulate(aoi_id, date, [c["lon"] for c in cands], [c["lat"] for c in cands], hours=horizon)
    tr = sim["track"]  # [k, horizon+1, 2]

    def pos_at(i: int, h: float):
        h = float(np.clip(h, 0, horizon))
        j = int(np.floor(h))
        j2 = min(j + 1, horizon)
        a = h - j
        return tr[i, j] * (1 - a) + tr[i, j2] * a

    speed = speed_kn * KNOT
    t = delay_h
    cur = np.array(port, float)
    left = set(range(len(cands)))
    stops, line, total_km = [], [cur.tolist()], 0.0
    while left and len(stops) < n_stops:
        best, best_val = None, -1.0
        for i in left:
            # Итерация неподвижной точки: где будет пятно, когда мы до него доплывём
            arr = t
            for _ in range(3):
                p = pos_at(i, arr)
                arr = t + haversine_km(cur[0], cur[1], p[0], p[1]) / speed
            val = cands[i]["area_m2"] / (arr - t + survey_h)
            if arr + survey_h - delay_h <= shift_h and val > best_val:
                best, best_val, best_arr, best_p = i, val, arr, p
        if best is None:
            break
        dist = haversine_km(cur[0], cur[1], best_p[0], best_p[1])
        total_km += dist
        c = cands[best]
        stops.append({
            "order": len(stops) + 1, "h3": c["h3"], "area_m2": round(c["area_m2"], 1), "n_pixels": c["n"],
            "observed": [round(c["lon"], 5), round(c["lat"], 5)],
            "predicted": [round(float(best_p[0]), 5), round(float(best_p[1]), 5)],
            "drift_km": round(float(haversine_km(c["lon"], c["lat"], best_p[0], best_p[1])), 2),
            "eta_h": round(float(best_arr), 2),
            "eta": (scene_time(aoi_id, date) + timedelta(hours=float(best_arr))).isoformat(timespec="minutes"),
            "leg_km": round(float(dist), 2),
        })
        cur = np.asarray(best_p, float)
        line.append(cur.tolist())
        t = best_arr + survey_h
        left.discard(best)
    back = haversine_km(cur[0], cur[1], port[0], port[1])
    line.append(list(port))
    return {
        "stops": stops, "line": line, "port": list(port),
        "total_km": round(float(total_km + back), 1), "duration_h": round(float(t - delay_h + back / speed), 2),
        "speed_kn": speed_kn, "delay_h": delay_h, "pass_time": scene_time(aoi_id, date).isoformat(timespec="minutes"),
        "covered_m2": round(sum(s["area_m2"] for s in stops), 1),
        "total_m2": round(sum(c["area_m2"] for c in cands), 1),
    }
