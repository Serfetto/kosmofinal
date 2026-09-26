"""Контрольные примеры формул концентрации (постановка кейса, раздел «Оценка концентрации»)."""
import math

import pytest

import numpy as np

from pipeline.measure import (field_concentration, group_bootstrap_mae, mae, mae_log, medae, poisson_ci,
                              pooled_concentration, rmse, strip_area_km2)


def test_case_example_items_per_km2():
    # 12 предметов на площади 0,20 км² → 60 шт./км²
    assert field_concentration(12, 0.20) == pytest.approx(60.0)


def test_case_example_absolute_error():
    # Модель дала 75 шт./км² на той же площади → абсолютная ошибка 15
    assert mae([60.0], [75.0]) == pytest.approx(15.0)


def test_strip_area_units():
    # 19,68 км × 10 м = 0,1968 км² (S2, трансекта T1)
    assert strip_area_km2(19.68, 10) == pytest.approx(0.1968)


def test_real_rows():
    assert field_concentration(4, 0.1968) == pytest.approx(20.325203252, rel=1e-9)   # S2 T1
    assert field_concentration(7, 0.24465) == pytest.approx(28.6123032904, rel=1e-9)  # S2 T4


def test_mask_share_is_not_concentration():
    # Доля пикселей маски — безразмерная величина; функция концентрации требует число предметов и площадь
    with pytest.raises(ValueError):
        field_concentration(5, 0.0)
    with pytest.raises(ValueError):
        field_concentration(-1, 1.0)


def test_poisson_ci_contains_estimate_and_zero_case():
    lo, hi = poisson_ci(4, 0.2)
    assert lo < 20 < hi
    assert lo == pytest.approx(5.45, abs=0.05) and hi == pytest.approx(51.2, abs=0.1)
    lo0, hi0 = poisson_ci(0, 0.2)
    assert lo0 == 0 and hi0 > 0


def test_rmse():
    assert rmse([0, 0], [3, 4]) == pytest.approx(math.sqrt(12.5))


def test_pooled_concentration_is_ratio_of_sums():
    # Два сегмента полосы 10 м: 1 предмет на 2 км и 7 предметов на 18 км → 8 / 0,2 км² = 40 шт./км²,
    # а не среднее концентраций сегментов (50 и 38,9 → 44,4)
    areas = [strip_area_km2(2, 10), strip_area_km2(18, 10)]
    assert pooled_concentration([1, 7], areas) == pytest.approx(40.0)
    assert np.mean([field_concentration(1, areas[0]), field_concentration(7, areas[1])]) == pytest.approx(44.444, abs=1e-3)
    # Одна полоса — то же, что C = N / A
    assert pooled_concentration([12], [0.20]) == pytest.approx(60.0)


def test_mae_rmse_medae_same_sample():
    # Эталон 60, 20, 100; модель 75, 20, 40 → ошибки 15, 0, 60
    y, p = [60, 20, 100], [75, 20, 40]
    assert mae(y, p) == pytest.approx(25.0)
    assert rmse(y, p) == pytest.approx(math.sqrt((15 ** 2 + 60 ** 2) / 3))
    assert medae(y, p) == pytest.approx(15.0)


def test_mae_log_is_factor_error():
    # Модель ошиблась вдвое вверх и вдвое вниз (в шкале C + 1): средняя лог-ошибка ln 2
    assert mae_log([99, 99], [199, 49]) == pytest.approx(math.log(2))
    assert mae_log([60], [60]) == 0.0


def test_group_bootstrap_resamples_whole_groups():
    y = np.array([10.0, 10, 10, 10])
    p = {"model": np.array([12.0, 12, 30, 30]), "median": np.array([10.0, 10, 10, 10])}
    # Одна группа: ресэмплировать нечего — интервал вырождается в точку
    r = group_bootstrap_mae(y, p, ["g"] * 4, "model", n_boot=200)
    assert r["mae_ci"]["model"] == pytest.approx([11.0, 11.0])
    assert r["delta"]["median"]["value"] == pytest.approx(11.0) and r["delta"]["median"]["p_ref_better"] == 0
    # Две группы: MAE бутстрепа — только 2, 11 или 20 (группы целиком), не отдельные события
    r = group_bootstrap_mae(y, p, ["a", "a", "b", "b"], "model", n_boot=500, seed=1)
    lo, hi = r["mae_ci"]["model"]
    assert lo == pytest.approx(2.0) and hi == pytest.approx(20.0)
    # Детерминирован при том же seed — это позволяет pipeline.verify пересчитать интервалы
    assert group_bootstrap_mae(y, p, ["a", "a", "b", "b"], "model", n_boot=500, seed=1) == r
