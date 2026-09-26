"""Метрики концентрации: пересчёт из сохранённых эталонов и предсказаний, одинаковые выборки у основной и базовых
моделей, вложенная CV и правило выбора модели сервиса."""
import json

import pandas as pd
import pytest

from pipeline import verify
from pipeline.config import DATA, MODELS
from pipeline.provenance import load_yaml

CONC = DATA / "eval" / "concentration"


def _metrics(pid):
    return json.loads((CONC / f"{pid}_metrics.json").read_text(encoding="utf-8"))


def test_metrics_recomputed_from_saved_predictions():
    # MAE, RMSE, MedAE, ошибка в лог-шкале и ДИ бутстрепа — заново из CSV, без обучения и исходных данных
    assert verify.check_concentration() == []
    assert verify.check_transfer() == []


@pytest.mark.parametrize("pid", ["A", "B"])
def test_main_and_baselines_on_same_events(pid):
    met, samples = verify.conc_samples(pid)
    for part, (d, cols) in samples.items():
        assert len(d) and d["event_id"].is_unique
        # У каждой модели предсказание для каждого события выборки — сравнение на одной выборке
        assert d[list(cols.values())].notna().all().all(), part
        assert d["y_true"].gt(0).all(), "нулей в эталоне нет — MAPE не понадобился бы, но и не используется"
        for m in cols:
            assert met[part][m]["n"] == len(d)
    hold = set(samples["holdout"][0]["event_id"])
    assert not hold & set(samples["cv"][0]["event_id"])
    assert hold == set(json.loads((MODELS / f"conc_{pid}.json").read_text(encoding="utf-8"))["holdout"])


@pytest.mark.parametrize("pid", ["A", "B"])
def test_nested_cv_reselects_features_per_fold(pid):
    met = _metrics(pid)
    cfg = load_yaml("concentration.yaml")["profiles"][pid]
    folds = pd.read_csv(DATA / "splits" / f"{pid}.csv").query("~is_holdout")["fold"].nunique()
    assert [s["fold"] for s in met["nested_selection"]] == list(range(folds))
    assert all(s["features"] in cfg["candidate_features"] for s in met["nested_selection"])
    # У базовых выбора признаков нет: их вложенная CV совпадает с обычной
    for m in met["baselines"]:
        assert met["cv_nested"][m]["mae"] == pytest.approx(met["cv"][m]["mae"])


@pytest.mark.parametrize("pid", ["A", "B"])
def test_serve_rule(pid):
    # Модель сервиса — основная, пока базовая не лучше её значимо на вложенной CV; отложенная выборка в выборе
    # не участвует
    met = _metrics(pid)
    better = [m for m, v in met["uncertainty"]["cv_nested"]["delta"].items() if v["ci"][0] > 0]
    assert met["serve_model"] == (min(better, key=lambda m: met["cv_nested"][m]["mae"]) if better else met["main"])
    served = json.loads((MODELS / f"conc_{pid}.json").read_text(encoding="utf-8"))["serve"]["type"]
    assert served == met["serve_model"]


def test_estimate_refers_to_strip_area():
    # Полевая C относится ко всей полосе, модель считается в её середине. Для профиля A (полосы ~20 км, известны
    # концы и сегменты) это то же, что среднее модели вдоль полосы: расхождение < 0,1%
    import numpy as np
    from shapely import wkt

    from pipeline.concentration import from_json, load_model

    m = from_json(load_model("A")["serve"])
    ev = pd.read_csv(DATA / "field" / "events.csv").query("profile == 'A'")
    for _, r in ev.iterrows():
        g = wkt.loads(r["geometry_wkt"])
        pts = [g.interpolate(t, normalized=True) for t in np.linspace(0, 1, 41)]
        along = m.predict(pd.DataFrame({"lon": [p.x for p in pts], "lat": [p.y for p in pts]})).mean()
        mid = m.predict(pd.DataFrame({"lon": [r["lon"]], "lat": [r["lat"]]}))[0]
        assert abs(along / mid - 1) < 1e-3, r["event_id"]


def test_transfer_predictions_are_saved():
    t = pd.read_csv(CONC / "B_transfer_predictions.csv")
    ev = pd.read_csv(DATA / "field" / "events.csv")
    s3 = ev[(ev["profile"] == "B") & (ev["role"] == "transfer_check")]
    assert set(t["event_id"]) == set(s3["event_id"]) and len(t) == 41
    assert not set(t["event_id"]) & set(pd.read_csv(DATA / "splits" / "B.csv")["event_id"])
