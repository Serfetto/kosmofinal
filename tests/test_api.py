"""Интеграция: пустые и некорректные входы, согласованность чисел, повторяемость запроса."""
import csv
import hashlib
import io
import json
import math

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from pipeline.aggregate import WEB
from pipeline.config import AOIS

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


def _uniform_met(lon, lat, t0, hours, u=0.5):
    """Поля «как из Copernicus Marine»: течение u м/с на восток, штиль."""
    import numpy as np

    lons, lats = np.arange(lon - 3, lon + 3, 0.1), np.arange(lat - 3, lat + 3, 0.1)
    t = t0.timestamp() - 86400 + 3600.0 * np.arange(hours + 72)
    shape = (len(t), len(lats), len(lons))
    return dict(lons=lons, lats=lats, t=t, cu=np.full(shape, u), cv=np.zeros(shape),
                wu=np.zeros(shape), wv=np.zeros(shape), sources=np.array(["течения: тест"]))


def test_drift_without_imagery(monkeypatch):
    """Дрейф из места полевого измерения S1 (Тихий океан, 2015): снимка нет, поля — реанализ."""
    import pipeline.cmems

    monkeypatch.setattr(pipeline.cmems, "fetch_met", _uniform_met)
    r = client.get("/api/drift?lon=-140&lat=32&t0=2015-07-27T15:34:00&hours=24&n=5")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["t0"].startswith("2015-07-27T15:34:00") and d["sources"] == ["течения: тест"]
    assert len(d["tracks"]) == 5 and len(d["center"]) == 25 and d["beached_frac"] == 0
    # 0,5 м/с на восток за сутки — 43,2 км; диффузия ансамбля сдвигает центр на сотни метров
    (lon0, lat0), (lon1, lat1) = d["center"][0], d["center"][-1]
    assert (lon1 - lon0) * 111.32 * math.cos(math.radians(32)) == pytest.approx(43.2, abs=2) and abs(lat1 - lat0) < 0.02

    assert client.get("/api/drift?lon=-100&lat=40&t0=2015-07-27T15:34:00").status_code == 404  # старт на суше
    assert client.get("/api/drift?lon=-140&lat=32").status_code == 422  # без момента старта


def test_drift_without_credentials_is_503(monkeypatch):
    import pipeline.cmems

    monkeypatch.setattr(pipeline.cmems, "has_credentials", lambda: False)
    monkeypatch.setattr(pipeline.cmems, "cached", lambda path: path.with_name("no-such-cache.npz"))
    r = client.get("/api/drift?lon=-140&lat=32&t0=2015-07-27T15:34:00&hours=24&n=5")
    assert r.status_code == 503 and "Copernicus Marine" in r.json()["detail"] and r.headers["Retry-After"]


def test_flow_for_map_animation(monkeypatch):
    """Течения и ветер по часам для штрихов на карте: сетка, кадры, маска воды; у водохранилища течений нет."""
    from datetime import datetime, timezone

    import backend.app
    import pipeline.drift

    def met(aoi, date):
        m = _uniform_met(sum(AOIS[aoi]["bbox"][::2]) / 2, sum(AOIS[aoi]["bbox"][1::2]) / 2,
                         datetime.fromisoformat(date).replace(tzinfo=timezone.utc), 72)
        m["wv"] = m["wv"] + 5.0
        return m

    monkeypatch.setattr(pipeline.drift, "fetch_met", met)
    backend.app._flow_aoi.cache_clear()
    try:
        d = client.get(f"/api/aois/{AOI}/{_date()}/flow?hours=24").json()
        nx, ny = len(d["lons"]), len(d["lats"])
        assert nx <= 32 and ny <= 32 and d["hours"] == 24
        assert len(d["currents"]["u"]) == len(d["wind"]["v"]) == 25 and len(d["currents"]["u"][0]) == nx * ny
        assert set(d["currents"]["u"][0]) == {0.5} and set(d["wind"]["v"][12]) == {5.0}
        w = d["water"]
        at = lambda lon, lat: w["bits"][round((lat - w["lat0"]) / w["step"]) * w["nx"] + round((lon - w["lon0"]) / w["step"])]  # noqa: E731
        assert len(w["bits"]) == w["nx"] * w["ny"] and at(39.60, 43.45) == "1" and at(39.95, 43.60) == "0"  # море / горы
        inland = next(a for a, v in AOIS.items() if v["kind"] == "inland")
        date = json.loads((WEB / inland / "series.json").read_text(encoding="utf-8"))["dates"][-1]
        d = client.get(f"/api/aois/{inland}/{date}/flow?hours=6").json()
        assert d["currents"] is None and len(d["wind"]["u"]) == 7
    finally:
        backend.app._flow_aoi.cache_clear()


