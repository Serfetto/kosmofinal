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


def test_errors_are_readable():
    r = client.get(f"/api/aois/{AOI}/2026-99-99x/zones")
    assert r.status_code == 422 and "YYYY-MM-DD" in r.json()["detail"] and r.json()["errors"]
    r = client.get(f"/api/aois/{AOI}/{_date()}/drift?hours=500")
    assert r.status_code == 422 and "hours" in r.json()["detail"]
    r = client.get("/api/aois/nope")
    assert r.status_code == 404 and isinstance(r.json()["detail"], str)
