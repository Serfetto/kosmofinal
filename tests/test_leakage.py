"""Отсутствие утечек: запрещённые предикторы, пересечения групп, порог детектора до проверки."""
import json

import pandas as pd
import pytest

from pipeline.config import DATA, MODELS
from pipeline.provenance import load_yaml

CFG = load_yaml("concentration.yaml")


def test_candidate_features_are_allowed():
    forbidden = set(CFG["forbidden_predictors"])
    for pid, p in CFG["profiles"].items():
        for feats in p["candidate_features"]:
            assert not set(feats) & forbidden, f"{pid}: {feats}"


def test_check_features_rejects_target():
    from pipeline.concentration import check_features

    with pytest.raises(ValueError):
        check_features(["wind24_ms", "conc_items_km2"])
    with pytest.raises(ValueError):
        check_features(["sea_state_beaufort"])


@pytest.mark.parametrize("profile", ["A", "B"])
def test_groups_do_not_cross_holdout(profile):
    s = pd.read_csv(DATA / "splits" / f"{profile}.csv")
    assert s["event_id"].is_unique
    g = s.groupby("group_id")["is_holdout"].nunique()
    assert (g == 1).all(), "группа попала и в обучение, и в отложенную выборку"
    folds = s[~s["is_holdout"]].groupby("group_id")["fold"].nunique()
    assert (folds == 1).all(), "группа разбита между фолдами CV"
    assert (s[s["is_holdout"]]["fold"] == -1).all()


@pytest.mark.parametrize("profile", ["A", "B"])
def test_same_day_events_share_group(profile):
    s = pd.read_csv(DATA / "splits" / f"{profile}.csv")
    per_day = s.groupby(["source_id", "date_utc"])["group_id"].nunique()
    assert (per_day == 1).all()


@pytest.mark.parametrize("profile", ["A", "B"])
def test_served_model_trained_without_holdout(profile):
    m = json.loads((MODELS / f"conc_{profile}.json").read_text(encoding="utf-8"))
    assert not set(m["trained_on"]) & set(m["holdout"])
    s = pd.read_csv(DATA / "splits" / f"{profile}.csv")
    assert set(m["holdout"]) == set(s[s["is_holdout"]]["event_id"])


def test_detector_threshold_fixed_before_test():
    m = json.loads((DATA / "eval" / "detector" / "metrics.json").read_text(encoding="utf-8"))
    assert m["thresholds"]["xgb"] == load_yaml("detector.yaml")["p_det"]
    # Разбиение MARIDA не пересекается по сценам
    from pipeline.config import RAW

    sp = RAW / "marida" / "splits"
    if not sp.exists():
        pytest.skip("MARIDA не скачана")
    scenes = {s: {p.rsplit("_", 1)[0] for p in (sp / f"{s}_X.txt").read_text().split()} for s in ("train", "val", "test")}
    assert not scenes["train"] & scenes["test"] and not scenes["val"] & scenes["test"]


def test_mados_is_external_frozen_test():
    m = json.loads((DATA / "eval" / "detector_mados" / "metrics.json").read_text(encoding="utf-8"))
    assert m["threshold"] == load_yaml("detector.yaml")["p_det"]
    assert "no training or threshold selection on MADOS" in m["evaluation"]
    assert m["split"].startswith("официальный MADOS")

    from pipeline.config import RAW

    sp = RAW / "mados" / "splits"
    if not sp.exists():
        pytest.skip("MADOS не скачан")
    scenes = {s: {p.rsplit("_", 1)[0] for p in (sp / f"{s}_X.txt").read_text().split()}
              for s in ("train", "val", "test")}
    assert not scenes["train"] & scenes["test"] and not scenes["val"] & scenes["test"]
