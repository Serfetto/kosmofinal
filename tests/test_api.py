"""Интеграция: пустые и некорректные входы, согласованность чисел, повторяемость запроса."""
import csv
import hashlib
import io
import json

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from pipeline.aggregate import WEB

client = TestClient(app)
AOI = "sochi"


def _date():
    return json.loads((WEB / AOI / "series.json").read_text(encoding="utf-8"))["dates"][-1]


def test_invalid_inputs():
    d = _date()
    assert client.get(f"/api/aois/nope/{d}/zones").status_code == 404
    assert client.get(f"/api/aois/{AOI}/2026-99-99x/zones").status_code == 422
    assert client.get(f"/api/aois/{AOI}/2000-01-01/zones").status_code == 404
    assert client.get(f"/api/export?aoi={AOI}&date={d}&profile=Z").status_code == 422
    assert client.get(f"/api/export?aoi={AOI}&date={d}&layer=pixels").status_code == 422
    assert client.get("/api/field?bbox=1,2,3").status_code == 422
    assert client.get("/api/field/NO-SUCH-EVENT").status_code == 404
    assert client.post("/api/queries", json={"aoi": AOI, "date": "bad"}).status_code == 422


def test_empty_selection_is_empty_collection():
    r = client.get("/api/field?bbox=0,0,0.001,0.001")
    assert r.status_code == 200 and r.json()["features"] == []


def test_export_matches_zones_and_units():
    d = _date()
    zones = client.get(f"/api/aois/{AOI}/{d}/zones").json()["features"]
    r = client.get(f"/api/export?aoi={AOI}&date={d}&profile=B&layer=zones&format=csv")
    assert r.status_code == 200
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert len(rows) == len(zones)
    for row, z in zip(rows, zones):
        p = z["properties"]
        assert row["zone_id"] == p["zone_id"]
        assert row["unit"] == "шт./км²"
        v = p["conc_B_items_km2"]
        assert (row["conc_items_km2"] == "" and v is None) or float(row["conc_items_km2"]) == pytest.approx(v)
        assert row["conc_status"] == p["conc_B_status"]


def test_same_request_same_bytes():
    d = _date()
    url = f"/api/export?aoi={AOI}&date={d}&profile=B&layer=hexes&format=geojson"
    a, b = client.get(url), client.get(url)
    assert a.content == b.content
    assert a.headers["x-result-sha256"] == hashlib.sha256(a.content).hexdigest()


def test_saved_query_rerun_matches():
    q = client.post("/api/queries", json={"aoi": AOI, "date": _date(), "profile": "B", "layer": "zones", "format": "csv"})
    assert q.status_code == 200
    r = client.post(f"/api/queries/{q.json()['id']}/rerun").json()
    assert r["match"] is True


def test_profile_unavailable_is_explicit():
    c = client.get(f"/api/aois/{AOI}/concentration?profile=A").json()
    assert c["available"] is False and c["reason"]


def test_zones_consistent_with_series():
    s = json.loads((WEB / AOI / "series.json").read_text(encoding="utf-8"))
    for d, sc in list(zip(s["dates"], s["scenes"]))[-3:]:
        zones = client.get(f"/api/aois/{AOI}/{d}/zones").json()["features"]
        assert len(zones) == sc["n_zones"]
        assert sum(z["properties"]["n_pixels"] for z in zones) == sc["n_det"]
        for z in zones:
            p = z["properties"]
            assert p["zone_area_km2"] > 0 and p["detection_status"] == "detected"
            assert p["conc_B_status"] in ("model_estimate", "research_estimate", "unavailable")
            assert p["conc_A_status"] == "unavailable"  # профиль A вне бассейна Чёрного моря


def test_report_is_pdf():
    d = _date()
    r = client.get(f"/api/report?aoi={AOI}&date={d}&profile=B")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and f"report_{AOI}_{d}_B.pdf" in r.headers["content-disposition"]
    assert client.get(f"/api/report?aoi=nope&date={d}").status_code == 404
    assert client.get(f"/api/report?aoi={AOI}&date=2000-01-01").status_code == 404
    assert client.get(f"/api/report?aoi={AOI}&date={d}&profile=Z").status_code == 422


def test_report_districts_take_densest_window():
    import numpy as np

    from backend.report import districts

    col, row = np.array([10.0, 12, 14, 500, 900]), np.array([10.0, 11, 12, 500, 900])
    ds = districts(col, row, np.array([5.0, 5, 5, 12, 1]), 100, 60, 2000, 2000, k=2)
    assert [round(d["cover"]) for d in ds] == [15, 12]
    assert sorted(ds[0]["members"].tolist()) == [0, 1, 2]


def test_every_endpoint_documented():
    """Новая ручка без раздела, названия, описания или описаний параметров не пройдёт."""
    ops = [(m, p, op) for p, item in app.openapi()["paths"].items() for m, op in item.items()]
    assert len(ops) >= 26
    for m, p, op in ops:
        assert op.get("tags") and op.get("summary") and op.get("description"), f"{m.upper()} {p}"
        for prm in op.get("parameters", []):
            assert prm.get("description"), f"{m.upper()} {p}: {prm['name']}"
    assert (WEB.parent.parent / "docs" / "api.md").exists()


def test_service_endpoints():
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["aois"] > 0 and "configs" in h["versions"]
    st = client.get("/api/statuses").json()
    assert {s["id"] for s in st["detection"]} == {"detected", "not_detected", "insufficient_data"}
    assert {s["id"] for s in st["concentration"]} == {"model_estimate", "research_estimate", "unavailable"}


