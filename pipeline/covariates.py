"""Признаки модели концентрации — один путь кода для полевых событий (обучение) и гексов/зон (применение).

Используются только величины, доступные при реальном применении: координаты, расстояние до берега,
ветер ERA5 за сутки до момента оценки. Наблюдённые на борту волнение и ветер (sea_state_beaufort,
wind_speed_kn) в признаки не входят — при оценке по снимку их нет.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import requests
from global_land_mask import globe
from scipy.ndimage import distance_transform_edt

from .config import DATA

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
CACHE = DATA / "field" / "cache_era5.json"
MAX_COAST_KM = 300.0
GRID_RES = 1 / 60  # град, ~1,9 км

_cache: dict | None = None


def dist_coast_km(lon, lat, max_km: float = MAX_COAST_KM) -> np.ndarray:
    """Расстояние до ближайшей суши по global-land-mask (~1 км), км; дальше max_km — max_km."""
    lon = np.atleast_1d(np.asarray(lon, float))
    lat = np.atleast_1d(np.asarray(lat, float))
    out = np.full(lon.shape, max_km)
    keys = np.floor(lat / 5).astype(int) * 1000 + np.floor(lon / 5).astype(int)
    for k in np.unique(keys):
        i = np.flatnonzero(keys == k)
        la0, la1 = lat[i].min(), lat[i].max()
        lo0, lo1 = lon[i].min(), lon[i].max()
        mlat = max_km / 111.32
        mlon = max_km / (111.32 * max(np.cos(np.radians(max(abs(la0), abs(la1)))), 0.2))
        lats = np.arange(la0 - mlat, la1 + mlat + GRID_RES, GRID_RES)
        lons = np.arange(lo0 - mlon, lo1 + mlon + GRID_RES, GRID_RES)
        LA, LO = np.meshgrid(np.clip(lats, -89.99, 89.99), ((lons + 180) % 360) - 180, indexing="ij")
        land = globe.is_land(LA, LO)
        if not land.any():
            continue
        cy = np.cos(np.radians((la0 + la1) / 2))
        d = distance_transform_edt(~land, sampling=(GRID_RES * 111.32, GRID_RES * 111.32 * cy))
        r = np.clip(np.round((lat[i] - lats[0]) / GRID_RES).astype(int), 0, len(lats) - 1)
        c = np.clip(np.round((lon[i] - lons[0]) / GRID_RES).astype(int), 0, len(lons) - 1)
        out[i] = np.minimum(d[r, c], max_km)
    return out


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        _cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    return _cache


def _save_cache() -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(_cache, sort_keys=True), encoding="utf-8")


def _key(lat: float, lon: float, day: str) -> str:
    # Сетка ERA5 — 0,25°: ближе нет смысла, а кеш получается компактным
    return f"{round(lat * 4) / 4:.2f},{round(lon * 4) / 4:.2f},{day}"


def _fetch_days(points: list[tuple[float, float]], day: str) -> None:
    """Почасовой ветер 10 м за сутки day для списка точек (одним запросом, до 50 точек)."""
    cache = _load_cache()
    todo = sorted({(round(a * 4) / 4, round(b * 4) / 4) for a, b in points} - {
        tuple(map(float, k.split(",")[:2])) for k in cache if k.endswith(day)})
    for s in range(0, len(todo), 50):
        chunk = todo[s:s + 50]
        r = requests.get(ARCHIVE_URL, timeout=60, params=dict(
            latitude=",".join(f"{a:.2f}" for a, _ in chunk), longitude=",".join(f"{b:.2f}" for _, b in chunk),
            hourly="wind_speed_10m", start_date=day, end_date=day, wind_speed_unit="ms", timezone="GMT"))
        r.raise_for_status()
        js = r.json()
        js = js if isinstance(js, list) else [js]
        for (a, b), j in zip(chunk, js):
            cache[_key(a, b, day)] = j["hourly"]["wind_speed_10m"]
    if todo:
        _save_cache()


def wind24(lat, lon, t_ref) -> np.ndarray:
    """Средний ветер ERA5 10 м, м/с, за 24 ч до t_ref (datetime UTC или ISO-строка) в точках."""
    lat = np.atleast_1d(np.asarray(lat, float))
    lon = np.atleast_1d(np.asarray(lon, float))
    t = np.atleast_1d(np.asarray(t_ref, dtype=object))
    if len(t) == 1 and len(lat) > 1:
        t = np.repeat(t, len(lat))
    ts = [(_parse(x)) for x in t]
    need: dict[str, list] = {}
    for a, b, x in zip(lat, lon, ts):
        for d in {(x - timedelta(hours=24)).date(), x.date()}:
            need.setdefault(str(d), []).append((a, b))
    for day, pts in need.items():
        _fetch_days(pts, day)
    cache = _load_cache()
    out = np.full(len(lat), np.nan)
    for i, (a, b, x) in enumerate(zip(lat, lon, ts)):
        vals = []
        for h in range(24):
            tt = x - timedelta(hours=h)
            series = cache.get(_key(a, b, str(tt.date())))
            if series is not None and series[tt.hour] is not None:
                vals.append(series[tt.hour])
        if vals:
            out[i] = float(np.mean(vals))
    return out


def _parse(x) -> datetime:
    if isinstance(x, datetime):
        return x if x.tzinfo is None else x.astimezone(timezone.utc).replace(tzinfo=None)
    return datetime.fromisoformat(str(x).replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None) \
        if "+" in str(x) or str(x).endswith("Z") else datetime.fromisoformat(str(x))


def event_reference_time(t_start: str, t_end: str, time_known: bool) -> datetime:
    """Момент, к которому относится полевое событие: середина интервала или конец суток при неизвестном времени."""
    s, e = datetime.fromisoformat(t_start), datetime.fromisoformat(t_end)
    return s + (e - s) / 2 if time_known else e


def build(lon, lat, t_ref) -> dict[str, np.ndarray]:
    """Все допустимые признаки для точек (lon, lat) на моменты t_ref."""
    lon = np.atleast_1d(np.asarray(lon, float))
    lat = np.atleast_1d(np.asarray(lat, float))
    dc = dist_coast_km(lon, lat)
    return {
        "lon": lon, "lat": lat,
        "dist_coast_km": dc, "log_dist_coast_km": np.log1p(dc),
        "wind24_ms": wind24(lat, lon, t_ref),
    }
