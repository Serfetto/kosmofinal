"""Поля Copernicus Marine для дрейфа без снимка: выбор набора, сдвиг суточных средних, суша, кеш — без сети."""
from datetime import datetime, timezone

import numpy as np
import pytest

from pipeline import cmems

T0 = datetime(2015, 7, 27, 15, 34, tzinfo=timezone.utc)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """Подменяет загрузку: течение 0,1 × (номер суток) м/с на восток, стоксов дрейф 0,05, ветер 5 м/с на север;
    западная половина рамки — суша (NaN)."""
    calls = []

    def load(dataset_id, variables, box, start, end, depth=False):
        calls.append(dataset_id)
        step = {cmems.GLO_CUR: 86400, cmems.WAVES: 3 * 3600}.get(dataset_id, 3600)
        t = np.arange(start.timestamp(), end.timestamp() + 1, step)
        lats, lons = np.arange(box[1], box[3] + 0.1, 0.1), np.arange(box[0], box[2] + 0.1, 0.1)
        land = lons < (box[0] + box[2]) / 2
        day = ((t - start.timestamp()) // 86400)[:, None, None]
        base = {"uo": 0.1 * day, "vo": 0.0, "VSDX": 0.05, "VSDY": 0.0, "eastward_wind": 0.0, "northward_wind": 5.0}
        out = {}
        for v in variables:
            a = np.broadcast_to(np.asarray(base[v], np.float32), (len(t), len(lats), len(lons))).copy()
            a[:, :, land] = np.nan
            out[v] = a
        return t.astype(float), lats, lons, out

    monkeypatch.setattr(cmems, "_load", load)
    monkeypatch.setattr(cmems, "has_credentials", lambda: True)
    monkeypatch.setattr(cmems, "cached", lambda path: tmp_path / path.name)
    return calls


def test_open_ocean_uses_glorys_with_stokes(fake):
    met = cmems.fetch_met(-140.0, 32.0, T0, 24)
    assert fake == [cmems.GLO_CUR, cmems.WAVES, cmems.WIND[0][0]]
    assert "GLORYS12" in str(met["sources"][0])
    start = met["t"][0]  # полночь суток перед стартом
    east = met["lons"] >= -140.0
    # Суточное среднее относится к полудню: в 12:00 первых суток течение ровно 0 (+ стоксов дрейф 0,05),
    # в полночь между первыми и вторыми сутками — среднее соседних (0,05 + 0,05)
    at = lambda h: met["cu"][int(h), :, east].mean()  # noqa: E731
    assert at(12) == pytest.approx(0.05, abs=1e-6)
    assert at(24) == pytest.approx(0.10, abs=1e-6)
    assert at(36) == pytest.approx(0.15, abs=1e-6)
    assert met["t"][1] - start == 3600
    # Суша остаётся сушей в течениях (маска для выброса на берег), а ветер у берега не обнуляется
    assert np.isnan(met["cu"][:, :, ~east]).all()
    assert np.allclose(met["wv"], 5.0)


def test_north_sea_shelf_uses_hourly_tidal_model(fake):
    met = cmems.fetch_met(7.5, 54.3, T0, 24)
    assert fake[0] == cmems.NWS_CUR and "прилив" in str(met["sources"][0])


def test_cached_fields_are_reused(fake):
    a = cmems.fetch_met(-140.0, 32.0, T0, 24)
    b = cmems.fetch_met(-140.1, 31.95, T0.replace(hour=20), 24)  # то же место с точностью до 0,25° и тот же день
    assert len(fake) == 3 and np.allclose(a["cu"], b["cu"], atol=1e-3, equal_nan=True)  # кеш — в float16
    assert b["cu"].dtype == np.float32


def test_dates_outside_reanalysis_are_404(fake):
    with pytest.raises(cmems.CmemsNoData) as e:
        cmems.fetch_met(-140.0, 32.0, datetime(1990, 1, 1, tzinfo=timezone.utc), 24)
    assert e.value.status == 404


def test_env_file(monkeypatch, tmp_path):
    """Логин Copernicus Marine из .env: комментарии, кавычки, `export`; окружение важнее файла."""
    from pipeline.config import _load_env

    (tmp_path / ".env").write_text('# логин\nexport CM_TEST_USER="user one"\nCM_TEST_PASS=\'p=ss\'\nCM_TEST_EMPTY=\n'
                                   'CM_TEST_SET=from-file\n', encoding="utf-8")
    for k in ("CM_TEST_USER", "CM_TEST_PASS", "CM_TEST_EMPTY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CM_TEST_SET", "from-env")
    _load_env(tmp_path / ".env")
    import os
    assert os.environ["CM_TEST_USER"] == "user one" and os.environ["CM_TEST_PASS"] == "p=ss"
    assert "CM_TEST_EMPTY" not in os.environ and os.environ["CM_TEST_SET"] == "from-env"
    for k in ("CM_TEST_USER", "CM_TEST_PASS"):
        monkeypatch.delenv(k)
