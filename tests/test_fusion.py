"""Сведение источников, различение объектов, методика в интерфейсе, реестр данных и воспроизводимость метрик."""
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from pipeline import fusion
from pipeline.aggregate import WEB
from pipeline.config import MODELS, ROOT

client = TestClient(app)


def _fm(profile="B"):
    return fusion.load(profile)


def test_no_measurements_nearby_means_model():
    """Далеко от всех измерений (другой год): сведённая оценка и интервалы — ровно модельные."""
    from pipeline.concentration import apply_interval, load_model

    f = fusion.fuse("B", 39.72, 43.55, "2026-09-13T08:30:00", 300.0)
    assert f["evidence"] == [] and f["field_weight"] == 0 and f["model"]["weight"] == 1
    assert f["fused"]["value"] == pytest.approx(300.0, abs=0.1)
    lo, hi = apply_interval(np.array([300.0]), load_model("B")["interval_log_quantiles"]["0.8"])
    assert (f["fused"]["lo80"], f["fused"]["hi80"]) == pytest.approx((lo[0], hi[0]), abs=0.1)


def test_fresh_colocated_measurement_dominates():
    """Измерение в той же точке и в тот же момент получает больший вес, чем модель, и сужает интервал."""
    e = next(x for x in _fm()["evidence"] if x["event_id"] == "S4:DOORS3:T19")
    f = fusion.fuse("B", e["lon"], e["lat"], e["t_ref"], 300.0)
    top = f["evidence"][0]
    assert top["event_id"] == "S4:DOORS3:T19" and top["weight"] > f["model"]["weight"]
    assert top["same_patch"] and f["variance_reduction"] > 0.3
    from pipeline.concentration import apply_interval, load_model

    lo, hi = apply_interval(np.array([300.0]), load_model("B")["interval_log_quantiles"]["0.8"])
    width = lambda a, b: np.log(b + 1) - np.log(a + 1)  # noqa: E731
    assert width(f["fused"]["lo80"], f["fused"]["hi80"]) < width(lo[0], hi[0])
    # Сведённое значение сдвигается к измерению в поправке модели: z = m + Σ w r
    k = fusion.krige(_fm(), e["lon"], e["lat"], fusion._ts(e["t_ref"]), np.log(301.0))
    assert k["z"] == pytest.approx(np.log(301.0) + float(k["weights"] @ [x["resid"] for x in k["ev"]]))


@pytest.mark.parametrize("profile", ["A", "B"])
def test_weight_falls_with_distance_and_age(profile):
    e = _fm(profile)["evidence"][0]
    w = lambda lon, days: fusion.krige(_fm(profile), lon, e["lat"], e["t"] + days * 86400, 0.0)  # noqa: E731
    near, far = w(e["lon"], 0), w(e["lon"] + 1.0, 0)
    fresh, old = w(e["lon"], 0), w(e["lon"], 5)
    wid = lambda k: dict(zip([x["event_id"] for x in k["ev"]], k["weights"]))[e["event_id"]]  # noqa: E731
    assert wid(near) > wid(far) > 0 and wid(fresh) > wid(old) > 0
    assert near["var"] < far["var"] <= near["var0"]


def test_variogram_selected_without_holdout():
    """Параметры сведения подобраны на обучающей части: отложенные события не участвуют в кросс-валидации."""
    for p in ("A", "B"):
        fm = _fm(p)
        vg = fm["variogram"]
        assert vg["nugget"] >= vg["meas_var_mean"] - 1e-9 and vg["psill"] > 0 and 5 <= vg["range_km"] <= 500
        assert vg["loo_mse"] < vg["loo_mse_no_neighbours"]
        held = set(json.loads((MODELS / f"conc_{p}.json").read_text(encoding="utf-8"))["holdout"])
        assert held <= {e["event_id"] for e in fm["evidence"] if e["part"] == "holdout"}


