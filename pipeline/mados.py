"""MADOS (Marine Debris and Oil Spill): дополнительные размеченные сцены Sentinel-2 для детектора.

python -m pipeline.mados            # сводка: сцены, дубликаты MARIDA, пиксели по классам в новых сценах

Архив data/raw/MADOS.zip (Zenodo 10.5281/zenodo.10664073, ~4 ГБ) читается без распаковки. MADOS включает все
63 сцены MARIDA с теми же пикселями и тем же разбиением, поэтому для проверки детектора (eval_detector_mados) и
эксперимента с обучением (train --with-mados) берутся только новые сцены: дубликаты находятся по совпадению числа
размеченных пикселей каждого класса (duplicates). В эксперименте нефть — «вода», то есть «не мусор». Модель сервиса
обучена только на MARIDA; почему — docs/mados.md.
"""
from __future__ import annotations

import zipfile
from functools import lru_cache

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from scipy.ndimage import zoom

from .config import MADOS_CLASSES, MADOS_OIL, MADOS_TO_GROUP, MARIDA_TO_MADOS, RAW

ZIP = RAW / "MADOS.zip"
URL = "https://zenodo.org/records/10664073/files/MADOS.zip?download=1"
SIZE = 240
# Каналы в порядке config.BANDS: папка разрешения и длина волны в имени файла (у S2A и S2B центры чуть разные)
BAND_FILES = [("60", (443, 442)), ("10", (492,)), ("10", (560, 559)), ("10", (665,)), ("20", (704,)),
              ("20", (740, 739)), ("20", (783, 780)), ("10", (833,)), ("20", (865, 864)), ("20", (1614, 1610)),
              ("20", (2202, 2186))]


def available() -> bool:
    return ZIP.exists()


@lru_cache(maxsize=1)
def _zip() -> tuple[zipfile.ZipFile, frozenset[str]]:
    z = zipfile.ZipFile(ZIP)
    return z, frozenset(z.namelist())


def _read(name: str) -> np.ndarray:
    with MemoryFile(_zip()[0].read(name)) as mf, mf.open() as r:
        return r.read(1)


def split_ids(split: str) -> list[str]:
    """Патчи официального разбиения MADOS: 'Scene_12_3' — сцена 12, фрагмент 3."""
    return _zip()[0].read(f"MADOS/splits/{split}_X.txt").decode().split()


def scene_of(pid: str) -> int:
    return int(pid.split("_")[1])


def load_patch(pid: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Отражение [11, 240, 240] (каналы 20 и 60 м — билинейно на сетку 10 м), классы и уверенность разметки."""
    scene, n = pid.rsplit("_", 1)
    names = _zip()[1]
    bands = []
    for res, wls in BAND_FILES:
        name = next(f for w in wls if (f := f"MADOS/{scene}/{res}/{scene}_L2R_rhorc_{w}_{n}.tif") in names)
        a = _read(name).astype(np.float32)
        if a.shape[0] != SIZE:
            a = zoom(a, SIZE / a.shape[0], order=1, mode="nearest", grid_mode=True)
        bands.append(a)
    cl = _read(f"MADOS/{scene}/10/{scene}_L2R_cl_{n}.tif").astype(np.int16)
    conf = _read(f"MADOS/{scene}/10/{scene}_L2R_conf_{n}.tif").astype(np.int16)
    return np.stack(bands), cl, conf


@lru_cache(maxsize=1)
def scene_splits() -> dict[int, str]:
    out = {}
    for split in ("train", "val", "test"):
        for pid in split_ids(split):
            out[scene_of(pid)] = split
    return out


def _mados_counts() -> dict[int, np.ndarray]:
    out: dict[int, np.ndarray] = {}
    for pid in [p for s in ("train", "val", "test") for p in split_ids(s)]:
        scene, n = pid.rsplit("_", 1)
        cl = _read(f"MADOS/{scene}/10/{scene}_L2R_cl_{n}.tif").astype(np.int64)
        c = out.setdefault(scene_of(pid), np.zeros(16, np.int64))
        c += np.bincount(cl.ravel(), minlength=16)[:16]
        c[0] = 0  # неразмеченные пиксели не сравниваем
    return out


def _marida_counts() -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for f in sorted((RAW / "marida" / "patches").glob("*/*_cl.tif")):
        with rasterio.open(f) as s:
            cl = s.read(1).astype(np.int64)
        raw = np.bincount(cl.ravel(), minlength=16)[:16]
        c = out.setdefault(f.parent.name.removeprefix("S2_"), np.zeros(16, np.int64))
        for a, b in MARIDA_TO_MADOS.items():
            c[b] += raw[a]
    return out


@lru_cache(maxsize=1)
def duplicates() -> dict[int, str]:
    """Сцены MADOS, которые повторяют сцену MARIDA: {номер сцены MADOS: сцена MARIDA}.

    Совпадением считается расхождение числа размеченных пикселей по классам не больше 5% (на практике —
    точное совпадение числа пикселей мусора у всех 63 сцен). Нужна распакованная MARIDA (data/raw/marida).
    """
    mados, marida = _mados_counts(), _marida_counts()
    out = {}
    for s, c in mados.items():
        best = min(marida, key=lambda k: np.abs(c - marida[k]).sum())
        if np.abs(c - marida[best]).sum() <= 0.05 * marida[best].sum() and c[1] == marida[best][1]:
            out[s] = best
    return out


def new_ids(split: str) -> list[str]:
    """Патчи сцен MADOS, которых нет в MARIDA."""
    dup = duplicates()
    return [p for p in split_ids(split) if scene_of(p) not in dup]


def training_pixels(split: str) -> tuple[np.ndarray, np.ndarray, dict]:
    """Признаки и группы размеченных пикселей новых сцен MADOS и сводка отбора."""
    from .features import features

    X, y = [], []
    stats = {"patches": 0, "scenes": set(), "oil_px": 0}
    for pid in new_ids(split):
        x, cl, _ = load_patch(pid)
        f, _ = features(x)
        m = (cl > 0) & np.isfinite(x).all(0)
        if not m.any():
            continue
        stats["oil_px"] += int((m & (cl == MADOS_OIL)).sum())
        X.append(f[:, m].T)
        y.append(np.vectorize(MADOS_TO_GROUP.get, otypes=[np.int64])(cl[m]))
        stats["patches"] += 1
        stats["scenes"].add(scene_of(pid))
    stats["scenes"] = sorted(stats["scenes"])
    return np.concatenate(X), np.concatenate(y), stats


def main() -> None:
    dup = duplicates()
    sp = scene_splits()
    print(f"сцен MADOS: {len(sp)}, из них повторяют MARIDA: {len(dup)}")
    for split in ("train", "val", "test"):
        new = sorted({scene_of(p) for p in new_ids(split)})
        print(f"  {split}: новых сцен {len(new)}, патчей {len(new_ids(split))}")
    mados = _mados_counts()
    new = sum(c for s, c in mados.items() if s not in dup)
    print("размеченные пиксели в новых сценах:")
    for k, name in MADOS_CLASSES.items():
        print(f"  {name:26s} {int(new[k]):8d}")


if __name__ == "__main__":
    main()
