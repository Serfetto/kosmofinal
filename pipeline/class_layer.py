"""Построение цветного слоя шести классов модели для опубликованного снимка.

python -m pipeline.class_layer [aoi ...]   — готовые PNG для всех снимков в data/web (из det.tif)

Слой — третий канал det.tif, раскрашенный палитрой. Готовые PNG лежат в data/web рядом с debris.png и отдаются
сразу. Если PNG для текущей версии модели нет, а det.tif в компактной поставке не хранится, первый запрос повторно
читает ровно эту сцену Sentinel-2 из Planetary Computer и прогоняет замороженный детектор: признаки считаются по
всему снимку (медиана фона по блокам), поэтому это десятки секунд на дату. Результат кешируется.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import planetary_computer as pc
import rasterio
from PIL import Image
from pystac_client import Client

from . import status as ST
from .config import AOIS, DATA, MODELS, PROCESSED
from .detect import BAD_SCL, detect_array, load_water
from .s2 import STAC_URL, Scene, aoi_grid, load_scene

WEB = DATA / "web"


def model_sha() -> str:
    """SHA модели для инвалидирования кеша после замены xgb.joblib."""
    manifest = MODELS / "MANIFEST.json"
    if manifest.exists():
        files = json.loads(manifest.read_text(encoding="utf-8")).get("files", {})
        item = files.get("data/models/xgb.joblib")
        if item and item.get("sha256"):
            return str(item["sha256"])
    return hashlib.sha256((MODELS / "xgb.joblib").read_bytes()).hexdigest()


def cache_filename() -> str:
    return f"model_classes_{model_sha()[:12]}.png"


def model_classes_png(groups: np.ndarray, path: Path) -> None:
    """Цветная маска групп детектора; 255/невалидные пиксели остаются прозрачными.

    PNG с палитрой и альфой на каждый цвет: втрое меньше RGBA и на порядок быстрее кодируется."""
    palette = np.zeros((256, 3), np.uint8)
    alpha = np.zeros(256, np.uint8)
    for code, (_, _, rgba) in ST.MODEL_CLASSES.items():
        palette[code], alpha[code] = rgba[:3], rgba[3]
    img = Image.fromarray(np.ascontiguousarray(groups, np.uint8))
    img.putpalette(palette.tobytes())
    img.save(path, transparency=alpha.tobytes(), optimize=True)


def _scene_from_meta(aoi_id: str, date: str) -> Scene:
    meta_path = PROCESSED / aoi_id / date / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    ids = meta.get("items") or [meta["id"]]
    client = Client.open(STAC_URL, modifier=pc.sign_inplace)
    collection = client.get_collection("sentinel-2-l2a")
    items = [collection.get_item(item_id) for item_id in ids]
    missing = [item_id for item_id, item in zip(ids, items) if item is None]
    if missing:
        raise RuntimeError(f"Planetary Computer не нашёл сцену: {', '.join(missing)}")
    return Scene(items, cover=1.0, cloud=float(meta.get("cloud_cover") or 0))


def build(aoi_id: str, date: str, output: Path) -> Path:
    """Создать цветной слой классов атомарно и вернуть его путь."""
    det = PROCESSED / aoi_id / date / "det.tif"
    if det.exists():
        with rasterio.open(det) as src:
            groups = src.read(3)
    else:
        scene = _scene_from_meta(aoi_id, date)
        refl, scl = load_scene(scene, aoi_grid(aoi_id))
        water = load_water(aoi_id)
        valid = water & np.isfinite(refl[1]) & ~np.isin(scl, list(BAD_SCL))
        groups = detect_array(refl, valid)["G"]

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp.png")
    model_classes_png(groups, temporary)
    temporary.replace(output)
    return output


def run(aoi_id: str) -> None:
    """PNG текущей версии модели для всех опубликованных снимков акватории; слои прежних версий удаляются."""
    name = cache_filename()
    days = sorted(p for p in (WEB / aoi_id).iterdir() if (p / "zones.geojson").exists())
    built = skipped = 0
    for day in days:
        for old in day.glob("model_classes_*.png"):
            if old.name != name:
                old.unlink()
        if (day / name).exists():
            continue
        if not (PROCESSED / aoi_id / day.name / "det.tif").exists():
            skipped += 1  # без det.tif слой построит сервис при первом запросе
            continue
        build(aoi_id, day.name, day / name)
        built += 1
    print(f"{aoi_id}: построено {built}, без det.tif {skipped}, всего снимков {len(days)}", flush=True)


if __name__ == "__main__":
    for a in sys.argv[1:] or [a for a in AOIS if (WEB / a).is_dir()]:
        run(a)
