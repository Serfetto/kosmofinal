"""Скачивание сцен Sentinel-2 по всем акваториям.

python -m pipeline.download [aoi ...]
"""
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor

from .config import AOIS, PROCESSED
from .s2 import aoi_grid, load_scene, save_scene, search

warnings.filterwarnings("ignore")

START, END = "2025-04-01", "2026-09-20"
MAX_CLOUD = 10
MAX_SCENES = 30


def run(aoi_id: str) -> None:
    grid = aoi_grid(aoi_id)
    items = search(aoi_id, START, END, MAX_CLOUD)
    if len(items) > MAX_SCENES:
        items = sorted(items, key=lambda i: i.properties["eo:cloud_cover"])[:MAX_SCENES]
        items.sort(key=lambda i: i.datetime)
    todo = [i for i in items if not (PROCESSED / aoi_id / i.datetime.strftime("%Y-%m-%d") / "meta.json").exists()]
    print(f"{aoi_id}: {len(items)} сцен, скачать {len(todo)}", flush=True)

    def one(item):
        try:
            refl, scl = load_scene(item, grid)
            save_scene(aoi_id, item, grid, refl, scl)
            print(f"  {aoi_id} {item.datetime:%Y-%m-%d} ok", flush=True)
        except Exception as e:  # сетевые сбои не должны валить всю загрузку
            print(f"  {aoi_id} {item.datetime:%Y-%m-%d} FAIL {e}", flush=True)

    with ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(one, todo))


if __name__ == "__main__":
    for a in sys.argv[1:] or list(AOIS):
        run(a)
