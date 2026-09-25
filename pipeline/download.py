"""Скачивание сцен Sentinel-2 по всем акваториям.

python -m pipeline.download [aoi ...]
"""
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor

from .config import AOIS, PROCESSED
from .s2 import aoi_grid, load_scene, save_scene, search

warnings.filterwarnings("ignore")

# Период и порог облачности по умолчанию; у акватории-события свои (period, max_cloud в config.AOIS)
START, END = "2025-04-01", "2026-09-20"
MAX_CLOUD = 10
MAX_SCENES = 30


def run(aoi_id: str) -> None:
    a = AOIS[aoi_id]
    start, end = a.get("period", (START, END))
    grid = aoi_grid(aoi_id)
    scenes = search(aoi_id, start, end, a.get("max_cloud", MAX_CLOUD))
    if len(scenes) > MAX_SCENES:
        scenes = sorted(scenes, key=lambda s: s.cloud)[:MAX_SCENES]
        scenes.sort(key=lambda s: s.datetime)
    todo = [s for s in scenes if not (PROCESSED / aoi_id / s.date / "meta.json").exists()]
    print(f"{aoi_id}: {len(scenes)} сцен, скачать {len(todo)}", flush=True)

    def one(scene):
        try:
            refl, scl = load_scene(scene, grid)
            save_scene(aoi_id, scene, grid, refl, scl)
            print(f"  {aoi_id} {scene.date} ok ({'+'.join(i.properties['s2:mgrs_tile'] for i in scene.items)})", flush=True)
        except Exception as e:  # сетевые сбои не должны валить всю загрузку
            print(f"  {aoi_id} {scene.date} FAIL {e}", flush=True)

    with ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(one, todo))


if __name__ == "__main__":
    for a in sys.argv[1:] or list(AOIS):
        run(a)
