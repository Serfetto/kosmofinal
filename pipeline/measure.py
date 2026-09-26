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


def pooled_concentration(n_items, areas_km2) -> float:
    """Концентрация по нескольким участкам (сегменты прерванной трансекты, полосы в одном гексе): ΣN / ΣA.

    Не среднее концентраций участков: короткий сегмент с одним предметом не должен весить как вся полоса.
    """
    n, a = np.asarray(n_items, float), np.asarray(areas_km2, float)
    return field_concentration(float(n.sum()), float(a.sum()))


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


def mae_log(y, p, eps: float = 1.0) -> float:
    """Средняя ошибка в лог-шкале |ln(Ĉ + eps) − ln(C + eps)|; e^mae_log — типичная ошибка «во сколько раз».

    Дополняет MAE: концентрации различаются в десятки раз, и MAE определяют несколько самых загрязнённых полос.
    """
    y, p = np.asarray(y, float), np.asarray(p, float)
    return float(np.mean(np.abs(np.log(p + eps) - np.log(y + eps))))


def group_bootstrap_mae(y, preds: dict, groups, ref: str, n_boot: int = 2000, seed: int = 0,
                        level: float = 0.95) -> dict:
    """Интервалы MAE по групповому бутстрепу: группы зависимых событий (один день рейса, соседство, общая сцена)
    выбираются с возвращением целиком — как в разбиении.

    Возвращает ДИ MAE каждой модели и парную разность MAE ref − модель на тех же выборках: < 0 — ref точнее;
    ДИ разности, накрывающий 0, — различие в пределах шума.
    """
    y = np.asarray(y, float)
    _, gi = np.unique(np.asarray(groups).astype(str), return_inverse=True)
    k = int(gi.max()) + 1
    cnt = np.random.default_rng(seed).multinomial(k, np.full(k, 1 / k), size=n_boot)
    size = cnt @ np.bincount(gi, minlength=k)
    boot = {m: cnt @ np.bincount(gi, np.abs(y - np.asarray(p, float)), minlength=k) / size for m, p in preds.items()}
    q = [50 * (1 - level), 50 * (1 + level)]
    out = {"level": level, "n_boot": n_boot, "seed": seed, "n_groups": k,
           "mae_ci": {m: np.percentile(b, q).tolist() for m, b in boot.items()}, "delta": {}}
    for m in preds:
        if m != ref:
            d = boot[ref] - boot[m]
            out["delta"][m] = {"value": mae(y, preds[ref]) - mae(y, preds[m]), "ci": np.percentile(d, q).tolist(),
                               "p_ref_better": float(np.mean(d < 0))}
    return out
