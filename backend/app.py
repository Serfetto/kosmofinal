"""Веб-сервис: API + статика фронтенда.

uvicorn backend.app:app --port 8000
Документация API: /api/docs (Swagger), /api/redoc, /api/openapi.json; справочник ручек — docs/api.md.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache

import h3
import numpy as np
import rasterio
import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pyproj import Transformer

from backend.case_api import _aoi_date, router, versions
from backend.methodology import router as methodology_router
from backend.report import router as report_router
from backend.schemas import (PATTERN_HINTS, AccumulationOut, AoiOut, AoiPath, DatePath, DriftAtOut, DriftOut,
                             DriftPointOut, FeatureCollection, FlowOut, GeoJSONResponse, HealthOut, HoursQuery, RasterGridOut, RouteOut,
                             SceneOut, SeriesOut, StatusesOut, Tag, errors)
from pipeline import status as ST
from pipeline.aggregate import WEB
from pipeline.config import AOIS, H3_RES, PROCESSED, ROOT, cached
from pipeline.cmems import CmemsUnavailable
from pipeline.drift import accumulation, flow, flow_at, simulate, simulate_at
from pipeline.route import plan

DESCRIPTION = """
Сервис для специалиста по экологическому мониторингу: по снимкам Sentinel-2 находит зоны вероятного скопления
плавающего мусора, показывает полевые измерения и модельную концентрацию в шт./км², прогнозирует дрейф и
планирует маршрут судна. Полный справочник с примерами — `docs/api.md` в репозитории.

### С чего начать
1. `GET /api/aois` — акватории и даты снимков.
2. `GET /api/aois/{aoi}/scenes` — снимки акватории: условия съёмки и ссылки на слои.
3. `GET /api/aois/{aoi}/{date}/zones` — зоны детекции на дату.
4. `GET /api/aois/{aoi}/concentration?profile=B` — концентрация по гексам.
5. `GET /api/export` или `GET /api/report` — выгрузка GeoJSON/CSV или PDF-отчёт.

### Соглашения
- Координаты — WGS 84, порядок **[долгота, широта]**, как в GeoJSON.
- Дата снимка — `YYYY-MM-DD` по UTC; моменты времени — ISO 8601 UTC.
- Гексы — H3 разрешения 8 (~0,74 км²). Индекс гекса `i` — позиция в `hexes.geojson`; массивы `[дата][гекс]`
  в `series` и `concentration` индексируются так же.
- Три величины не смешиваются: **полевое измерение** (C = N / A), **модельная концентрация** профиля в шт./км²
  и **покрытие** по детектору в м² (м²/км²) — последнее в шт./км² не переводится.
- Статусы и их подписи — `GET /api/statuses`.

### Ошибки
Тело ошибки — `{"detail": "текст по-русски"}`. `404` — нет акватории, снимка на дату, события, гекса или
запроса. `422` — неверный параметр; для ошибок формата в ответе ещё `errors` с разбором по полям.

