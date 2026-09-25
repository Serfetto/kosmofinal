"""Скачивание сцен Sentinel-2 по всем акваториям.

python -m pipeline.download [aoi ...]
"""
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from .config import AOIS, DATA, PROCESSED
from .s2 import MIN_COVER, aoi_grid, load_scene, save_scene, search

warnings.filterwarnings("ignore")

# Период и порог облачности по умолчанию; у акватории-события свои (period, max_cloud в config.AOIS)
START, END = "2025-04-01", "2026-09-20"
MAX_CLOUD = 10
MAX_SCENES = 30


def field_dates(aoi_id: str) -> list[str]:
    """Даты полевых событий внутри акватории ± окно реестра пар (pairs.yaml: search_window_days)."""
    import pandas as pd

    from .provenance import load_yaml

    lon0, lat0, lon1, lat1 = AOIS[aoi_id]["bbox"]
    ev = pd.read_csv(DATA / "field" / "events.csv")
    ev = ev[ev["lon"].between(lon0, lon1) & ev["lat"].between(lat0, lat1)]
    days = load_yaml("pairs.yaml")["search_window_days"]
    return sorted({(date.fromisoformat(d) + timedelta(days=k)).isoformat()
                   for d in ev["date_utc"] for k in range(-days, days + 1)})


def run(aoi_id: str) -> None:
    a = AOIS[aoi_id]
    start, end = a.get("period", (START, END))
    windows = [(start, end)]
    if a.get("field_window"):
        # Отдельный поиск на каждую серию подряд идущих дат: рейсы разных лет не тянут весь архив между ними
        want = field_dates(aoi_id)
        windows = []
        for d in want:
            if windows and date.fromisoformat(d) - date.fromisoformat(windows[-1][1]) == timedelta(days=1):
                windows[-1] = (windows[-1][0], d)
            else:
                windows.append((d, d))
    grid = aoi_grid(aoi_id)
    scenes = [s for w0, w1 in windows
              for s in search(aoi_id, w0, w1, a.get("max_cloud", MAX_CLOUD), a.get("min_cover", MIN_COVER))]
    if len(scenes) > MAX_SCENES:
        scenes = sorted(scenes, key=lambda s: s.cloud)[:MAX_SCENES]
        scenes.sort(key=lambda s: s.datetime)
    todo = [s for s in scenes if not (PROCESSED / aoi_id / s.date / "meta.json").exists()]
    print(f"{aoi_id}: {len(scenes)} сцен, скачать {len(todo)}", flush=True)
    # Параллельные чтения GDAL в одном процессе изредка зависают намертво (см. pairs.detect_pairs). У районов
    # полевых данных снимков 1–2, зато склеены из 4–7 тайлов — их читаем последовательно, с повтором при сбое
    seq = bool(a.get("field_window"))

    def one(scene):
        for attempt in range(3 if seq else 1):
            try:
                refl, scl = load_scene(scene, grid, workers=1 if seq else 6)
                save_scene(aoi_id, scene, grid, refl, scl)
                print(f"  {aoi_id} {scene.date} ok ({'+'.join(i.properties['s2:mgrs_tile'] for i in scene.items)})",
                      flush=True)
                return
            except Exception as e:  # сетевые сбои не должны валить всю загрузку
                print(f"  {aoi_id} {scene.date} FAIL {e}", flush=True)

    with ThreadPoolExecutor(max_workers=1 if seq else 3) as ex:
        list(ex.map(one, todo))


if __name__ == "__main__":
    for a in sys.argv[1:] or list(AOIS):
        run(a)