def test_flow_without_imagery(monkeypatch):
    import backend.app
    import pipeline.cmems

    monkeypatch.setattr(pipeline.cmems, "fetch_met", _uniform_met)
    backend.app._flow_at.cache_clear()
    try:
        d = client.get("/api/flow?lon=-140&lat=32&t0=2015-07-27T15:34:00&hours=24").json()
        assert d["t0"].startswith("2015-07-27T15:34") and d["sources"] == ["течения: тест"]
        assert len(d["lons"]) <= 32 and set(d["currents"]["u"][24]) == {0.5} and set(d["water"]["bits"]) == {"1"}
        assert client.get("/api/flow?lon=-100&lat=40&t0=2015-07-27T15:34:00").status_code == 404  # на суше
    finally:
        backend.app._flow_at.cache_clear()


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


def test_zones_flat_json():
    """Плоский JSON зон согласован с GeoJSON снимка и с выгрузкой CSV; фильтры, порядок и страницы."""
    d = _date()
    fc = client.get(f"/api/aois/{AOI}/{d}/zones").json()["features"]
    r = client.get(f"/api/zones?aoi={AOI}&date={d}&limit=10000&profile=B").json()
    assert r["total"] == r["count"] == len(fc)
    geo = {f["properties"]["zone_id"]: f["properties"] for f in fc}
    exp = client.get(f"/api/export?aoi={AOI}&date={d}&profile=B&layer=zones&format=csv")
    csv_rows = {row["zone_id"]: row for row in csv.DictReader(io.StringIO(exp.content.decode("utf-8-sig")))}
    for it in r["items"]:
        z = geo[it["zone_id"]]
        assert (it["lat"], it["lon"], it["date"], it["cover_m2"]) == (z["lat"], z["lon"], z["date"], z["cover_m2"])
        assert it["conc_items_km2"] == z["conc_B_items_km2"] and it["conc_status"] == csv_rows[it["zone_id"]]["conc_status"]
        assert it["unit"] == "шт./км²" and it.get("geometry") is None
    covers = [it["cover_m2"] for it in r["items"]]
    assert covers == sorted(covers, reverse=True)

    full = client.get(f"/api/zones?aoi={AOI}&limit=10").json()["items"]
    a = client.get(f"/api/zones?aoi={AOI}&limit=5").json()["items"]
    b = client.get(f"/api/zones?aoi={AOI}&limit=5&offset=5").json()["items"]
    assert [z["zone_id"] for z in a + b] == [z["zone_id"] for z in full]
    if fc:
        g = client.get(f"/api/zones?aoi={AOI}&date={d}&limit=1&geometry=true").json()["items"][0]["geometry"]
        assert g["type"] in ("Polygon", "MultiPolygon")

    total = sum(sc["n_zones"] for k in [a["id"] for a in client.get("/api/aois").json()]
                for sc in json.loads((WEB / k / "series.json").read_text(encoding="utf-8"))["scenes"])
    assert client.get("/api/zones?limit=1").json()["total"] == total
    dates = [z["date"] for z in client.get("/api/zones?sort=date&limit=200").json()["items"]]
    assert dates == sorted(dates, reverse=True)

    assert client.get("/api/zones?sort=x").status_code == 422
    assert client.get("/api/zones?limit=0").status_code == 422
    assert client.get("/api/zones?bbox=1,2").status_code == 422
    assert client.get("/api/zones?aoi=nope").status_code == 404
    assert client.get("/api/zones?aoi=sochi&date=2000-01-01").json()["total"] == 0
