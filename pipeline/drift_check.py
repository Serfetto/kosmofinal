"""Проверка модели дрейфа (дополнительная функция) на парах соседних снимков.

python -m pipeline.drift_check [aoi ...]     # → data/eval/drift_check.json

Частицы засеваются детекциями снимка d1 и переносятся до момента снимка d2 (≤ 2 сут.). Для каждой детекции
на d2 считаем расстояние до ближайшей частицы прогноза и до ближайшей детекции d1 («пятно стоит на месте» —
базовый прогноз). Детекции d2 в прогнозе не участвуют.

Оговорка: течения и ветер берутся из архива анализа/реанализа на даты между снимками (как в drift.py),
то есть это проверка физической модели переноса при известном ветре, а не оперативного прогноза. Для
оценки оперативного прогноза нужен архив прогнозов, выпущенных на момент d1.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date

import numpy as np
from scipy.spatial import cKDTree

from .aggregate import WEB
from .config import AOIS, DATA
from .drift import scene_time, simulate
from .provenance import run_meta, write_json

MAX_GAP_DAYS = 2
MIN_DET = 5


def _xy(lon, lat, lat0):
    return np.c_[np.asarray(lon) * 111.32 * np.cos(np.radians(lat0)), np.asarray(lat) * 111.32]


def pairs_for(aoi: str) -> list[tuple[str, str]]:
    s = json.loads((WEB / aoi / "series.json").read_text(encoding="utf-8"))
    ok = [(d, sc) for d, sc in zip(s["dates"], s["scenes"]) if not sc["storm"] and sc["n_det"] >= MIN_DET]
    out = []
    for (d1, _), (d2, _) in zip(ok[:-1], ok[1:]):
        if 0 < (date.fromisoformat(d2) - date.fromisoformat(d1)).days <= MAX_GAP_DAYS:
            out.append((d1, d2))
    return out


def check(aoi: str, d1: str, d2: str) -> dict:
    p1 = np.asarray(json.loads((WEB / aoi / d1 / "points.json").read_text()), float).reshape(-1, 4)
    p2 = np.asarray(json.loads((WEB / aoi / d2 / "points.json").read_text()), float).reshape(-1, 4)
    hours = int(round((scene_time(aoi, d2) - scene_time(aoi, d1)).total_seconds() / 3600))
    res = simulate(aoi, d1, p1[:, 0], p1[:, 1], hours=hours, n_ens=4, seed=3)
    end = res["track"][:, hours, :]
    lat0 = float(np.mean(p2[:, 1]))
    obs = _xy(p2[:, 0], p2[:, 1], lat0)
    d_fc = cKDTree(_xy(end[:, 0], end[:, 1], lat0)).query(obs)[0]
    d_ps = cKDTree(_xy(p1[:, 0], p1[:, 1], lat0)).query(obs)[0]
    return {"aoi": aoi, "d1": d1, "d2": d2, "hours": hours, "n_seed": len(p1), "n_obs": len(p2),
            "median_km_forecast": float(np.median(d_fc)), "median_km_persistence": float(np.median(d_ps)),
            "within_1km_forecast": float(np.mean(d_fc <= 1)), "within_1km_persistence": float(np.mean(d_ps <= 1)),
            "beached_frac": float(res["beached"].mean())}


def main(aois: list[str]) -> dict:
    rows = []
    for a in aois:
        if not (WEB / a / "series.json").exists() or AOIS[a]["kind"] != "sea":
            continue
        for d1, d2 in pairs_for(a):
            r = None
            for attempt in range(3):
                try:
                    r = check(a, d1, d2)
                    break
                except Exception as e:  # сеть Open-Meteo (лимит запросов) или нет данных течений
                    print(f"  {a} {d1}→{d2}: попытка {attempt + 1} не удалась ({str(e)[:80]})", flush=True)
                    time.sleep(60)
            if r is None:
                continue
            rows.append(r)
            print(f"  {a} {d1}→{d2} ({r['hours']} ч): медиана до прогноза {r['median_km_forecast']:.2f} км, "
                  f"до исходного положения {r['median_km_persistence']:.2f} км", flush=True)
    summary = {}
    if rows:
        f = np.array([r["median_km_forecast"] for r in rows])
        p = np.array([r["median_km_persistence"] for r in rows])
        summary = {"n_pairs": len(rows), "median_km_forecast": float(np.median(f)),
                   "median_km_persistence": float(np.median(p)),
                   "pairs_forecast_better": int((f < p).sum())}
    out = {"pairs": rows, "summary": summary,
           "note": "течения и ветер — анализ/реанализ на даты между снимками; детекции d2 в прогнозе не участвуют",
           "meta": run_meta()}
    write_json(DATA / "eval" / "drift_check.json", out)
    return out


if __name__ == "__main__":
    r = main(sys.argv[1:] or list(AOIS))
    print(json.dumps(r["summary"], ensure_ascii=False))
