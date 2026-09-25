"""API кейса: профили концентрации, полевые наблюдения, зоны, реестр пар, выгрузка, сохранённые запросы.

Все ответы строятся из подготовленных файлов (data/field, data/registry, data/web, data/eval) без случайности,
поэтому одинаковый запрос даёт тот же результат — это проверяется повтором сохранённого запроса по sha256.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np
from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import Response

from backend.schemas import (DATE_RE, PROFILE_RE, AoiPath, AoiQuery, ConcentrationLayerOut, DatePath, DateQuery,
                             FeatureCollection, FieldEventOut, GeoJSONResponse, HexEstimateOut, MetricsOut, PairsOut,
                             ProfileOut, ProfileQuery, QueryIdPath, QueryIn, RerunOut, SavedQueryOut, Tag, errors)
from pipeline import status as ST
from pipeline.aggregate import WEB
from pipeline.config import AOIS, DATA, MODELS
from pipeline.provenance import config_hash, load_yaml

router = APIRouter()
FIELD = DATA / "field"
REG = DATA / "registry"
EVAL = DATA / "eval"
QUERIES = DATA / "queries"

ZONE_COLUMNS = [
    "zone_id", "aoi", "date", "scene_id", "scene_datetime_utc", "lon", "lat", "h3", "zone_area_km2", "n_pixels",
    "cover_m2", "cover_m2_km2", "p_mean", "p_max", "detection_status", "detection_status_ru",
    "profile", "profile_label", "size_class", "unit", "conc_items_km2", "conc_lo80", "conc_hi80", "conc_lo95",
    "conc_hi95", "conc_status", "conc_status_ru", "conc_reasons", "model_version", "valid_frac_scene", "wind_ms",
    "sea", "field_event_id", "field_profile", "field_date", "field_distance_km", "field_date_gap_days",
    "field_conc_items_km2",
]
HEX_COLUMNS = [
    "aoi", "date", "scene_id", "h3", "hex_index", "lon", "lat", "water_km2", "valid_frac", "detection_status",
    "detection_status_ru", "n_det_px", "cover_m2_km2", "profile", "unit", "conc_items_km2", "conc_lo80",
    "conc_hi80", "conc_status", "conc_status_ru", "model_version",
]


def _read(path):
    if not path.exists():
        raise HTTPException(404, f"нет данных: {path.relative_to(DATA)}")
    return json.loads(path.read_text(encoding="utf-8"))


def _profiles() -> dict:
    return load_yaml("profiles.yaml")["profiles"]


def _profile(pid: str) -> dict:
    p = _profiles().get(pid)
    if p is None:
        raise HTTPException(422, f"неизвестный профиль {pid}; доступны: {', '.join(_profiles())}")
    return p


def _aoi_date(aoi: str, date: str | None = None):
    if aoi not in AOIS or not (WEB / aoi / "series.json").exists():
        raise HTTPException(404, f"акватория {aoi!r} не найдена")
    if date is not None:
        if not re.match(DATE_RE, date):
            raise HTTPException(422, "дата должна быть в формате YYYY-MM-DD")
        if not (WEB / aoi / date / "zones.geojson").exists():
            raise HTTPException(404, f"нет обработанного снимка {aoi} на {date}")


def versions() -> dict:
    out = {"configs": {n: config_hash(n) for n in ("profiles.yaml", "concentration.yaml", "detector.yaml", "pairs.yaml")}}
    for pid in _profiles():
        p = MODELS / f"conc_{pid}.json"
        if p.exists():
            out[f"conc_{pid}"] = json.loads(p.read_text(encoding="utf-8"))["version"]
    return out


# ---------- профили и полевые данные ----------

@router.get("/api/profiles", tags=[Tag.CONCENTRATION], summary="Профили концентрации",
            response_model=list[ProfileOut])
def profiles():
    """Что именно считается в шт./км²: профиль задаёт материал, класс размера, метод полевого учёта и бассейны,
    где модель применима.

    У обученных профилей — версия и тип модели, объём обучения и MAE на отложенной выборке рядом с базовыми
    моделями (медиана, IDW). Профили — разные совокупности: складывать и сравнивать их значения нельзя.
    `show_in_ui: false` — профиль есть в реестре, но модели для него нет.
    """
    out = []
    for pid, p in _profiles().items():
        m = MODELS / f"conc_{pid}.json"
        met = EVAL / "concentration" / f"{pid}_metrics.json"
        info = {"id": pid, **{k: p.get(k) for k in ("label", "material", "size_class", "method", "unit", "primary")},
                "code": p["id"], "show_in_ui": p.get("show_in_ui", True), "basins": p["domain"]["basins"]}
        if m.exists():
            j = json.loads(m.read_text(encoding="utf-8"))
            info.update(model_version=j["version"], model_type=j["serve"]["type"],
                        n_train=len(j["trained_on"]), n_holdout=len(j["holdout"]))
        if met.exists():
            r = json.loads(met.read_text(encoding="utf-8"))
            info["holdout_mae"] = {k: round(v["mae"], 1) for k, v in r["holdout"].items()}
        out.append(info)
    return out


@lru_cache(maxsize=1)
def _field_events() -> dict:
    return _read(FIELD / "events.geojson")


@router.get("/api/field", tags=[Tag.FIELD], summary="Полевые измерения концентрации",
            response_class=GeoJSONResponse, responses={200: {"model": FeatureCollection}, **errors(422)})
def field(profile: str | None = Query(None, pattern=PROFILE_RE, description="Только измерения этого профиля",
                                      examples=["B"]),
          bbox: str | None = Query(None, description="Только внутри рамки `lon_min,lat_min,lon_max,lat_max`",
                                   examples=["27.0,40.5,42.0,47.0"]),
          date_from: str | None = Query(None, pattern=DATE_RE, description="С даты включительно, YYYY-MM-DD"),
          date_to: str | None = Query(None, pattern=DATE_RE, description="По дату включительно, YYYY-MM-DD")):
    """Полевые измерения из реестра кейса (`value_type = measurement`), GeoJSON FeatureCollection.

    Геометрия — полоса учёта (`LineString`) или точка, если трек в источнике не опубликован.
    Главные атрибуты: `event_id`, `profile`, `role` (train — обучение модели, transfer_check — только проверка
    переноса), `date_utc`, `t_start_utc`/`t_end_utc`, `length_km`, `width_m`, `area_km2` (A), `n_items` (N),
    `conc_items_km2` (C = N / A), `conc_lo95`/`conc_hi95` (95% ДИ по Гарвуду), `conc_source` (`n_over_a` — посчитано
    из N и A, иначе опубликованное значение), `source_doi`, `source_license`.

    Пустая выборка — пустая коллекция, не ошибка. Расшифровка расчёта — `GET /api/field/{event_id}`.
    """
    fc = _field_events()
    box = None
    if bbox:
        try:
            box = [float(v) for v in bbox.split(",")]
            assert len(box) == 4 and box[0] < box[2] and box[1] < box[3]
        except (ValueError, AssertionError):
            raise HTTPException(422, "bbox: четыре числа lon_min,lat_min,lon_max,lat_max")
    feats = []
    for f in fc["features"]:
        p = f["properties"]
        if profile and p["profile"] != profile:
            continue
        if date_from and p["date_utc"] < date_from or date_to and p["date_utc"] > date_to:
            continue
        if box and not (box[0] <= p["lon"] <= box[2] and box[1] <= p["lat"] <= box[3]):
            continue
        feats.append(f)
    return GeoJSONResponse({"type": "FeatureCollection", "features": feats})


@router.get("/api/field/objects", tags=[Tag.FIELD], summary="Отдельные предметы (контекст)",
            response_class=GeoJSONResponse, responses={200: {"model": FeatureCollection}})
def field_objects():
    """Объектные записи реестра: где и какой предмет видели (`item_type`, `category`, `material`), точки.

    Это контекст для карты, а не метки концентрации: по отдельным предметам C = N / A не считается.
    `value_type = object_context`.
    """
    return GeoJSONResponse(_read(FIELD / "objects.geojson"))


@router.get("/api/field/{event_id}", tags=[Tag.FIELD], summary="Расшифровка полевого события",
            responses={200: {"model": FieldEventOut}, **errors(404, 422)})
def field_event(event_id: str = Path(description="id события — `event_id` из `GET /api/field`",
                                     examples=["S3:HE419_MarLitter_transect01"])):
    """Как получено число по событию: все строки реестра и решение по каждой (взята / отброшена и почему),
    принятые измерения с формулой `C = N / A` и пары события со снимками Sentinel-2 с решением по каждой.

    Для проверки конкретного расчёта без чтения кода.
    """
    import pandas as pd

    sel = pd.read_csv(FIELD / "selection.csv", keep_default_na=False)
    rows = sel[sel["event_id"] == event_id]
    if rows.empty:
        raise HTTPException(404, f"событие {event_id} не найдено")
    ev = [f["properties"] for f in _field_events()["features"] if f["properties"]["event_id"] == event_id]
    pairs = []
    if (REG / "pairs.csv").exists():
        reg = pd.read_csv(REG / "pairs.csv")
        pr = reg[reg["event_id"] == event_id]
        cols = ["scene_id", "scene_datetime_utc", "sync_tier", "dt_min_h", "dt_max_h", "drift_buffer_km",
                "footprint_coverage", "clear_water_frac", "glint_b11", "decision", "reason_code", "reason_text"]
        pairs = json.loads(pr[[c for c in cols if c in pr]].to_json(orient="records", force_ascii=False))
    for e in ev:
        if e["conc_source"] == "n_over_a":
            e["formula"] = f"C = N / A = {e['n_items']:g} / {e['area_km2']:g} км² = {e['conc_items_km2']:.2f} шт./км²"
        else:
            e["formula"] = f"C = {e['conc_items_km2']:g} шт./км² — опубликованная оценка (N и A в источнике нет)"
    return {"event_id": event_id, "rows": rows.to_dict("records"), "measurements": ev, "pairs": pairs}


# ---------- реестр пар ----------

@router.get("/api/pairs", tags=[Tag.PAIRS], summary="Реестр пар «событие ↔ снимок»",
            responses={200: {"model": PairsOut, "content": {"text/csv": {}}}, **errors(404, 422)})
def pairs(format: str = Query("json", pattern="^(json|csv)$",
                              description="`json` — сводка и строки с признаками детектора; `csv` — файл pairs.csv")):
    """Для каждого полевого события — найденные снимки Sentinel-2 и решение: пара принята, контекст или отклонена.

    Главные поля строки: `pair_id`, `event_id`, `profile`, `scene_id`, `dt_min_h`/`dt_max_h` (разница во времени
    с наблюдением, ч), `sync_tier`, `drift_buffer_km`, `footprint_coverage`, `clear_water_frac`, `glint_b11`,
    `decision` (accepted / context / rejected), `reason_code` и `reason_text`. У принятых пар — признаки детектора
    в следе: `det_px`, `det_per_km2`, `n_zones`, `cover_m2_km2`.

    404 — реестр ещё не построен (`python -m pipeline.pairs build`).
    """
    p = REG / "pairs.csv"
    if not p.exists():
        raise HTTPException(404, "реестр пар не построен: python -m pipeline.pairs build")
    if format == "csv":
        return Response(p.read_bytes(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=pairs.csv"})
    import pandas as pd

    reg = pd.read_csv(p).drop(columns=["footprint_wkt"], errors="ignore")
    feats = REG / "pair_features.csv"
    if feats.exists():
        reg = reg.merge(pd.read_csv(feats).drop(columns=["event_id", "scene_id"]), on="pair_id", how="left")
    return {"summary": _read(REG / "summary.json"), "rows": json.loads(reg.to_json(orient="records", force_ascii=False))}


# ---------- зоны, концентрация, выгрузка ----------

@router.get("/api/aois/{aoi}/concentration", tags=[Tag.CONCENTRATION], summary="Слой концентрации по всем датам",
            responses={200: {"model": ConcentrationLayerOut}, **errors(404, 422)})
def concentration_layer(aoi: AoiPath, profile: ProfileQuery = "B"):
    """Модельная концентрация профиля в центре каждого гекса на момент каждого снимка, шт./км², с 80%-интервалом
    и статусом. Компактно: массивы `[дата][гекс]`, индекс гекса — `properties.i` из `GET /api/aois/{aoi}/hexes`,
    индекс даты — позиция в `dates`.

    Если профиль к акватории не применим (другой бассейн, пресные воды) — `available: false`, `reason` и без
    массивов: это не ошибка. Концентрация — модель по полевым данным, из площади детекций она не выводится.
    """
    _aoi_date(aoi)
    _profile(profile)
    return _read(WEB / aoi / f"conc_{profile}.json")


@router.get("/api/aois/{aoi}/{date}/zones", tags=[Tag.DETECTION], summary="Зоны детекции на снимке",
            response_class=GeoJSONResponse, responses={200: {"model": FeatureCollection}, **errors(404, 422)})
def zones(aoi: AoiPath, date: DatePath):
    """Зоны вероятного скопления плавающего мусора — связные группы пикселей 10 м, где детектор видит мусор.
    GeoJSON FeatureCollection, полигоны.

    Атрибуты зоны:
    - `zone_id`, `aoi`, `date`, `scene_id`, `scene_datetime_utc`, `h3` — ячейка, где центр зоны;
    - `lon`, `lat` — центр зоны; `n_pixels`, `zone_area_km2` — размер зоны;
    - `cover_m2` — эквивалентная площадь мусора в зоне, м²; `cover_m2_km2` — она же на км² зоны;
    - `p_mean`, `p_max` — вероятность мусора по пикселям зоны;
    - `detection_status` — всегда `detected`; `valid_frac_scene`, `wind_ms`, `sea`, `scene_reliable` — условия съёмки;
    - `field_event_id`, `field_distance_km`, `field_date_gap_days`, `field_conc_items_km2` — ближайшее полевое измерение;
    - `conc_<профиль>_items_km2`, `_lo80`/`_hi80`, `_lo95`/`_hi95`, `_status`, `_status_ru`, `_reasons`, `_model` —
      модельная концентрация профиля в точке зоны, шт./км².

    Площадь и покрытие — показатели детектора, в шт./км² они не переводятся.
    """
    _aoi_date(aoi, date)
    return GeoJSONResponse(_read(WEB / aoi / date / "zones.geojson"))


@router.get("/api/aois/{aoi}/{date}/hex/{i}", tags=[Tag.CONCENTRATION], summary="Оценка концентрации в гексе",
            response_model=HexEstimateOut, responses=errors(404, 422))
def hex_estimate(aoi: AoiPath, date: DatePath, profile: ProfileQuery = "B",
                 i: int = Path(ge=0, description="Индекс гекса — `properties.i` из `GET /api/aois/{aoi}/hexes`",
                               examples=[5])):
    """Концентрация профиля в центре гекса на момент снимка — с 80% и 95% интервалами, статусом и причинами
    статуса (далеко от полевых данных, другой сезон, признак вне диапазона обучения), признаками модели и
    статусом детекции того же гекса. Считается на лету той же моделью, что и слой концентрации.
    """
    from pipeline import concentration

    _aoi_date(aoi, date)
    _profile(profile)
    hexes = _read(WEB / aoi / "hexes.geojson")["features"]
    if not 0 <= i < len(hexes):
        raise HTTPException(404, f"гекс {i} не найден")
    s = _read(WEB / aoi / "series.json")
    di = s["dates"].index(date)
    h = hexes[i]["properties"]
    r = concentration.predict(profile, [h["lon"]], [h["lat"]], [s["scenes"][di]["datetime"]])
    val = lambda k: None if np.isnan(r[k][0]) else round(float(r[k][0]), 1)  # noqa: E731
    return {"aoi": aoi, "date": date, "hex": i, "h3": h["h3"], "profile": profile, "unit": "шт./км²",
            "conc_items_km2": val("value"), "lo80": val("lo80"), "hi80": val("hi80"), "lo95": val("lo95"),
            "hi95": val("hi95"), "status": r["status"][0], "status_ru": ST.CONCENTRATION[r["status"][0]],
            "reasons": r["reasons"][0], "nearest_field_km": round(float(r["nearest_field_km"][0]), 1),
            "features": {k: round(float(np.atleast_1d(v)[0]), 3) for k, v in r["features"].items()},
            "model_version": r["model_version"], "model_type": r["model_type"],
            "detection_status": s["status_codes"][str(s["status"][di][i])],
            "detection_status_ru": ST.DETECTION[s["status_codes"][str(s["status"][di][i])]],
            "cover_m2_km2": s["cover"][di][i], "valid_frac": s["valid"][di][i]}


def _zone_rows(aoi: str, date: str, profile: str) -> tuple[list[dict], list[dict]]:
    p = _profile(profile)
    fc = _read(WEB / aoi / date / "zones.geojson")
    rows, feats = [], []
    for f in fc["features"]:
        z = f["properties"]
        row = {k: z.get(k) for k in ZONE_COLUMNS if k in z}
        row.update(profile=profile, profile_label=p["label"], size_class=p["size_class"], unit="шт./км²",
                   conc_items_km2=z.get(f"conc_{profile}_items_km2"), conc_status=z.get(f"conc_{profile}_status"),
                   conc_status_ru=z.get(f"conc_{profile}_status_ru"), conc_reasons=z.get(f"conc_{profile}_reasons"),
                   model_version=z.get(f"conc_{profile}_model"),
                   **{f"conc_{b}": z.get(f"conc_{profile}_{b}") for b in ("lo80", "hi80", "lo95", "hi95")})
        rows.append({k: row.get(k) for k in ZONE_COLUMNS})
        feats.append({"type": "Feature", "geometry": f["geometry"], "properties": rows[-1]})
    return rows, feats


def _hex_rows(aoi: str, date: str, profile: str) -> tuple[list[dict], list[dict]]:
    _profile(profile)
    s = _read(WEB / aoi / "series.json")
    di = s["dates"].index(date)
    hexes = _read(WEB / aoi / "hexes.geojson")["features"]
    c = _read(WEB / aoi / f"conc_{profile}.json")
    rows, feats = [], []
    for i, h in enumerate(hexes):
        p = h["properties"]
        dst = s["status_codes"][str(s["status"][di][i])]
        if c["available"]:
            cst = c["status_codes"][str(c["status"][di][i])]
            cv, lo, hi = c["value"][di][i], c["lo80"][di][i], c["hi80"][di][i]
        else:
            cst, cv, lo, hi = ST.UNAVAILABLE, None, None, None
        row = {"aoi": aoi, "date": date, "scene_id": s["scenes"][di].get("scene_id"), "h3": p["h3"], "hex_index": i,
               "lon": p["lon"], "lat": p["lat"], "water_km2": p["water_km2"], "valid_frac": s["valid"][di][i],
               "detection_status": dst, "detection_status_ru": ST.DETECTION[dst], "n_det_px": s["ndet"][di][i],
               "cover_m2_km2": s["cover"][di][i], "profile": profile, "unit": "шт./км²", "conc_items_km2": cv,
               "conc_lo80": lo, "conc_hi80": hi, "conc_status": cst, "conc_status_ru": ST.CONCENTRATION[cst],
               "model_version": c.get("model_version")}
        rows.append(row)
        feats.append({"type": "Feature", "geometry": h["geometry"], "properties": row})
    return rows, feats


def build_export(aoi: str, date: str, profile: str, layer: str, fmt: str) -> tuple[bytes, str]:
    _aoi_date(aoi, date)
    rows, feats = (_zone_rows if layer == "zones" else _hex_rows)(aoi, date, profile)
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=ZONE_COLUMNS if layer == "zones" else HEX_COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        return buf.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8"
    doc = {"type": "FeatureCollection", "features": feats,
           "meta": {"aoi": aoi, "date": date, "profile": profile, "layer": layer, "unit": "шт./км²",
                    "note": "Концентрация — модель по полевым данным профиля; площадь зоны и покрытие — отдельные "
                            "показатели детектора и в концентрацию не переводятся.", "versions": versions()}}
    return json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8"), "application/geo+json"


EXPORT_HEADERS = {
    "Content-Disposition": {"description": "Имя файла: `<layer>_<aoi>_<date>_<profile>.<geojson|csv>`",
                            "schema": {"type": "string"}},
    "X-Result-SHA256": {"description": "sha256 тела ответа — для сверки повторного запроса", "schema": {"type": "string"}},
}


@router.get("/api/export", tags=[Tag.EXPORT], summary="Выгрузка зон или гексов в GeoJSON / CSV",
            response_class=Response, responses={
                200: {"description": "Файл выгрузки", "headers": EXPORT_HEADERS, "content": {
                    "application/geo+json": {"schema": {"$ref": "#/components/schemas/FeatureCollection"}},
                    "text/csv": {"schema": {"type": "string"}}}},
                **errors(404, 422)})
def export(aoi: AoiQuery, date: DateQuery, profile: ProfileQuery = "B",
           layer: str = Query("zones", pattern="^(zones|hexes)$",
                              description="`zones` — зоны детекции; `hexes` — все гексы акватории с концентрацией"),
           format: str = Query("geojson", pattern="^(geojson|csv)$", description="`geojson` или `csv` (UTF-8 с BOM)")):
    """Файл для ГИС и таблиц на выбранную дату и профиль. Колонки CSV и атрибуты GeoJSON одинаковые:

    - `zones`: `zone_id`, координаты, `h3`, площадь и покрытие зоны, `p_mean`/`p_max`, статус детекции, профиль,
      `conc_items_km2` с 80% и 95% интервалами, статус и причины статуса концентрации, версия модели, условия
      съёмки и ближайшее полевое измерение;
    - `hexes`: `h3`, `hex_index`, центр, площадь воды, доля пригодной воды, статус детекции, пиксели детекции,
      покрытие, `conc_items_km2` с 80%-интервалом и статус концентрации.

    В GeoJSON дополнительно `meta`: параметры, единица и версии конфигов и моделей. Одинаковый запрос даёт
    побайтно одинаковый файл — sha256 в заголовке `X-Result-SHA256`.
    """
    body, media = build_export(aoi, date, profile, layer, format)
    ext = "csv" if format == "csv" else "geojson"
    return Response(body, media_type=media, headers={
        "Content-Disposition": f"attachment; filename={layer}_{aoi}_{date}_{profile}.{ext}",
        "X-Result-SHA256": hashlib.sha256(body).hexdigest()})


# ---------- сохранённые запросы ----------

def _query_id(q: QueryIn) -> str:
    return hashlib.sha256(json.dumps(q.model_dump(), sort_keys=True).encode()).hexdigest()[:12]


@router.post("/api/queries", tags=[Tag.QUERIES], summary="Сохранить запрос выгрузки",
             response_model=SavedQueryOut, responses=errors(404, 422))
def save_query(q: QueryIn):
    """Выполняет выгрузку с этими параметрами (как `GET /api/export`) и сохраняет параметры, sha256 результата
    и версии конфигов и моделей. id — первые 12 символов sha256 от параметров, поэтому тот же запрос получает
    тот же id; уже сохранённый запрос не перезаписывается — исходный отпечаток остаётся.
    """
    body, _ = build_export(q.aoi, q.date, q.profile, q.layer, q.format)
    qid = _query_id(q)
    rec = {"id": qid, "params": q.model_dump(), "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "result_sha256": hashlib.sha256(body).hexdigest(), "result_bytes": len(body), "versions": versions()}
    QUERIES.mkdir(parents=True, exist_ok=True)
    path = QUERIES / f"{qid}.json"
    if path.exists():  # тот же запрос уже сохранён — не перезаписываем исходный отпечаток
        rec = json.loads(path.read_text(encoding="utf-8"))
    else:
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


@router.get("/api/queries/{qid}", tags=[Tag.QUERIES], summary="Сохранённый запрос",
            response_model=SavedQueryOut, responses=errors(404, 422))
def get_query(qid: QueryIdPath):
    """Параметры, sha256 результата и версии на момент сохранения."""
    return _read(QUERIES / f"{qid}.json")


@router.post("/api/queries/{qid}/rerun", tags=[Tag.QUERIES], summary="Повторить запрос и сверить результат",
             response_model=RerunOut, responses=errors(404, 422))
def rerun_query(qid: QueryIdPath):
    """Заново строит выгрузку по сохранённым параметрам и сравнивает sha256 с сохранённым: `match: true` —
    результат воспроизводится побайтно. При расхождении сравните `saved_versions` и `current_versions` —
    видно, какой конфиг или модель изменились.
    """
    rec = _read(QUERIES / f"{qid}.json")
    q = QueryIn(**rec["params"])
    body, _ = build_export(q.aoi, q.date, q.profile, q.layer, q.format)
    sha = hashlib.sha256(body).hexdigest()
    return {"id": qid, "params": rec["params"], "saved_sha256": rec["result_sha256"], "rerun_sha256": sha,
            "match": sha == rec["result_sha256"], "saved_versions": rec["versions"], "current_versions": versions()}


# ---------- метрики ----------

@router.get("/api/metrics", tags=[Tag.METRICS], summary="Метрики детектора, моделей и проверок",
            responses={200: {"model": MetricsOut}})
def metrics():
    """Все результаты проверок одним ответом: детектор на тесте MARIDA и на регионе вне обучения, модели
    концентрации (групповая CV, отложенная выборка, покрытие интервалов, перенос), реестр пар, связь детекций
    с полевой концентрацией, проверка прогноза дрейфа, ручная проверка детекций и сводка «что чем подтверждено».
    Разделы, для которых проверка ещё не запускалась, отсутствуют или равны null.
    """
    det = EVAL / "detector" / "metrics.json"
    out = {"detector": _read(det) if det.exists() else None, "concentration": {}, "pairs": None}
    for pid in _profiles():
        p = EVAL / "concentration" / f"{pid}_metrics.json"
        if p.exists():
            out["concentration"][pid] = _read(p)
    if (REG / "summary.json").exists():
        out["pairs"] = _read(REG / "summary.json")
    feats = REG / "pair_features.csv"
    if feats.exists():
        import pandas as pd

        out["pair_features"] = json.loads(pd.read_csv(feats).to_json(orient="records"))
    for key, path in (("transfer", EVAL / "transfer.json"), ("drift_check", EVAL / "drift_check.json"),
                      ("detector_lro", EVAL / "detector" / "leave_region_out.json"),
                      ("review", EVAL / "review" / "score.json")):
        if path.exists():
            out[key] = _read(path)
    out["validated_where"] = [
        {"claim": "Детектор находит скопления плавающего мусора",
         "field": "—", "satellite_labels": "MARIDA test: P/R/F1/IoU", "pairs": "качественно (фрагменты пар S4)"},
        {"claim": "Концентрация профиля, шт./км²",
         "field": "групповая CV + отложенная выборка", "satellite_labels": "—", "pairs": "—"},
        {"claim": "Перенос числа на снимок",
         "field": "—", "satellite_labels": "—", "pairs": "исследование на принятых парах S4 (время наблюдения неизвестно)"},
    ]
    return out