def test_aoi_and_scenes():
    a = client.get(f"/api/aois/{AOI}").json()
    assert a == next(x for x in client.get("/api/aois").json() if x["id"] == AOI)
    sc = client.get(f"/api/aois/{AOI}/scenes").json()
    assert [s["date"] for s in sc] == a["dates"]
    last = sc[-1]
    assert len(last["corners"]) == 4
    for key in ("rgb", "debris", "quality", "zones", "points"):
        assert client.get(last["layers"][key]).status_code == 200, key
    assert client.get("/api/aois/nope/scenes").status_code == 404


def test_raster_grid_matches_corners():
    """Узлы привязки растров сходятся с углами снимка и накрывают его с запасом на прореженные rgb/quality."""
    import numpy as np

    g = client.get(f"/api/aois/{AOI}/grid").json()
    corners = client.get(f"/api/aois/{AOI}/scenes").json()[-1]["corners"]
    n = np.asarray(g["lonlat"])
    assert n[0, 0] == pytest.approx(corners[0], abs=1e-6)
    assert (n.shape[1] - 1) * g["step"] > g["width"] and (n.shape[0] - 1) * g["step"] > g["height"]

    def at(col, row):  # как во фронтенде: билинейно между узлами
        i, j = int(col // g["step"]), int(row // g["step"])
        a, b = col / g["step"] - i, row / g["step"] - j
        return (1 - b) * ((1 - a) * n[j, i] + a * n[j, i + 1]) + b * ((1 - a) * n[j + 1, i] + a * n[j + 1, i + 1])

    for (col, row), c in zip([(g["width"], 0), (g["width"], g["height"]), (0, g["height"])], corners[1:]):
        assert at(col, row) == pytest.approx(c, abs=1e-6)
    assert client.get("/api/aois/nope/grid").status_code == 404


def test_errors_are_readable():
    r = client.get(f"/api/aois/{AOI}/2026-99-99x/zones")
    assert r.status_code == 422 and "YYYY-MM-DD" in r.json()["detail"] and r.json()["errors"]
    r = client.get(f"/api/aois/{AOI}/{_date()}/drift?hours=500")
    assert r.status_code == 422 and "hours" in r.json()["detail"]
    r = client.get("/api/aois/nope")
    assert r.status_code == 404 and isinstance(r.json()["detail"], str)


def test_weather_outage_is_503(monkeypatch):
    import requests

    import pipeline.drift

    def rate_limited(*a, **kw):
        resp = requests.Response()
        resp.status_code = 429
        raise requests.HTTPError("429 Too Many Requests", response=resp)

    monkeypatch.setattr(pipeline.drift, "fetch_met", rate_limited)
    r = client.get(f"/api/aois/{AOI}/{_date()}/drift_point?lon=39.72&lat=43.55&hours=24&n=5")
    assert r.status_code == 503 and "Open-Meteo" in r.json()["detail"] and r.headers["Retry-After"]


def test_field_sources_cover_s1_to_s4():
    """Состав наблюдений из постановки: четыре источника, все 935 строк и 318 событий реестра."""
    r = client.get("/api/field/sources")
    assert r.status_code == 200
    src = {s["code"]: s for s in r.json()}
    assert list(src) == ["S1", "S2", "S3", "S4"]
    assert sum(s["n_rows"] for s in src.values()) == 935
    assert sum(s["n_events"] for s in src.values()) == 318
    assert src["S1"]["pairs"]["scenes"] == {}  # над открытым океаном сцен нет ни в одном архиве
    for s in src.values():
        assert s["date_from"] <= s["date_to"] and s["imagery"]
        for a in s["aois"]:
            assert client.get(f"/api/aois/{a['id']}").json()["source"] == s["source_id"]


def test_field_aois_on_measurement_dates():
    """Снимки районов полевых данных — только на даты событий внутри акватории ± окно реестра пар."""
    from datetime import date

    from pipeline.config import AOIS
    from pipeline.provenance import load_yaml

    win = load_yaml("pairs.yaml")["search_window_days"]
    ev = [f["properties"] for f in client.get("/api/field").json()["features"]]
    field = [a for a in client.get("/api/aois").json() if AOIS[a["id"]].get("field_window")]
    assert field
    for a in field:
        x0, y0, x1, y1 = a["bbox"]
        days = [date.fromisoformat(p["date_utc"]) for p in ev if x0 <= p["lon"] <= x1 and y0 <= p["lat"] <= y1]
        assert days and a["group"] == "field"
        for d in a["dates"]:
            assert min(abs((date.fromisoformat(d) - x).days) for x in days) <= win, (a["id"], d)


def test_field_event_scene_links():
    """Сцену-кандидат можно показать на карте, даже если сервис её не обрабатывал (Landsat над S2)."""
    r = client.get("/api/field/S2:MSM41_litter-T28").json()
    p = [x for x in r["pairs"] if x.get("scene_id")]
    assert p and p[0]["collection"] == "landsat-c2-l2"
    assert p[0]["tiles_url"].startswith("https://planetarycomputer.microsoft.com/") and "{z}/{x}/{y}" in p[0]["tiles_url"]
    assert len(p[0]["scene_bbox"]) == 4
    for x in client.get("/api/field/S4:DOORS3:T18").json()["pairs"]:
        if x["aoi"]:
            assert x["aoi_date"] in client.get(f"/api/aois/{x['aoi']}").json()["dates"]
