"""Лагранжев прогноз дрейфа плавающего мусора.

Скорость частицы = течение + windage × ветер (+ случайное блуждание для ансамбля).
Течения — Open-Meteo Marine (Meteo-France SMOC: суммарные поверхностные течения
с приливом и стоксовым дрейфом), ветер — ERA5 / прогноз Open-Meteo. Ключи не нужны.
Для внутренних водоёмов течений нет — только ветровой дрейф.
Там, где снимка нет (полевые события 2014–2016 гг.), — `simulate_at` по реанализам Copernicus Marine (`cmems.py`).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import rasterio
import requests
from global_land_mask import globe
from rasterio.warp import transform as warp_transform
from scipy.interpolate import RegularGridInterpolator
from scipy.ndimage import binary_dilation

from . import cmems
from .config import AOIS, PROCESSED, cached

MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
M_PER_DEG = 111_320.0


def _grid(aoi_id: str, step: float = 0.1, margin: float = 0.25):
    lon0, lat0, lon1, lat1 = AOIS[aoi_id]["bbox"]
    lons = np.round(np.arange(lon0 - margin, lon1 + margin + 1e-9, step), 4)
    lats = np.round(np.arange(lat0 - margin, lat1 + margin + 1e-9, step), 4)
    return lons, lats


def _fetch(url: str, lons, lats, params: dict) -> list:
    LA, LO = np.meshgrid(lats, lons, indexing="ij")
    p = dict(latitude=",".join(map(str, LA.ravel())), longitude=",".join(map(str, LO.ravel())), timezone="GMT", **params)
    r = requests.get(url, params=p, timeout=60)
    r.raise_for_status()
    d = r.json()
    return d if isinstance(d, list) else [d]


def scene_time(aoi_id: str, date: str) -> datetime:
    meta = json.loads((PROCESSED / aoi_id / date / "meta.json").read_text(encoding="utf-8"))
    return datetime.fromisoformat(meta["datetime"].replace("Z", "+00:00"))


def fetch_met(aoi_id: str, date: str, days: int = 4) -> dict:
    """Почасовые поля течений и ветра на сетке 0.1° вокруг акватории, с кэшем на диске."""
    cache = cached(PROCESSED / aoi_id / date / "met.npz")
    if cache.exists():
        z = np.load(cache)
        return {k: z[k] for k in z.files}
    lons, lats = _grid(aoi_id)
    start = datetime.fromisoformat(date).date()
    end = start + timedelta(days=days - 1)
    ny, nx = len(lats), len(lons)
    rng = dict(start_date=str(start), end_date=str(end))

    if AOIS[aoi_id]["kind"] == "sea":
        cur = _fetch(MARINE_URL, lons, lats, dict(hourly="ocean_current_velocity,ocean_current_direction",
                                                  cell_selection="sea", **rng))
        spd = np.array([[np.nan if v is None else v for v in c["hourly"]["ocean_current_velocity"]] for c in cur]) / 3.6
        drc = np.array([[np.nan if v is None else v for v in c["hourly"]["ocean_current_direction"]] for c in cur])
        nt = spd.shape[1]
        # Направление течения — «куда»
        cu = (spd * np.sin(np.radians(drc))).T.reshape(nt, ny, nx)
        cv = (spd * np.cos(np.radians(drc))).T.reshape(nt, ny, nx)
    else:
        cu = cv = None

    recent = (datetime.now(timezone.utc).date() - end).days < 6
    wind_url = FORECAST_URL if recent else ARCHIVE_URL
    wparams = dict(hourly="wind_speed_10m,wind_direction_10m", wind_speed_unit="ms", **rng)
    wnd = _fetch(wind_url, lons, lats, wparams)
    ws = np.array([[np.nan if v is None else v for v in c["hourly"]["wind_speed_10m"]] for c in wnd])
    wd = np.array([[np.nan if v is None else v for v in c["hourly"]["wind_direction_10m"]] for c in wnd])
    nt = ws.shape[1]
    # Направление ветра — «откуда»
    wu = (-ws * np.sin(np.radians(wd))).T.reshape(nt, ny, nx)
    wv = (-ws * np.cos(np.radians(wd))).T.reshape(nt, ny, nx)
    if cu is None:
        cu = np.zeros_like(wu)
        cv = np.zeros_like(wv)
    t0 = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp()
    met = dict(lons=lons, lats=lats, t=t0 + 3600.0 * np.arange(nt), cu=cu, cv=cv, wu=wu, wv=wv)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, **met)
    return met


class LandMask:
    """Вода/суша по маске акватории; за пределами растра (или без него) — по наличию течений и глобальной маске суши.

    Open-Meteo отдаёт течения и для точек на суше (ближайшая морская клетка), поэтому одного наличия
    течений мало: без global-land-mask (~1 км) частицы за краем снимка уплывают в горы.
    """

    def __init__(self, met: dict, water_tif=None, sea: bool = True):
        self.water = None
        if water_tif is not None:
            with rasterio.open(water_tif) as s:
                self.water = binary_dilation(s.read(1).astype(bool), iterations=4)
                self.tr, self.crs = s.transform, s.crs
            self.h, self.w = self.water.shape
        cells = np.isfinite(met["cu"]).any(0) if sea else np.zeros(met["cu"].shape[1:], bool)
        self.sea = RegularGridInterpolator((met["lats"], met["lons"]), cells.astype(float),
                                           method="nearest", bounds_error=False, fill_value=0.0)

    def is_water(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        out = (self.sea(np.c_[lat, lon]) > 0.5) & globe.is_ocean(lat, lon)
        if self.water is None:
            return out
        x, y = warp_transform("EPSG:4326", self.crs, lon.tolist(), lat.tolist())
        col = ((np.asarray(x) - self.tr.c) / self.tr.a).astype(int)
        row = ((np.asarray(y) - self.tr.f) / self.tr.e).astype(int)
        inside = (row >= 0) & (row < self.h) & (col >= 0) & (col < self.w)
        out[inside] = self.water[row[inside], col[inside]]
        return out


def simulate(aoi_id: str, date: str, lon, lat, hours: int = 72, start_offset_h: float = 0.0,
             n_ens: int = 1, windage: float | None = None, diffusivity: float = 5.0, seed: int = 0) -> dict:
    """Дрейф с момента съёмки акватории. Возвращает траектории [n, hours+1, 2] и флаги выброса на берег."""
    met = fetch_met(aoi_id, date)
    sea = AOIS[aoi_id]["kind"] == "sea"
    if windage is None:
        windage = 0.02 if sea else 0.03
    land = LandMask(met, PROCESSED / aoi_id / "water.tif", sea)
    t0 = scene_time(aoi_id, date)
    track, beached = _integrate(met, land, lon, lat, t0.timestamp() + start_offset_h * 3600, hours, n_ens,
                                windage, diffusivity, seed)
    return {"track": track, "beached": beached, "t0": t0.isoformat(),
            "start_offset_h": start_offset_h, "windage": windage}


def simulate_at(lon, lat, t0: datetime, hours: int = 72, n_ens: int = 1, windage: float = 0.02,
                diffusivity: float = 5.0, seed: int = 0) -> dict:
    """Дрейф в море без снимка: с любого момента, поля — реанализы Copernicus Marine вокруг первой точки."""
    lon, lat = np.atleast_1d(np.asarray(lon, float)), np.atleast_1d(np.asarray(lat, float))
    if not globe.is_ocean(lat, lon).all():  # до скачивания полей
        raise cmems.CmemsNoData("точка старта на суше")
    met = cmems.fetch_met(float(lon[0]), float(lat[0]), t0, hours)
    land = LandMask(met)
    if not land.is_water(lon, lat).all():
        raise cmems.CmemsNoData("точка старта у берега, где у реанализа течений нет морских клеток")
    track, beached = _integrate(met, land, lon, lat, t0.timestamp(), hours, n_ens, windage, diffusivity, seed)
    return {"track": track, "beached": beached, "t0": t0.isoformat(), "windage": windage,
            "sources": [str(x) for x in met["sources"]]}


def _integrate(met: dict, land: LandMask, lon, lat, t: float, hours: int, n_ens: int, windage: float,
               diffusivity: float, seed: int):
    """Интегрирование RK2 с шагом 30 мин от момента t (с от эпохи)."""
    rng = np.random.default_rng(seed)
    pts_lon = np.repeat(np.asarray(lon, float), n_ens)
    pts_lat = np.repeat(np.asarray(lat, float), n_ens)
    n = len(pts_lon)
    # Ансамбль: разброс парусности 1–3% (сколько объекта над водой)
    wk = np.full(n, windage) if n_ens == 1 else rng.uniform(0.5 * windage, 1.5 * windage, n)

    axes = (met["t"], met["lats"], met["lons"])
    cu = np.nan_to_num(met["cu"])
    cv = np.nan_to_num(met["cv"])
    wu = np.nan_to_num(met["wu"])
    wv = np.nan_to_num(met["wv"])
    fu = RegularGridInterpolator(axes, cu, bounds_error=False, fill_value=0.0)
    fv = RegularGridInterpolator(axes, cv, bounds_error=False, fill_value=0.0)
    gu = RegularGridInterpolator(axes, wu, bounds_error=False, fill_value=None)
    gv = RegularGridInterpolator(axes, wv, bounds_error=False, fill_value=None)

    def vel(t, x, y):
        q = np.c_[np.full(len(x), t), y, x]
        return fu(q) + wk * gu(q), fv(q) + wk * gv(q)

    dt = 1800.0
    steps_per_h = 2
    x, y = pts_lon.copy(), pts_lat.copy()
    beached = np.zeros(n, bool)
    track = np.zeros((n, hours + 1, 2), np.float32)
    track[:, 0, 0], track[:, 0, 1] = x, y
    sigma = np.sqrt(2 * diffusivity * dt) if n_ens > 1 else 0.0
    for h in range(1, hours + 1):
        for _ in range(steps_per_h):
            u1, v1 = vel(t, x, y)
            k = M_PER_DEG * np.cos(np.radians(y))
            xm, ym = x + 0.5 * dt * u1 / k, y + 0.5 * dt * v1 / M_PER_DEG
            u2, v2 = vel(t + dt / 2, xm, ym)
            nx = x + dt * u2 / k + (rng.normal(0, sigma, n) / k if sigma else 0)
            ny = y + dt * v2 / M_PER_DEG + (rng.normal(0, sigma, n) / M_PER_DEG if sigma else 0)
            move = ~beached
            # Проверяем и середину шага: за 30 мин частица проходит до ~1 км и иначе перескакивает косы и молы
            ok = land.is_water(np.r_[nx, 0.5 * (x + nx)], np.r_[ny, 0.5 * (y + ny)]).reshape(2, n).all(0)
            hit = move & ~ok
            beached |= hit
            move &= ~hit
            x[move], y[move] = nx[move], ny[move]
            t += dt
        track[:, h, 0], track[:, h, 1] = x, y
    return track, beached


def flow_frames(met: dict, t0: float, hours: int, land: LandMask, currents: bool = True,
                max_cells: int = 32, mask_cells: int = 256) -> dict:
    """Течения и ветер по часам от t0 — для анимации на карте, — и маска воды для штрихов течений.

    Сетка прореживается до `max_cells` узлов по большей стороне; кадр — [ячейка] построчно с юга на север,
    в строке с запада на восток; NaN (суша у течений) → None. Маска — `mask_cells` клеток по большей стороне,
    строка из «0»/«1» в том же порядке.
    """
    lons, lats = met["lons"], met["lats"]
    s = max(1, -(-max(len(lons), len(lats)) // max_cells))
    t = t0 + 3600.0 * np.arange(hours + 1)
    i = np.clip(np.searchsorted(met["t"], t) - 1, 0, len(met["t"]) - 2)
    w = np.clip((t - met["t"][i]) / (met["t"][i + 1] - met["t"][i]), 0, 1)[:, None, None]

    def frames(a, nd):
        a = np.asarray(a, float)[:, ::s, ::s]
        f = np.round(a[i] * (1 - w) + a[i + 1] * w, nd).reshape(len(t), -1)
        return [[float(x) if np.isfinite(x) else None for x in row] for row in f]

    x0, x1, y0, y1 = float(lons[0]), float(lons[-1]), float(lats[0]), float(lats[-1])
    step = max(x1 - x0, y1 - y0) / mask_cells
    # Клетки строго внутри сетки полей: за её краем признак «море» из течений не определён
    mx = np.minimum(x0 + step * np.arange(int((x1 - x0) / step + 1e-9) + 1), x1)
    my = np.minimum(y0 + step * np.arange(int((y1 - y0) / step + 1e-9) + 1), y1)
    LO, LA = np.meshgrid(mx, my)
    water = land.is_water(LO.ravel(), LA.ravel())
    return {
        "lons": np.round(lons[::s], 5).tolist(), "lats": np.round(lats[::s], 5).tolist(), "hours": hours,
        "currents": {"u": frames(met["cu"], 2), "v": frames(met["cv"], 2)} if currents else None,
        "wind": {"u": frames(met["wu"], 1), "v": frames(met["wv"], 1)},
        "water": {"lon0": round(x0, 5), "lat0": round(y0, 5), "step": step, "nx": len(mx), "ny": len(my),
                  "bits": "".join("1" if b else "0" for b in water)},
    }


def flow(aoi_id: str, date: str, hours: int = 72) -> dict:
    """Поля для анимации в акватории: те же течения и ветер Open-Meteo, по которым считается дрейф."""
    met = fetch_met(aoi_id, date)
    sea = AOIS[aoi_id]["kind"] == "sea"
    t0 = scene_time(aoi_id, date)
    out = flow_frames(met, t0.timestamp(), hours, LandMask(met, PROCESSED / aoi_id / "water.tif", sea), currents=sea)
    out["t0"] = t0.isoformat()
    out["sources"] = (["течения: Meteo-France SMOC через Open-Meteo (1/12°, ежечасно, с приливом и стоксовым дрейфом)"]
                      if sea else []) + ["ветер 10 м: ERA5 / прогноз через Open-Meteo (ежечасно)"]
    return out


def flow_at(lon: float, lat: float, t0: datetime, hours: int = 72) -> dict:
    """Поля для анимации вокруг точки без снимка — реанализы Copernicus Marine, как у `simulate_at`."""
    if not globe.is_ocean(lat, lon):
        raise cmems.CmemsNoData("точка на суше")
    met = cmems.fetch_met(lon, lat, t0, hours)
    out = flow_frames(met, t0.timestamp(), hours, LandMask(met))
    out["t0"] = t0.isoformat()
    out["sources"] = [str(x) for x in met["sources"]]
    return out


def accumulation(aoi_id: str, date: str, hours: int = 72, spacing_m: float = 600.0) -> dict:
    """Карта вероятных зон скопления: равномерно засеваем акваторию и смотрим, куда соберутся частицы."""
    with rasterio.open(PROCESSED / aoi_id / "water.tif") as s:
        water = s.read(1).astype(bool)
        tr, crs = s.transform, s.crs
    step = max(int(spacing_m / tr.a), 1)
    rr, cc = np.nonzero(water[::step, ::step])
    xs = tr.c + (cc * step + 0.5) * tr.a
    ys = tr.f + (rr * step + 0.5) * tr.e
    lon, lat = warp_transform(crs, "EPSG:4326", xs.tolist(), ys.tolist())
    res = simulate(aoi_id, date, lon, lat, hours=hours)
    return {"start": np.c_[lon, lat], "end": res["track"][:, -1, :], "beached": res["beached"]}