def test_fusion_api_and_hex_card():
    r = client.get("/api/fusion?lon=41.6&lat=41.62&t=2024-06-05T08:30:00Z&profile=B")
    assert r.status_code == 200
    f = r.json()
    assert abs(f["model"]["weight"] + f["field_weight"] - 1) < 1e-3 and f["unit"] == "шт./км²"
    assert f["evidence"] and all(e["weight"] > 0 for e in f["evidence"])
    s = json.loads((WEB / "batumi" / "series.json").read_text(encoding="utf-8"))
    h = client.get(f"/api/aois/batumi/{s['dates'][1]}/hex/10?profile=B").json()
    assert h["calc"] and h["calc"][-2]["label"] == "Оценка"
    assert f"{h['conc_items_km2']:.1f}".replace(".", ",") in h["calc"][-2]["formula"]
    assert h["fusion"]["model"]["value"] == pytest.approx(h["conc_items_km2"], abs=0.1)


def test_separation_matches_published_detections():
    """Разбор снимка сходится с картой: кандидаты = отбраковано + осталось, осталось = детекции снимка."""
    checked = 0
    for p in sorted(WEB.iterdir()):
        if not (p / "separation.json").exists():
            continue
        sep = json.loads((p / "separation.json").read_text(encoding="utf-8"))
        s = json.loads((p / "series.json").read_text(encoding="utf-8"))
        assert sep["dates"] == s["dates"]
        for d, sc in zip(s["dates"], s["scenes"]):
            st = sep["by_date"][d]
            assert st["kept"] == sc["n_det"]
            assert st["candidates"] == st["kept"] + sum(st["rejected"].values())
        checked += 1
    assert checked, "нет ни одного separation.json"
    d = client.get("/api/aois/batumi/2024-06-05/separation").json()
    assert set(d["rejected"]) == set(d["reasons_ru"]) and d["model_classes_ru"]["organic"]


def test_methodology_in_ui_uses_served_models():
    m = client.get("/api/methodology").json()
    assert {t["profile"] for t in m["target"]} == {"A", "B"}
    assert all(t["unit"] == "шт./км²" for t in m["target"])
    assert "2,5 см" in next(t["size_class"] for t in m["target"] if t["profile"] == "B").replace(".", ",")
    b = json.loads((MODELS / "conc_B.json").read_text(encoding="utf-8"))["serve"]["beta"]
    step = next(s for s in m["steps"] if s["id"] == "model_B")
    assert f"{b[0]:.3f}".replace(".", ",") in " ".join(step["formula"])
    ids = [s["id"] for s in m["steps"]]
    for need in ("field", "classifier", "model_A", "model_B", "interval", "fusion", "drift", "metrics"):
        assert need in ids
    status = {o["object"]: o["status"] for o in m["objects"]}
    assert status["Нефтяные эмульсии, мазут"] == "не выделяем" and status["Морская слизь, медузы"] == "не различаем"
    assert status["Пена и барашки"] == "различаем" and len(m["fusion_rules"]) >= 5


def test_dataset_registry_complete_and_documented():
    from pipeline.datasets import DOC, render

    ds = client.get("/api/datasets").json()
    assert len(ds) >= 12
    for d in ds:
        assert d["license"] and d["role"] and d["used_for"], d["id"]
    ids = {d["id"] for d in ds}
    assert {"case_registry", "marida", "mados", "sentinel2_l2a", "era5_openmeteo", "smoc_openmeteo"} <= ids
    assert DOC.read_text(encoding="utf-8") == render(), "docs/datasets.md устарел: python -m pipeline.datasets"


def test_metrics_reproduce_from_saved_predictions():
    """Метрики детектора, концентрации и сведения пересчитываются из предсказаний и эталонов в репозитории."""
    from pipeline import verify

    assert verify.check_detector() == []
    assert verify.check_concentration() == []
    assert verify.check_fusion() == []
    split = set((ROOT / "data" / "splits" / "marida" / "test_X.txt").read_text().split())
    assert split == set(np.load(ROOT / "data" / "eval" / "detector" / "ref_test.npz").files)
