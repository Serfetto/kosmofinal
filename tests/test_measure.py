"""Контрольные примеры формул концентрации (постановка кейса, раздел «Оценка концентрации»)."""
import math

import pytest

from pipeline.measure import field_concentration, mae, poisson_ci, rmse, strip_area_km2


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
