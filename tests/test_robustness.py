"""Т5: сервис не падает на некорректных и пустых входах — ни одна ручка не отвечает 500.

Сеть отключена: всё, что пошло бы в Open-Meteo или Copernicus Marine, должно превратиться в понятную ошибку,
а не в трассировку.
"""
import json
import re

import pytest
import requests
from fastapi.testclient import TestClient

from backend.app import app
from pipeline.aggregate import WEB

client = TestClient(app, raise_server_exceptions=False)

# Заведомо плохие значения по имени параметра; остальным — мусорная строка
BAD = {
    "aoi": "nope", "date": "2026-99-99x", "profile": "zz", "i": "-1", "event_id": "NO:SUCH:EVENT",
    "qid": "xyz", "lon": "abc", "lat": "999", "t": "not-a-date", "t0": "not-a-date", "hours": "100000",
    "n": "0", "speed": "-5", "delay": "1e9", "bbox": "1,2,3", "format": "xml", "layer": "pixels",
    "limit": "0", "offset": "-3", "sort": "nope", "min_cover_m2": "-1", "date_from": "01.01.2024",
    "date_to": "2024/01/01", "geometry": "maybe", "reliable_only": "perhaps",
}
EMPTY = {"aoi": "", "date": "", "profile": "", "lon": "", "lat": "", "t": "", "t0": "", "bbox": ",,,"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def down(*a, **kw):
        raise requests.ConnectionError("сеть отключена в тесте")

    monkeypatch.setattr(requests, "get", down)
    monkeypatch.setattr(requests.Session, "request", down)


def _routes():
    out = []
    for path, item in app.openapi()["paths"].items():
        for method, op in item.items():
            out.append((method, path, [p["name"] for p in op.get("parameters", [])]))
    return out


def _url(path: str, params: list[str], values: dict) -> str:
    url = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), "zzz") or "x", path)
    q = "&".join(f"{p}={values.get(p, 'zzz')}" for p in params if "{" + p + "}" not in path)
    return f"{url}?{q}" if q else url


@pytest.mark.parametrize("method,path,params", _routes())
def test_garbage_never_500(method, path, params):
    for values in (BAD, EMPTY, {}):
        r = client.request(method.upper(), _url(path, params, values))
        assert r.status_code < 500 or r.status_code == 503, f"{method.upper()} {path}: {r.status_code} {r.text[:200]}"
        if r.status_code >= 400:
            assert isinstance(r.json()["detail"], str) and r.json()["detail"], path


def test_missing_required_params_are_422():
    for url in ("/api/export", "/api/report", "/api/drift", "/api/fusion", "/api/flow",
                "/api/aois/sochi/2026-09-13/drift_point"):
        r = client.get(url)
        assert r.status_code in (404, 422), url
        assert "обязательный параметр" in r.json()["detail"] or r.status_code == 404, url


def test_bad_json_bodies():
    for body in (b"", b"{", b"[]", b'{"aoi": 1, "date": null}', json.dumps({"aoi": "sochi"}).encode()):
        r = client.post("/api/queries", content=body, headers={"Content-Type": "application/json"})
        assert r.status_code == 422, body
    assert client.post("/api/queries/000000000000/rerun").status_code == 404
    assert client.post("/api/queries", json={"aoi": "nope", "date": "2024-06-05"}).status_code == 404


def test_network_outage_is_503_not_500():
    """Нет сети и нет кеша полей: дрейф на дату без met.npz — 503 с понятным текстом."""
    for aoi in sorted(p.name for p in WEB.iterdir() if (p / "series.json").exists()):
        s = json.loads((WEB / aoi / "series.json").read_text(encoding="utf-8"))
        for d in s["dates"]:
            if not (WEB.parent / "processed" / aoi / d / "met.npz").exists():
                r = client.get(f"/api/aois/{aoi}/{d}/drift_point?lon={s['scenes'][0]['corners'][0][0]}"
                               f"&lat={s['scenes'][0]['corners'][0][1]}&hours=24&n=2")
                assert r.status_code == 503 and "Open-Meteo" in r.json()["detail"]
                return
    pytest.skip("у всех снимков поля течений в кеше")


def test_empty_date_is_valid_everywhere():
    """Снимок без зон: пустые коллекции и выгрузки с одной шапкой, отчёт собирается, разбор снимка есть."""
    for aoi in sorted(p.name for p in WEB.iterdir() if (p / "series.json").exists()):
        s = json.loads((WEB / aoi / "series.json").read_text(encoding="utf-8"))
        empty = [d for d, sc in zip(s["dates"], s["scenes"]) if sc["n_zones"] == 0]
        if not empty:
            continue
        d = empty[0]
        assert client.get(f"/api/aois/{aoi}/{d}/zones").json()["features"] == []
        csv_ = client.get(f"/api/export?aoi={aoi}&date={d}&layer=zones&format=csv")
        assert csv_.status_code == 200 and csv_.content.decode("utf-8-sig").count("\n") == 1
        assert client.get(f"/api/zones?aoi={aoi}&date={d}").json()["total"] == 0
        assert client.get(f"/api/aois/{aoi}/{d}/route").json()["stops"] == []
        drift = client.get(f"/api/aois/{aoi}/{d}/drift")
        assert drift.status_code == 200 and drift.json()["n"] == 0
        rep = client.get(f"/api/report?aoi={aoi}&date={d}")
        assert rep.status_code == 200 and rep.content.startswith(b"%PDF")
        sep = client.get(f"/api/aois/{aoi}/{d}/separation")
        assert sep.status_code == 200 and sep.json()["kept"] == 0
        return
    pytest.skip("снимков без зон нет")


def test_unknown_but_wellformed_ids_are_404():
    assert client.get("/api/aois/nope/2024-06-05/separation").status_code == 404
    assert client.get("/api/aois/sochi/2000-01-01/separation").status_code == 404
    assert client.get("/api/aois/sochi/2026-09-13/hex/999999").status_code == 404
    assert client.get("/api/field/S9:NOPE").status_code == 404
    assert client.get("/api/queries/abcdefabcdef").status_code == 404
    r = client.get("/api/fusion?lon=10&lat=10&t=2024-06-05T00:00:00Z&profile=B")
    assert r.status_code == 404 and "недоступна" in r.json()["detail"]