### Растровые слои
Файлы снимка отдаются статикой: `/data/{aoi}/{date}/rgb.jpg`, `debris.png`, `quality.png`. Растры лежат в сетке
UTM, привязка — `GET /api/aois/{aoi}/grid`; `corners` в `GET /api/aois/{aoi}/scenes` — только грубая, по углам.
"""

TAGS = [
    {"name": Tag.SERVICE, "description": "Проверка сервиса, версии моделей, словарь статусов."},
    {"name": Tag.AOIS, "description": "Акватории, даты снимков, условия съёмки, гексы и ряды по датам."},
    {"name": Tag.DETECTION, "description": "Что детектор нашёл на конкретном снимке: зоны и пиксели. "
                                           "Площадь и покрытие — в м², не в шт./км²."},
    {"name": Tag.CONCENTRATION, "description": "Модельная концентрация в шт./км² по профилям: слой по гексам, "
                                               "оценка в точке с причинами статуса."},
    {"name": Tag.FIELD, "description": "Полевые измерения кейса (C = N / A), отдельные предметы, расшифровка расчёта."},
    {"name": Tag.PAIRS, "description": "Сопоставление полевых событий со снимками Sentinel-2 и решения по каждой паре."},
    {"name": Tag.DRIFT, "description": "Прогноз дрейфа по течениям и ветру (Open-Meteo; без снимка — реанализы "
                                       "Copernicus Marine), зоны скопления, маршрут судна."},
    {"name": Tag.EXPORT, "description": "Файлы: GeoJSON, CSV и PDF-отчёт."},
    {"name": Tag.QUERIES, "description": "Сохранение выгрузки и проверка, что она воспроизводится побайтно (sha256)."},
    {"name": Tag.METRICS, "description": "Результаты проверок детектора, моделей концентрации и прогноза дрейфа; "
                                         "методика с формулами."},
]

app = FastAPI(title="AquaFlow – мониторинг океанического пластика", version="1.0.0", description=DESCRIPTION,
              openapi_tags=TAGS, docs_url="/api/docs", redoc_url="/api/redoc", openapi_url="/api/openapi.json")
FRONTEND_DIST = ROOT / "frontend" / "dist"

WHERE = {"path": "путь", "query": "параметр запроса", "body": "тело запроса"}
BOUND = {"greater_than_equal": "≥", "greater_than": ">", "less_than_equal": "≤", "less_than": "<"}


def _explain(e: dict) -> str:
    """Ошибка валидации pydantic → строка по-русски: «date (путь): '2026-99' — ожидается дата в формате YYYY-MM-DD»."""
    loc, t, ctx = e.get("loc", ()), e.get("type"), e.get("ctx") or {}
    if t == "json_invalid":
        return "тело запроса: некорректный JSON"
    name = ".".join(str(x) for x in loc[1:]) or "тело запроса"
    where = WHERE.get(loc[0], str(loc[0])) if loc else ""
    if t == "missing":
        why = "обязательный параметр не передан"
    elif t == "string_pattern_mismatch":
        why = f"{e.get('input')!r} — ожидается {PATTERN_HINTS.get(ctx.get('pattern'), 'шаблон ' + str(ctx.get('pattern')))}"
    elif t in ("int_parsing", "float_parsing", "int_from_float"):
        why = f"{e.get('input')!r} — ожидается число"
    elif t in BOUND:
        why = f"{e.get('input')!r} — должно быть {BOUND[t]} {next(iter(ctx.values()), '')}"
    else:
        why = e.get("msg", "неверное значение")
    return f"{name} ({where}): {why}"


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    errs = exc.errors()
    return JSONResponse(status_code=422, content={"detail": "; ".join(_explain(e) for e in errs),
                                                  "errors": jsonable_encoder(errs)})


@app.exception_handler(requests.RequestException)
async def weather_unavailable(request: Request, exc: requests.RequestException):
    """Дрейф, зоны скопления и маршрут скачивают течения и ветер из Open-Meteo; его сбой или лимит — не 500."""
    code = getattr(exc.response, "status_code", None)
    why = "ограничил частоту запросов" if code == 429 else "не ответил"
    return JSONResponse(status_code=503, headers={"Retry-After": "60"},
                        content={"detail": f"сервис погоды Open-Meteo {why}, повторите запрос через минуту"})


@app.exception_handler(CmemsUnavailable)
async def ocean_unavailable(request: Request, exc: CmemsUnavailable):
    """Дрейф без снимка берёт поля из Copernicus Marine: нет аккаунта или сервис не ответил — 503, нет данных — 404."""
    headers = {"Retry-After": "60"} if exc.status == 503 else None
    return JSONResponse(status_code=exc.status, headers=headers, content={"detail": str(exc)})


def _json(path):
    if not path.exists():
        raise HTTPException(404, f"нет данных: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------- служебное ----------

DETECTION_MEANING = {
    ST.DETECTED: "детектор нашёл плавающий мусор хотя бы в одном пикселе",
    ST.NOT_DETECTED: "мусора не видно, и снимок это позволяет утверждать: видно ≥50% воды, нет блика, море спокойное",
    ST.INSUFFICIENT: "облака, блик, шторм или лёд — отсутствие мусора подтвердить нельзя",
}
CONCENTRATION_MEANING = {
    ST.MODEL: "модель профиля в области своих обучающих данных",
    ST.RESEARCH: "перенос модели не подтверждён: дальше 60 км от полевых измерений, другой сезон "
                 "или признаки вне диапазона обучения",
    ST.UNAVAILABLE: "профиль к этому месту не применим: другой бассейн или пресные воды",
}
VALUE_TYPE_MEANING = {
    "measurement": "полевое измерение: C = N / A по полосе учёта",
    "model_estimate": "модельная оценка профиля",
    "research_estimate": "исследовательская оценка — перенос не подтверждён",
    "unavailable": "оценки нет",
}


@app.get("/api/health", tags=[Tag.SERVICE], summary="Сервис жив", response_model=HealthOut)
def health():
    """Для мониторинга и проверки развёртывания: сколько акваторий с готовыми данными и какие версии
    конфигов и моделей сейчас работают."""
    return {"status": "ok", "aois": sum((WEB / k / "series.json").exists() for k in AOIS), "versions": versions()}


@app.get("/api/statuses", tags=[Tag.SERVICE], summary="Словарь статусов и кодов", response_model=StatusesOut)
def statuses():
    """Все статусы, которые встречаются в ответах: машинное значение, числовой код в компактных массивах,
    подпись по-русски и смысл. Плюс коды маски качества снимка (quality.png) с цветами."""
    return {
        "detection": [{"id": k, "code": ST.DETECTION_CODE[k], "label": v, "meaning": DETECTION_MEANING[k]}
                      for k, v in ST.DETECTION.items()],
        "concentration": [{"id": k, "code": ST.CONCENTRATION_CODE[k], "label": v, "meaning": CONCENTRATION_MEANING[k]}
                          for k, v in ST.CONCENTRATION.items()],
        "value_type": [{"id": k, "code": None, "label": v, "meaning": VALUE_TYPE_MEANING[k]}
                       for k, v in ST.VALUE_TYPE.items()],
        "quality": [{"code": c, "id": k, "label": label, "rgba": list(rgba)} for c, (k, label, rgba) in ST.QUALITY.items()],
    }


# ---------- акватории ----------

def _aoi_info(k: str) -> dict:
    a, s = AOIS[k], _json(WEB / k / "series.json")
    conc = {}
    for c in sorted((WEB / k).glob("conc_*.json")):
        j = _json(c)
        conc[j["profile"]] = {"available": j["available"], "reason": j.get("reason")}
    return {"id": k, "name": a["name"], "bbox": a["bbox"], "kind": a["kind"], "port": a["port"], "tz": a["tz"],
            "rivers": a["rivers"], "note": a.get("note"), "group": a.get("group", "monitoring"),
            "source": a.get("source"), "dates": s["dates"],
            "basin": s.get("basin"), "water_type": s.get("water_type"), "concentration": conc,
            "total_area": [sc["area_m2"] for sc in s["scenes"]]}


@app.get("/api/aois", tags=[Tag.AOIS], summary="Список акваторий", response_model=list[AoiOut])
def aois():
    """Все акватории с готовыми данными: границы, порт и устья рек, даты обработанных снимков, доступность
    концентрации по профилям и суммарное покрытие мусором на каждую дату. Отсюда берутся `aoi` и `date`
    для остальных ручек.

    `group: field` — районы полевых данных кейса: снимки подобраны на даты измерений источника `source`;
    `group: monitoring` — акватории оперативного мониторинга."""
    return [_aoi_info(k) for k in AOIS if (WEB / k / "series.json").exists()]


@app.get("/api/aois/{aoi}", tags=[Tag.AOIS], summary="Одна акватория", response_model=AoiOut,
         responses=errors(404, 422))
def aoi_one(aoi: AoiPath):
    """То же, что элемент списка `GET /api/aois`."""
    _aoi_date(aoi)
    return _aoi_info(aoi)


def _layers(aoi: str, date: str) -> dict:
    return {"rgb": f"/data/{aoi}/{date}/rgb.jpg", "debris": f"/data/{aoi}/{date}/debris.png",
            "quality": f"/data/{aoi}/{date}/quality.png", "zones": f"/api/aois/{aoi}/{date}/zones",
            "points": f"/api/aois/{aoi}/{date}/points", "report": f"/api/report?aoi={aoi}&date={date}"}


@app.get("/api/aois/{aoi}/scenes", tags=[Tag.AOIS], summary="Снимки акватории", response_model=list[SceneOut],
         responses=errors(404, 422))
def scenes(aoi: AoiPath):
    """По каждой дате: сцена Sentinel-2, момент съёмки, видимость воды, ветер и состояние моря, надёжность
    сцены, число детекций и зон, покрытие, доли маски качества, число гексов по статусам и ссылки на слои —
    растры, зоны, пиксели и PDF-отчёт."""
    _aoi_date(aoi)
    return [{**sc, "layers": _layers(aoi, sc["date"])} for sc in _json(WEB / aoi / "series.json")["scenes"]]


@app.get("/api/aois/{aoi}/hexes", tags=[Tag.AOIS], summary="Сетка гексов H3",
         response_class=GeoJSONResponse, responses={200: {"model": FeatureCollection}, **errors(404, 422)})
def hexes(aoi: AoiPath):
    """Гексы H3 (разрешение 8) по воде акватории, полигоны. Атрибуты: `i` — индекс гекса в массивах `series`
    и `concentration`, `h3`, центр `lon`/`lat`, `water_km2`, `dist_coast_km`, `n_obs` — число надёжных
    наблюдений, `persistence` — доля наблюдений с мусором, `mean_cover`/`max_cover` — покрытие, м²/км²,
    `trend_cover` — наклон покрытия, м²/км² в месяц, `hot` — индекс устойчивого скопления
    (persistence × ln(1 + mean_cover))."""
    _aoi_date(aoi)
    return FileResponse(WEB / aoi / "hexes.geojson", media_type="application/geo+json")


@app.get("/api/aois/{aoi}/series", tags=[Tag.AOIS], summary="Ряды по датам и гексам",
         responses={200: {"model": SeriesOut}, **errors(404, 422)})
def series(aoi: AoiPath):
    """Всё по датам одним файлом: метаданные снимков (`scenes`) и массивы `[дата][гекс]` — покрытие, число
    пикселей с детекцией, доля пригодной воды и код статуса детекции. Для графиков динамики и анимации."""
    _aoi_date(aoi)
    return FileResponse(WEB / aoi / "series.json", media_type="application/json")


GRID_STEP = 256  # пикс.: билинейно между узлами через 2,56 км ошибка — доли метра


@lru_cache(maxsize=16)
def _raster_grid(aoi: str) -> dict:
    path = PROCESSED / aoi / "water.tif"
    if not path.exists():
        raise HTTPException(404, f"нет сетки растров акватории {aoi!r}")
    with rasterio.open(path) as s:
        t, crs, w, h = s.transform, s.crs, s.width, s.height
    # Узлы с запасом за правый и нижний край: rgb и quality — через пиксель и выходят за сетку на полпикселя
    cols = np.arange(-(-(w + 1) // GRID_STEP) + 1) * GRID_STEP
    rows = np.arange(-(-(h + 1) // GRID_STEP) + 1) * GRID_STEP
    x, y = t @ tuple(np.meshgrid(cols, rows))
    lon, lat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(x, y)
    return {"width": w, "height": h, "step": GRID_STEP,
            "lonlat": np.stack([lon, lat], -1).round(7).tolist()}


@app.get("/api/aois/{aoi}/grid", tags=[Tag.AOIS], summary="Привязка растров снимка", response_model=RasterGridOut,
         responses=errors(404, 422))
def raster_grid(aoi: AoiPath):
    """Растры `rgb.jpg`, `debris.png`, `quality.png` лежат в сетке UTM 10 м (rgb и quality — через пиксель).
    В меркаторе карты эта сетка не прямоугольник и не трапеция: если растянуть растр целиком по четырём
    `corners`, середина снимка уезжает до 100 м. Здесь узлы сетки через `step` пикселей: растр режется на куски,
    углы каждого куска берутся билинейно между узлами, куски ложатся на карту с точностью до пары метров.
    Сетка одна на все даты акватории."""
    _aoi_date(aoi)
    return _raster_grid(aoi)


@app.get("/api/aois/{aoi}/{date}/points", tags=[Tag.DETECTION], summary="Пиксели с детекцией",
         responses={200: {"model": list[list[float]]}, **errors(404, 422)})
def points(aoi: AoiPath, date: DatePath):
    """Каждый пиксель 10 м с детекцией — `[lon, lat, p, frac]`: центр пикселя, вероятность мусора и доля
    пикселя, занятая мусором (эквивалентная площадь = frac × 100 м²). Затравки для прогноза дрейфа."""
    _aoi_date(aoi, date)
    return FileResponse(WEB / aoi / date / "points.json", media_type="application/json")


# ---------- дрейф и маршрут ----------

@lru_cache(maxsize=64)
def _drift_all(aoi: str, date: str, hours: int, max_seeds: int = 300, n_ens: int = 4):
    pts = np.asarray(_json(WEB / aoi / date / "points.json"), float).reshape(-1, 4)
    if len(pts) == 0:
        return {"frames": [], "hexes": {}, "n": 0}
    if len(pts) > max_seeds:
        pts = pts[np.argsort(-pts[:, 2] * pts[:, 3])[:max_seeds]]
    res = simulate(aoi, date, pts[:, 0], pts[:, 1], hours=hours, n_ens=n_ens, seed=1)
    tr = res["track"]
    weight = np.repeat(pts[:, 2] * pts[:, 3] * 100 / n_ens, n_ens)  # м² на частицу
    snap = {}
    for h in (24, 48, hours):
        if h > hours:
            continue
        cells = {}
        for (lon, lat), w in zip(tr[:, h], weight):
            c = h3.latlng_to_cell(float(lat), float(lon), H3_RES)
            cells[c] = cells.get(c, 0.0) + float(w)
        snap[str(h)] = cells
    return {
        "n": int(tr.shape[0]), "hours": hours, "t0": res["t0"],
        "frames": np.round(tr.astype(float), 5).transpose(1, 0, 2).tolist(),  # [hour][particle][lon,lat]
        "beached": res["beached"].astype(int).tolist(),
        "hexes": snap,
    }


@app.get("/api/aois/{aoi}/{date}/drift", tags=[Tag.DRIFT], summary="Прогноз дрейфа всех детекций",
         responses={200: {"model": DriftOut}, **errors(404, 422, 503)})
def drift_all(aoi: AoiPath, date: DatePath, hours: HoursQuery = 72):
    """Куда унесёт обнаруженный мусор: ансамбль частиц (до 300 самых крупных детекций × 4 члена ансамбля)
    по течениям и ветру с момента съёмки. Кадры по часам — для анимации, распределение по гексам на 24, 48 ч
    и на горизонт — в м² мусора. Первый запрос на дату считается несколько секунд и скачивает погоду
    из Open-Meteo, дальше — из кеша."""
    _aoi_date(aoi, date)
    return _drift_all(aoi, date, hours)


@app.get("/api/aois/{aoi}/{date}/drift_point", tags=[Tag.DRIFT], summary="Дрейф из точки (конус неопределённости)",
         response_model=DriftPointOut, responses=errors(404, 422, 503))
def drift_point(aoi: AoiPath, date: DatePath,
                lon: float = Query(ge=-180, le=180, description="Долгота точки старта", examples=[39.72]),
                lat: float = Query(ge=-90, le=90, description="Широта точки старта", examples=[43.55]),
                hours: HoursQuery = 72, n: int = Query(40, ge=1, le=200, description="Членов ансамбля")):
    """Ансамбль траекторий из одной точки с разбросом парусности и турбулентной диффузией: треки, центр
    ансамбля и его разброс по часам (ширина конуса), доля выброшенных на берег."""
    _aoi_date(aoi, date)
    return _cone(simulate(aoi, date, [lon], [lat], hours=hours, n_ens=n, seed=2), lat)


def _cone(res: dict, lat: float) -> dict:
    tr = res["track"]
    center = tr.mean(0)
    spread = np.sqrt(((tr - center[None]) ** 2 * np.array([np.cos(np.radians(lat)) ** 2, 1.0])).sum(-1).mean(0)) * 111.32
    return {"tracks": np.round(tr.astype(float), 5).tolist(), "center": np.round(center.astype(float), 5).tolist(),
            "spread_km": np.round(spread, 2).tolist(), "beached_frac": float(res["beached"].mean()), "t0": res["t0"]}


@app.get("/api/drift", tags=[Tag.DRIFT], summary="Дрейф из точки без снимка (реанализ Copernicus Marine)",
         response_model=DriftAtOut, responses=errors(404, 422, 503))
def drift_at(lon: float = Query(ge=-180, le=180, description="Долгота точки старта", examples=[-139.6]),
             lat: float = Query(ge=-80, le=80, description="Широта точки старта", examples=[31.9]),
             t0: datetime = Query(description="Момент старта, ISO 8601; без часового пояса — UTC",
                                  examples=["2015-07-27T15:34:00Z"]),
             hours: HoursQuery = 72, n: int = Query(40, ge=1, le=200, description="Членов ансамбля")):
    """Тот же ансамбль, что `drift_point`, но без привязки к акватории и снимку: из любой морской точки
    с любого момента — например, из места полевого измерения S1–S3 (2014–2016 гг.), где снимков нет, а течений
    Open-Meteo на эти годы тоже нет. Поля — реанализы Copernicus Marine: течения GLORYS12 (1/12°, среднесуточные;
    на шельфе Северного моря — NWS, ежечасные с приливом) + стоксов дрейф волн + ветер 10 м (L4). Это реконструкция
    задним числом: второго наблюдения того же мусора нет, точность не проверена.

    Нужен аккаунт Copernicus Marine: COPERNICUSMARINE_SERVICE_USERNAME и _PASSWORD в `.env` или в окружении,
    без них — 503. Поля скачиваются на первый запрос (до минуты) и кешируются: соседние точки того же дня
    считаются из кеша. Нет реанализа на эти даты или точка на суше — 404."""
    t0 = t0.replace(tzinfo=timezone.utc) if t0.tzinfo is None else t0.astimezone(timezone.utc)
    res = simulate_at(lon, lat, t0, hours=hours, n_ens=n, seed=2)
    return {**_cone(res, lat), "sources": res["sources"]}


@app.get("/api/aois/{aoi}/{date}/flow", tags=[Tag.DRIFT], summary="Течения и ветер по часам (для анимации)",
         response_model=FlowOut, responses=errors(404, 422, 503))
def flow_aoi(aoi: AoiPath, date: DatePath, hours: HoursQuery = 72):
    """Поля, по которым считается дрейф в акватории, — чтобы показать их на карте: течения и ветер 10 м на сетке
    вокруг акватории, кадры через 1 ч от момента съёмки. Кадр `h` соответствует часу `h` прогноза дрейфа.
    Маска воды — где рисовать течения (по маске снимка, за её краем — по глобальной маске суши).
    У внутренних водоёмов `currents` = null: течений нет."""
    _aoi_date(aoi, date)
    return _flow_aoi(aoi, date, hours)


@lru_cache(maxsize=16)
def _flow_aoi(aoi: str, date: str, hours: int):
    return flow(aoi, date, hours)


@app.get("/api/flow", tags=[Tag.DRIFT], summary="Течения и ветер вокруг точки без снимка (для анимации)",
         response_model=FlowOut, responses=errors(404, 422, 503))
def flow_point(lon: float = Query(ge=-180, le=180, description="Долгота точки", examples=[-139.6]),
               lat: float = Query(ge=-80, le=80, description="Широта точки", examples=[31.9]),
               t0: datetime = Query(description="Кадр 0, ISO 8601; без часового пояса — UTC",
                                    examples=["2015-07-27T15:34:00Z"]),
               hours: HoursQuery = 72):
    """Поля, по которым считается `/api/drift`: реанализы Copernicus Marine в той же рамке вокруг точки
    (сетка прорежена до 32 узлов по большей стороне). Нужен аккаунт Copernicus Marine, как у `/api/drift`."""
    t0 = t0.replace(tzinfo=timezone.utc) if t0.tzinfo is None else t0.astimezone(timezone.utc)
    return _flow_at(lon, lat, t0, hours)


@lru_cache(maxsize=16)
def _flow_at(lon: float, lat: float, t0: datetime, hours: int):
    return flow_at(lon, lat, t0, hours)


@lru_cache(maxsize=32)
def _accum(aoi: str, date: str):
    cache = cached(WEB / aoi / date / "accumulation.json")
    if cache.exists():
        return _json(cache)
    a = accumulation(aoi, date)
    start, end = {}, {}
    for lon, lat in a["start"]:
        c = h3.latlng_to_cell(float(lat), float(lon), H3_RES)
        start[c] = start.get(c, 0) + 1
    for lon, lat in a["end"]:
        c = h3.latlng_to_cell(float(lat), float(lon), H3_RES)
        end[c] = end.get(c, 0) + 1
    mean_start = np.mean(list(start.values()))
    factor = {c: round(n / mean_start, 3) for c, n in end.items()}
    out = {"factor": factor, "beached_frac": round(float(a["beached"].mean()), 3), "n": len(a["start"])}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out), encoding="utf-8")
    return out


@app.get("/api/aois/{aoi}/{date}/accumulation", tags=[Tag.DRIFT], summary="Зоны вероятного скопления течениями",
         response_model=AccumulationOut, responses=errors(404, 422, 503))
def accum(aoi: AoiPath, date: DatePath):
    """Куда течения и ветер сгоняют плавающий мусор независимо от детекций: частицы засеваются равномерно
    по воде (шаг 600 м) и прогоняются 72 ч. `factor` > 1 — в гексе собирается больше частиц, чем в среднем
    было на старте. Первый расчёт на дату занимает до минуты, результат кешируется."""
    _aoi_date(aoi, date)
    return _accum(aoi, date)


@app.get("/api/aois/{aoi}/{date}/route", tags=[Tag.DRIFT], summary="Маршрут судна-сборщика",
         response_model=RouteOut, responses=errors(404, 422, 503))
def route(aoi: AoiPath, date: DatePath,
          n: int = Query(8, ge=1, le=20, description="Максимум остановок"),
          speed: float = Query(12, gt=1, le=40, description="Скорость судна, узлы"),
          delay: float = Query(6, ge=0, le=48, description="Выход из порта через столько часов после съёмки")):
    """Жадный маршрут из порта за 12-часовую смену: на каждом шаге — цель с наибольшим покрытием на час пути,
    с учётом того, куда её снесёт к моменту прибытия. Возвращает остановки с наблюдённой и прогнозной позицией,
    временем прибытия и длиной переходов, линию маршрута и сводку. Если детекций нет — пустой `stops` и `note`."""
    _aoi_date(aoi, date)
    return plan(aoi, date, n_stops=n, speed_kn=speed, delay_h=delay)


# Additional case-study and report endpoints must be registered before the
# catch-all frontend mount. Otherwise requests such as /api/profiles are
# handled by StaticFiles and return 404 even though the routes are defined.
app.include_router(router)
app.include_router(report_router)
app.include_router(methodology_router)

app.mount("/data", StaticFiles(directory=WEB), name="data")
app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True, check_dir=False), name="frontend")
