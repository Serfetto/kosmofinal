"""Спектральные индексы и контекстные признаки пикселя.

Одинаково считаются для патчей MARIDA (обучение) и для сцен акваторий (инференс).

Ключевой шаг — нормализация фона: из каждого пикселя вычитается локальный спектр
окружающей воды (медиана по блокам ~320 м) и прибавляется эталонный спектр чистой
воды MARIDA. Это убирает солнечный блик, дымку и разницу атмосферной коррекции
(Sen2Cor L2A против ACOLITE в MARIDA): модель видит только аномалию относительно воды.
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import distance_transform_edt, uniform_filter, zoom

from .config import BAND_IDX, WAVELENGTH

EPS = 1e-6
# Медианный спектр класса Marine Water в MARIDA
WATER_REF = np.array([0.0368, 0.0342, 0.0268, 0.0175, 0.0142, 0.0140, 0.0148, 0.0121, 0.0140, 0.0086, 0.0062],
                     dtype=np.float32)
BLOCK = 32


def _b(x: np.ndarray, name: str) -> np.ndarray:
    return x[BAND_IDX[name]]


def fdi(x: np.ndarray) -> np.ndarray:
    """Floating Debris Index (Biermann et al., 2020)."""
    r6, r8, r11 = _b(x, "B06"), _b(x, "B08"), _b(x, "B11")
    k = (WAVELENGTH["B08"] - WAVELENGTH["B04"]) / (WAVELENGTH["B11"] - WAVELENGTH["B04"]) * 10
    return r8 - (r6 + (r11 - r6) * k)


def fai(x: np.ndarray) -> np.ndarray:
    """Floating Algae Index (Hu, 2009)."""
    r4, r8, r11 = _b(x, "B04"), _b(x, "B08"), _b(x, "B11")
    k = (WAVELENGTH["B08"] - WAVELENGTH["B04"]) / (WAVELENGTH["B11"] - WAVELENGTH["B04"])
    return r8 - (r4 + (r11 - r4) * k)


def nd(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return (a - b) / (a + b + EPS)


def indices(x: np.ndarray) -> dict[str, np.ndarray]:
    b3, b4, b8, b11 = _b(x, "B03"), _b(x, "B04"), _b(x, "B08"), _b(x, "B11")
    return {
        "NDVI": nd(b8, b4),
        "FDI": fdi(x),
        "FAI": fai(x),
        "NDWI": nd(b3, b8),
        "PI": b8 / (b8 + b4 + EPS),
        "NDMI": nd(b8, b11),
    }


def water_candidates(x: np.ndarray, robust: bool = False) -> np.ndarray:
    """Пиксели для оценки фона.

    strict — только чистая вода (так обучена модель на MARIDA);
    robust — всё, кроме суши и облаков: мутность и цветение уходят в фон (для внутренних вод).
    """
    b2 = _b(x, "B02")
    if robust:
        return (b2 > 0) & (b2 < 0.25) & (_b(x, "B11") < 0.08)
    ndwi = nd(_b(x, "B03"), _b(x, "B08"))
    return (ndwi > 0.0) & (fdi(x) < 0.01) & (b2 < 0.25) & (b2 > 0)


def water_background(x: np.ndarray, block: int = BLOCK, robust: bool = False) -> np.ndarray:
    """Локальный спектр воды [11,H,W]: медиана по блокам в два прохода.

    Второй проход исключает пиксели ярче первого фона в NIR — пятна мусора
    не попадают в фон, а протяжённые мутность и цветение (в режиме robust) попадают.
    """
    cand = water_candidates(x, robust)
    bg1 = _block_median(x, cand, block)
    b8 = BAND_IDX["B08"]
    calm = cand & (x[b8] - bg1[b8] < 0.006) & (fdi(x) - fdi(bg1) < 0.004)
    return _block_median(x, calm, block)


def _block_median(x: np.ndarray, mask: np.ndarray, block: int) -> np.ndarray:
    """Медиана по блокам block×block, пустые блоки — от ближайших, сглаживание и растяжение до пикселей."""
    c, h, w = x.shape
    hb, wb = -(-h // block), -(-w // block)
    pad = np.full((c, hb * block, wb * block), np.nan, np.float32)
    pad[:, :h, :w] = np.where(mask, x, np.nan)
    blocks = pad.reshape(c, hb, block, wb, block).transpose(0, 1, 3, 2, 4).reshape(c, hb, wb, -1)
    n_ok = np.isfinite(blocks[0]).sum(-1)
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(blocks, axis=-1)
    good = n_ok >= block * block // 8
    if not good.any():
        return np.broadcast_to(WATER_REF[:, None, None], x.shape).copy()
    _, (iy, ix) = distance_transform_edt(~good, return_indices=True)
    med = med[:, iy, ix]
    # Сглаживание 3×3 блока и билинейное растягивание до пикселей
    med = np.stack([uniform_filter(m, 3, mode="nearest") for m in med])
    up = zoom(med, (1, block, block), order=1, mode="nearest", grid_mode=True)
    return up[:, :h, :w].astype(np.float32)


def normalize(x: np.ndarray, robust: bool = False) -> tuple[np.ndarray, np.ndarray]:
    x = np.nan_to_num(x, nan=0.0).astype(np.float32)
    bg = water_background(x, robust=robust)
    return x - bg + WATER_REF[:, None, None], bg


def features(x: np.ndarray, robust: bool = False) -> tuple[np.ndarray, list[str]]:
    """x: [11,H,W] отражение. Возвращает [F,H,W] и имена признаков."""
    xn, bg = normalize(x, robust)
    idx = indices(xn)
    feats, names = [xn[i] for i in range(xn.shape[0])], list(BAND_IDX)
    for k, v in idx.items():
        feats.append(v)
        names.append(k)
    f = idx["FDI"]
    feats.append(uniform_filter(f, 3, mode="nearest"))
    names.append("FDI_mean3")
    feats.append(np.sqrt(np.maximum(uniform_filter(f * f, 5, mode="nearest") - uniform_filter(f, 5, mode="nearest") ** 2, 0)))
    names.append("FDI_std5")
    b8 = _b(xn, "B08")
    feats.append(b8 - uniform_filter(b8, 5, mode="nearest"))
    names.append("B08_hp5")
    return np.stack(feats).astype(np.float32), names
