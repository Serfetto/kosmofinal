"""Контракт полевого реестра и правила отбора."""
import numpy as np
import pandas as pd
import pytest

from pipeline import field


@pytest.fixture(scope="module")
def raw():
    return field.load_raw()


@pytest.fixture(scope="module")
def sel(raw):
    return field.select(raw, field.config())


def test_schema(raw):
    assert raw.shape == (935, 56)
    assert raw["event_id"].nunique() == 318
    assert raw["sample_id"].is_unique


def test_empty_is_not_zero(raw):
    # У S4 нет числителя и площади — это NaN, а не 0
    s4 = raw[raw["source_id"] == "S4_BLACK_SEA_DOORS3"]
    assert s4["density_numerator_items"].isna().all()
    assert s4["sampled_area_km2"].isna().all()


def test_every_row_has_decision(sel):
    assert len(sel) == 935
    exc = sel[sel["decision"] == "excluded"]
    assert (exc["reason_code"] != "").all()
    counts = sel[sel["decision"] == "included"].groupby(["profile", "role"]).size().to_dict()
    assert counts == {("A", "train"): 63, ("B", "train"): 33, ("B", "transfer_check"): 41, ("C", "optional"): 83}


def test_scopes_not_mixed(sel):
    inc = sel[sel["decision"] == "included"]
    for pid, g in inc.groupby("profile"):
        assert g["target_scope"].nunique() == 1, f"профиль {pid} смешивает совокупности"
    assert set(inc[inc["profile"] == "A"]["target_scope"]) == {"total_plastic"}
    assert set(inc[inc["profile"] == "B"]["target_scope"]) == {"all_litter"}
    assert not (inc["target_scope"] == "object_context").any()


def test_midnight_crossing():
    s, e, known = field.event_interval("2015-07-27", "20:54:00", "00:28:00")
    assert known and (e - s).total_seconds() == pytest.approx(3.5667 * 3600, rel=1e-3)


def test_date_without_time_is_whole_day():
    s, e, known = field.event_interval("2024-06-05", "", "")
    assert not known and (e - s).days == 1


def test_events_n_over_a(raw, sel):
    ev, _ = field.build_events(raw, sel, field.config())
    a = ev[ev["profile"] == "A"]
    assert len(a) == 63 and (a["conc_source"] == "n_over_a").all()
    np.testing.assert_allclose(a["conc_items_km2"], a["n_items"] / a["area_km2"])
    # Прерванные трансекты S2 восстановлены сегментами из PANGAEA 931834
    seg = ev[ev["geometry_type"] == "segments"]["event_id"].tolist()
    assert sorted(seg) == sorted(f"S2:MSM41_litter-T{n}" for n in (18, 22, 35, 49))
    b = ev[(ev["profile"] == "B") & (ev["role"] == "train")]
    assert (b["conc_source"] == "published").all() and b["conc_lo95"].isna().all()


def test_interrupted_transect_area_is_sum_of_segments():
    # Площадь прерванной трансекты S2 — сумма площадей сегментов PANGAEA 931834 (Σ Lᵢ × 10 м). В реестре стоит
    # сводная площадь автора, округлённая до 0,01 км² (флаг summary_area_rounded_differs_from_segment_sum):
    # C = N / A сдвигается не больше чем на 3,5% (T18) и остаётся глубоко внутри 95% ДИ счёта
    from pipeline.measure import pooled_concentration, strip_area_km2

    ev = field.load_events("A").set_index("event_id")
    segs = field.s2_segments(field.config())
    assert len(segs) == 4
    for eid, ss in segs.items():
        e = ev.loc[eid]
        areas = [strip_area_km2(s["length_km"], e["width_m"]) for s in ss]
        assert sum(s["length_km"] for s in ss) == pytest.approx(e["length_km"], abs=1e-9)
        assert abs(sum(areas) - e["area_km2"]) < 0.01
        c = pooled_concentration([e["n_items"]], areas)
        assert abs(c / e["conc_items_km2"] - 1) < 0.035
        assert e["conc_lo95"] < c < e["conc_hi95"]
        assert "summary_area_rounded_differs_from_segment_sum" in e["quality_flags"] or sum(areas) == e["area_km2"]


def test_published_matches_n_over_a_within_rounding(raw):
    td = raw[raw["density_numerator_items"].notna() & raw["sampled_area_km2"].notna()
             & raw["concentration_items_km2"].gt(0)]
    rel = (td["density_numerator_items"] / td["sampled_area_km2"] - td["concentration_items_km2"]).abs() \
        / td["concentration_items_km2"]
    assert rel.max() < 0.01
