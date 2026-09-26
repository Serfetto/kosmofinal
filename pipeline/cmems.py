"""Течения, стоксов дрейф и ветер из реанализов Copernicus Marine — для дрейфа там, где нет снимка.

Open-Meteo отдаёт течения (Meteo-France SMOC) только с 2022 г., а полевые события S1–S3 — это 2014–2016 гг.
Реанализы Copernicus Marine покрывают эти годы:
- течения: NWS — северо-западный шельф Европы, ~7 км, ежечасно, с приливом (если район целиком на шельфе),
  иначе GLORYS12 — весь океан, 1/12°, среднесуточные, без прилива;
- стоксов дрейф волн: глобальный реанализ волн, 0,2°, раз в 3 ч (в SMOC он уже входит, здесь добавляем сами);
- ветер 10 м: L4 по скаттерометрам и модели, 0,125° ежечасно (до 2007 г. — 0,25°).

Нужен бесплатный аккаунт Copernicus Marine: COPERNICUSMARINE_SERVICE_USERNAME и COPERNICUSMARINE_SERVICE_PASSWORD
в .env (читает config.py) или в окружении; годится и `copernicusmarine login` (~/.copernicusmarine).
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from scipy.interpolate import RegularGridInterpolator
from scipy.ndimage import distance_transform_edt

from .config import DATA, cached

M_PER_DEG = 111_320.0
V_MAX = 1.0  # м/с: частица за горизонт не уходит дальше — столько берём полей вокруг точки старта

GLO_CUR = "cmems_mod_glo_phy_my_0.083deg_P1D-m"
NWS_CUR = "cmems_mod_nws_phy-uv_my_7km-2D_PT1H-i"
NWS_BOX = (-19.88, 40.07, 12.99, 65.0)
WAVES = "cmems_mod_glo_wav_my_0.2deg_PT3H-i"
WIND = [("cmems_obs-wind_glo_phy_my_l4_0.125deg_PT1H", datetime(2007, 1, 11, tzinfo=timezone.utc)),
        ("cmems_obs-wind_glo_phy_my_l4_0.25deg_PT1H", datetime(1994, 6, 1, tzinfo=timezone.utc))]
SOURCE_NAME = {
    GLO_CUR: "течения: GLORYS12, реанализ Copernicus Marine (1/12°, среднесуточные, без прилива)",
    NWS_CUR: "течения: NWS, реанализ северо-западного шельфа Copernicus Marine (~7 км, ежечасно, с приливом)",
    WAVES: "стоксов дрейф: реанализ волн Copernicus Marine (0,2°, раз в 3 ч)",
    WIND[0][0]: "ветер 10 м: L4 по скаттерометрам и модели, Copernicus Marine (0,125°, ежечасно)",
    WIND[1][0]: "ветер 10 м: L4 по скаттерометрам и модели, Copernicus Marine (0,25°, ежечасно)",
}


class CmemsUnavailable(RuntimeError):
    """Поля Copernicus Marine получить нельзя: нет аккаунта или сервис не ответил."""
    status = 503


class CmemsNoData(CmemsUnavailable):
    """На эти даты или в этом месте реанализа нет."""
    status = 404


def has_credentials() -> bool:
    if os.environ.get("COPERNICUSMARINE_SERVICE_USERNAME") and os.environ.get("COPERNICUSMARINE_SERVICE_PASSWORD"):
        return True
    base = Path(os.environ.get("COPERNICUSMARINE_CREDENTIALS_DIRECTORY") or Path.home()) / ".copernicusmarine"
    return (base / ".copernicusmarine-credentials").exists()


def _epoch(times) -> np.ndarray:
    return np.asarray(times, "datetime64[s]").astype(np.int64).astype(float)


def _load(dataset_id: str, variables: list[str], box: tuple, start: datetime, end: datetime, depth: bool = False):
    """Поля в рамке и интервале: (время, с; широты; долготы; {переменная: [t, lat, lon]})."""
    import copernicusmarine as cm

    logging.getLogger("copernicusmarine").setLevel(logging.WARNING)  # без INFO о выборе версии на каждый запрос
    kw = dict(dataset_id=dataset_id, variables=variables,
              minimum_longitude=box[0], maximum_longitude=box[2], minimum_latitude=box[1], maximum_latitude=box[3],
              start_datetime=start.strftime("%Y-%m-%dT%H:%M:%S"), end_datetime=end.strftime("%Y-%m-%dT%H:%M:%S"),
              coordinates_selection_method="outside")
    if depth:  # верхний уровень модели — 0,49 м; с «outside» рамка 0,5 м захватывает его и следующий
        kw.update(minimum_depth=0.5, maximum_depth=0.5)
    try:
        ds = cm.open_dataset(**kw)
        if "depth" in ds.dims:
            ds = ds.isel(depth=0)
        ds = ds.sortby("latitude").sortby("longitude").load()
    except cm.CoordinatesOutOfDatasetBounds as e:
        m = re.search(r"dataset coordinates \[(\d{4}-\d\d-\d\d)[^,]*, (\d{4}-\d\d-\d\d)", str(e))
        span = f": он есть с {_ru(m[1])} по {_ru(m[2])}" if m else ""
        raise CmemsNoData(f"реанализ Copernicus Marine ({dataset_id}) не покрывает эти даты{span}") from e
    except (cm.CouldNotConnectToAuthenticationSystem, cm.InvalidUsernameOrPassword) as e:
        # Сервер авторизации отвечает 400 и на неверный пароль, тулбокс превращает это в «не смог подключиться»
        raise CmemsUnavailable("Copernicus Marine не принял логин и пароль из .env: проверьте их (подходит и e-mail, "
                               "и имя пользователя) или повторите позже") from e
    except Exception as e:  # noqa: BLE001 — у тулбокса свои исключения на каждый случай, для API все они — «не ответил»
        raise CmemsUnavailable(f"Copernicus Marine не отдал {dataset_id}: {type(e).__name__} {e}".strip()) from e
    t = _epoch(ds["time"].values)
    if len(t) == 0:
        raise CmemsNoData(f"в {dataset_id} нет данных на {start:%d.%m.%Y}–{end:%d.%m.%Y}")
    return t, ds["latitude"].values.astype(float), ds["longitude"].values.astype(float), \
        {v: ds[v].values.astype(np.float32) for v in variables}


def _ru(iso: str) -> str:
    return ".".join(reversed(iso.split("-")))


def _day(ts: float) -> str:
    return f"{datetime.fromtimestamp(ts, timezone.utc):%d.%m.%Y}"


def _fill_nearest(a: np.ndarray) -> np.ndarray:
    """NaN (суша) — значением ближайшей морской клетки: иначе у берега ветер и стоксов дрейф обнуляются."""
    nan = ~np.isfinite(a).any(0)
    if not nan.any() or nan.all():
        return np.nan_to_num(a)
    idx = distance_transform_edt(nan, return_distances=False, return_indices=True)
    return np.nan_to_num(a[:, idx[0], idx[1]])


def _interp_time(src: tuple, var: str, t: np.ndarray) -> np.ndarray:
    """Только по времени, на сетке источника: суша (NaN) не расползается на соседние клетки."""
    st, a = src[0], src[3][var]
    if len(st) == 1:
        return np.repeat(a, len(t), 0)
    i = np.clip(np.searchsorted(st, t) - 1, 0, len(st) - 2)
    w = np.clip((t - st[i]) / (st[i + 1] - st[i]), 0, 1)[:, None, None].astype(np.float32)
    return a[i] * (1 - w) + a[i + 1] * w


def _regrid(src: tuple, var: str, t: np.ndarray, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """На сетку течений; суша заполняется ближайшей морской клеткой."""
    st, sla, slo, fields = src
    a = _fill_nearest(fields[var])
    if len(st) == 1:  # один срок — поле постоянно во времени
        st, a = np.r_[st, st + 1], np.concatenate([a, a])
    f = RegularGridInterpolator((st, sla, slo), a, bounds_error=False, fill_value=None)
    T, LA, LO = np.meshgrid(np.clip(t, st[0], st[-1]), lats, lons, indexing="ij")
    return f(np.stack([T, LA, LO], -1)).astype(np.float32)


def fetch_met(lon: float, lat: float, t0: datetime, hours: int) -> dict:
    """Почасовые поля течений (со стоксовым дрейфом) и ветра вокруг точки — в формате `drift.fetch_met`."""
    half_lat = min(4.0, max(0.5, hours * 3600 * V_MAX / M_PER_DEG))
    half_lon = min(8.0, half_lat / max(np.cos(np.radians(lat)), 0.2))
    # Центр рамки — по сетке 0,25°, начало — по суткам: соседние события одного дня берут поля из одного кеша
    clat, clon = round(lat * 4) / 4, round(lon * 4) / 4
    box = (clon - half_lon, max(clat - half_lat, -80.0), clon + half_lon, min(clat + half_lat, 80.0))
    start = datetime.combine(t0.date(), datetime.min.time(), tzinfo=timezone.utc) - timedelta(days=1)
    end = t0 + timedelta(hours=hours + 24)
    ndays = (end - start).days + 1
    cache = cached(DATA / "cmems" / f"{clat:+.2f}_{clon:+.2f}_{start:%Y%m%d}_{ndays}d_{half_lat:.2f}.npz")
    if cache.exists():
        z = np.load(cache)
        return {k: z[k].astype(np.float32) if z[k].dtype == np.float16 else z[k] for k in z.files}
    if not has_credentials():
        raise CmemsUnavailable("для дрейфа без снимка нужен аккаунт Copernicus Marine: впишите "
                               "COPERNICUSMARINE_SERVICE_USERNAME и COPERNICUSMARINE_SERVICE_PASSWORD в .env "
                               "(образец — .env.example) и перезапустите сервис")

    on_shelf = NWS_BOX[0] <= box[0] and box[2] <= NWS_BOX[2] and NWS_BOX[1] <= box[1] and box[3] <= NWS_BOX[3]
    cur_id = NWS_CUR if on_shelf else GLO_CUR
    cur = _load(cur_id, ["uo", "vo"], box, start, end, depth=not on_shelf)
    if cur_id == GLO_CUR:  # среднесуточное значение — середина суток, а не полночь
        cur = (cur[0] + 12 * 3600.0, *cur[1:])
    if not np.isfinite(cur[3]["uo"]).any():
        raise CmemsNoData("в районе точки нет морских клеток реанализа течений")
    wave = _load(WAVES, ["VSDX", "VSDY"], box, start, end)
    wind_id = next((d for d, since in WIND if start >= since), None)
    if wind_id is None:
        raise CmemsNoData(f"реанализа ветра Copernicus Marine до {WIND[-1][1]:%d.%m.%Y} нет")
    wind = _load(wind_id, ["eastward_wind", "northward_wind"], box, start, end)

    lats, lons = cur[1], cur[2]
    t = start.timestamp() + 3600.0 * np.arange(int((end - start).total_seconds() // 3600) + 1)
    first = max(src[0][0] for src in (cur, wave, wind))
    last = min(src[0][-1] for src in (cur, wave, wind))
    if first > t0.timestamp() + 12 * 3600:
        raise CmemsNoData(f"реанализ Copernicus Marine начинается только с {_day(first)}")
    if last < t0.timestamp() + hours * 3600:
        raise CmemsNoData(f"реанализ Copernicus Marine пока доходит только до {_day(last)}")
    cu, cv = (_interp_time(cur, v, t) for v in ("uo", "vo"))
    su, sv = (_regrid(wave, v, t, lats, lons) for v in ("VSDX", "VSDY"))
    wu, wv = (_regrid(wind, v, t, lats, lons) for v in ("eastward_wind", "northward_wind"))
    met = dict(lons=lons, lats=lats, t=t, cu=cu + su, cv=cv + sv, wu=wu, wv=wv,
               sources=np.array([SOURCE_NAME[cur_id], SOURCE_NAME[WAVES], SOURCE_NAME[wind_id]]))
    cache.parent.mkdir(parents=True, exist_ok=True)
    # Поля в float16 (шаг 0,0002 м/с для течений, 0,01 м/с для ветра) — кеш вдвое меньше: ~3 МБ на место и день
    np.savez_compressed(cache, **{k: v.astype(np.float16) if v.dtype == np.float32 else v for k, v in met.items()})
    return met
