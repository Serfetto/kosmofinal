"""Формулы концентрации и метрики — единственное место, где они определены.

C = N / A: N — число предметов выбранной совокупности, A — обследованная площадь, км².
"""
from __future__ import annotations

import numpy as np
from scipy.stats import chi2


def strip_area_km2(length_km: float, width_m: float) -> float:
    """Площадь полосы учёта: длина, км × ширина, м → км²."""
    return length_km * width_m / 1000.0


def field_concentration(n: float, area_km2: float) -> float:
    """Концентрация полевого измерения, шт./км²."""
    if not area_km2 > 0:
        raise ValueError(f"площадь должна быть > 0, получено {area_km2}")
    if n < 0:
        raise ValueError(f"число предметов не может быть отрицательным: {n}")
    return n / area_km2


def poisson_ci(n: float, area_km2: float, level: float = 0.95) -> tuple[float, float]:
    """Точный (Гарвуд) интервал для концентрации при пуассоновском счёте N на площади A."""
    a = 1 - level
    lo = 0.0 if n == 0 else chi2.ppf(a / 2, 2 * n) / 2
    hi = chi2.ppf(1 - a / 2, 2 * n + 2) / 2
    return lo / area_km2, hi / area_km2


def mae(y, p) -> float:
    y, p = np.asarray(y, float), np.asarray(p, float)
    return float(np.mean(np.abs(y - p)))


def rmse(y, p) -> float:
    y, p = np.asarray(y, float), np.asarray(p, float)
    return float(np.sqrt(np.mean((y - p) ** 2)))


def medae(y, p) -> float:
    y, p = np.asarray(y, float), np.asarray(p, float)
    return float(np.median(np.abs(y - p)))
