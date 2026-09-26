"""Схемы API: общие параметры, модели ответов и ошибок — из них собирается документация /api/docs.

Ответы, которые строятся в коде (профили, оценка гекса, маршрут, дрейф из точки, сохранённые запросы),
проверяются по модели. Ответы-файлы из data/web (гексы, ряды, зоны, слой концентрации) отдаются как есть:
модель для них только описывает формат в OpenAPI.
"""
from typing import Annotated, Any

from fastapi import Path, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

DATE_RE = r"^\d{4}-\d{2}-\d{2}$"
PROFILE_RE = r"^[A-Z]$"
QUERY_ID_RE = r"^[0-9a-f]{12}$"

# Подсказки к шаблонам для сообщений об ошибке 422
PATTERN_HINTS = {
    DATE_RE: "дата в формате YYYY-MM-DD",
    PROFILE_RE: "одна заглавная латинская буква — id профиля из /api/profiles",
    QUERY_ID_RE: "12 шестнадцатеричных символов — id из POST /api/queries",
    "^(json|csv)$": "json или csv",
    "^(geojson|csv)$": "geojson или csv",
    "^(zones|hexes)$": "zones или hexes",
    "^(cover|date|p)$": "cover, date или p",
}


class GeoJSONResponse(JSONResponse):
    media_type = "application/geo+json"


class Tag:
    """Разделы документации; описания разделов — TAGS в backend/app.py."""
    SERVICE = "Служебное"
    AOIS = "Акватории и снимки"
    DETECTION = "Детекции на снимке"
    CONCENTRATION = "Концентрация"
    FIELD = "Полевые данные"
    PAIRS = "Реестр пар"
    DRIFT = "Дрейф и маршрут"
    EXPORT = "Выгрузка и отчёт"
    QUERIES = "Сохранённые запросы"
    METRICS = "Метрики качества"


# ---------- общие параметры ----------

AoiPath = Annotated[str, Path(description="Акватория — поле `id` из `GET /api/aois`", examples=["sochi"])]
AoiQuery = Annotated[str, Query(description="Акватория — поле `id` из `GET /api/aois`", examples=["sochi"])]
DatePath = Annotated[str, Path(pattern=DATE_RE, description="Дата снимка `YYYY-MM-DD` (UTC) — одна из `dates` акватории",
                               examples=["2026-09-13"])]
DateQuery = Annotated[str, Query(pattern=DATE_RE, description="Дата снимка `YYYY-MM-DD` (UTC) — одна из `dates` акватории",
                                 examples=["2026-09-13"])]
ProfileQuery = Annotated[str, Query(
    pattern=PROFILE_RE, examples=["B"],
    description="Профиль концентрации — `id` из `GET /api/profiles`: A — плавающий макропластик (СЗ Атлантика), "
                "B — весь плавающий макромусор (Чёрное море)")]
QueryIdPath = Annotated[str, Path(pattern=QUERY_ID_RE, description="id сохранённого запроса (12 hex-символов)",
                                  examples=["0a0b3aaaf8f9"])]
HoursQuery = Annotated[int, Query(ge=6, le=90, description="Горизонт прогноза, ч (6–90)")]

LonLat = Annotated[list[float], Field(min_length=2, max_length=2, description="[долгота, широта], WGS 84",
                                      examples=[[39.7215, 43.579]])]


# ---------- ошибки ----------

class ErrorOut(BaseModel):
    detail: str = Field(description="Что не так — по-русски, можно показать пользователю",
                        examples=["нет обработанного снимка sochi на 2000-01-01"])
    errors: list[dict[str, Any]] | None = Field(
        None, description="Только у 422 из-за формата параметров: разбор по каждому полю (loc, msg, type)")


ERROR_TEXT = {
    404: "Не найдено: неизвестная акватория, нет снимка на эту дату, нет события, гекса или сохранённого запроса",
    422: "Неверные параметры: формат даты, неизвестный профиль, значение вне допустимого диапазона",
    503: "Open-Meteo или Copernicus Marine не ответил, ограничил частоту запросов или не настроен аккаунт — "
         "повторить позже",
}


def errors(*codes: int) -> dict:
    """Описание ответов с ошибками для `responses=` маршрута."""
    return {c: {"model": ErrorOut, "description": ERROR_TEXT[c]} for c in codes}


# ---------- служебное ----------

class HealthOut(BaseModel):
    status: str = Field(description="`ok`, если сервис отвечает", examples=["ok"])
    aois: int = Field(description="Сколько акваторий с готовыми данными")
    versions: dict[str, Any] = Field(description="Хеши конфигов (`configs`) и версии моделей концентрации (`conc_<профиль>`)")


class StatusItem(BaseModel):
    id: str = Field(description="Машинное значение — так статус приходит в ответах")
    code: int | None = Field(description="Числовой код в компактных массивах (`series.status`, `concentration.status`)")
    label: str = Field(description="Подпись по-русски")
    meaning: str = Field(description="Что значит статус")


class QualityItem(BaseModel):
    code: int = Field(description="Код пикселя в маске quality.png")
    id: str = Field(description="Машинное имя — ключ в `scenes[].quality`")
    label: str = Field(description="Подпись по-русски")
    rgba: list[int] = Field(description="Цвет пикселя на quality.png [R, G, B, A]")


class StatusesOut(BaseModel):
    detection: list[StatusItem] = Field(description="Статусы детекции гекса, зоны и снимка")
    concentration: list[StatusItem] = Field(description="Статусы оценки концентрации")
    value_type: list[StatusItem] = Field(description="Тип значения в полевом реестре и выгрузках")
    quality: list[QualityItem] = Field(description="Коды маски качества снимка")


# ---------- акватории ----------

class ConcAvailability(BaseModel):
    available: bool = Field(description="Есть ли на акватории оценки этого профиля")
    reason: str | None = Field(description="Почему профиль недоступен; null, если доступен")


class AoiOut(BaseModel):
    id: str = Field(description="Идентификатор для путей API", examples=["sochi"])
    name: str = Field(description="Название", examples=["Сочи — Адлер (Чёрное море)"])
    bbox: list[float] = Field(description="Границы [lon_min, lat_min, lon_max, lat_max], WGS 84")
    kind: str = Field(description="`sea` — море (дрейф: течения + ветер), `inland` — водохранилище или река (только ветер)")
    port: LonLat = Field(description="Порт — точка старта судна в планировщике маршрута, [lon, lat]")
    tz: int = Field(description="Смещение местного времени от UTC, ч")
    rivers: dict[str, LonLat] = Field(description="Устья рек: название → [lon, lat]")
    note: str | None = Field(description="Пояснение к акватории для интерфейса")
    group: str = Field(description="`field` — район полевых данных кейса (снимки на даты измерений), "
                                   "`monitoring` — акватория оперативного мониторинга")
    source: str | None = Field(description="Источник полевого реестра, для которого подобраны снимки "
                                           "(`source_id`, напр. `S4_BLACK_SEA_DOORS3`); null у акваторий мониторинга")
    dates: list[str] = Field(description="Даты обработанных снимков по возрастанию. Индекс даты — это индекс "
                                         "в массивах `series` и `concentration`")
    basin: str | None = Field(description="Морской бассейн (от него зависит применимость профилей), напр. `black_sea`; "
                                          "null для пресных вод")
    water_type: str | None = Field(description="`marine` или `inland`")
    concentration: dict[str, ConcAvailability] = Field(description="Доступность концентрации по профилям")
    total_area: list[float] = Field(description="Покрытие мусором по всей акватории на каждую дату из `dates`, м²")


class SceneLayers(BaseModel):
    rgb: str = Field(description="Снимок в естественных цветах, JPEG, 20 м/пиксель")
    debris: str = Field(description="Вероятность мусора по пикселям, прозрачный PNG")
    quality: str = Field(description="Маска качества, прозрачный PNG; коды — `GET /api/statuses` → `quality`")
    zones: str = Field(description="Зоны детекции, GeoJSON")
    points: str = Field(description="Пиксели-детекции [lon, lat, p, frac]")
    report: str = Field(description="PDF-отчёт по снимку (профиль B)")


class SceneOut(BaseModel):
    date: str = Field(description="Дата снимка, YYYY-MM-DD (UTC)")
    datetime: str = Field(description="Момент съёмки, ISO 8601 UTC")
    scene_id: str | None = Field(description="Идентификатор сцены Sentinel-2 L2A")
    platform: str | None = Field(description="Спутник: Sentinel-2A/B/C")
    tile: str | None = Field(description="Тайл MGRS")
    tile_cloud_pct: float | None = Field(description="Облачность всего тайла по метаданным, %")
    valid_frac: float = Field(description="Доля воды акватории, пригодной для анализа (без облаков, теней, нет данных)")
    wind: float | None = Field(description="Ветер в момент съёмки, м/с")
    wind_max: float | None = Field(description="Максимальный ветер за предшествующие сутки, м/с")
    sea: str = Field(description="Состояние моря: спокойное, волнение, шторм, лёд")
    storm: bool = Field(description="Ненадёжная сцена (шторм, лёд или сильный блик): остаются только крупные скопления, "
                                    "«не обнаружено» не выставляется, в статистику по времени сцена не входит")
    reason: str | None = Field(description="Почему сцена ненадёжна; null, если надёжна")
    glinty: bool = Field(description="Сильный солнечный блик на заметной части акватории")
    ice_frac: float = Field(description="Доля воды, покрытая льдом или шугой")
    n_det: int = Field(description="Пикселей 10 м с детекцией после фильтров")
    n_zones: int = Field(description="Зон детекции (связных групп пикселей)")
    area_m2: float = Field(description="Покрытие мусором по акватории, м² (эквивалентная площадь)")
    cover: float = Field(description="Покрытие на км² пригодной воды, м²/км²")
    quality: dict[str, float] = Field(description="Доли воды по кодам маски качества: ok, cloud, glint, ship…")
    status: dict[str, int] = Field(description="Число гексов по статусам детекции")
    corners: list[LonLat] = Field(description="Углы растров rgb/debris/quality: верх-лево, верх-право, низ-право, "
                                              "низ-лево. Растр целиком по четырём углам ложится на карту со сдвигом "
                                              "внутри до 100 м — для точной привязки `GET /api/aois/{aoi}/grid`")
    layers: SceneLayers = Field(description="Ссылки на слои снимка")


class RasterGridOut(BaseModel):
    width: int = Field(description="Ширина сетки 10 м, пикселей (= ширина debris.png)")
    height: int = Field(description="Высота сетки 10 м, пикселей")
    step: int = Field(description="Шаг узлов, пикселей сетки 10 м")
    lonlat: list[list[LonLat]] = Field(description="Узлы `[строка][столбец]`: [lon, lat] точки "
                                                   "(столбец × step, строка × step) — угла пикселя; узлы накрывают "
                                                   "снимок с запасом, между узлами — билинейно")


# ---------- профили и концентрация ----------

class ProfileOut(BaseModel):
    id: str = Field(description="Буква профиля — значение параметра `profile`", examples=["B"])
    code: str = Field(description="Код профиля в реестре", examples=["litter_visual_gt2"])
    label: str = Field(description="Что считаем")
    material: str | None = Field(description="Материал")
    size_class: str | None = Field(description="Класс размера")
    method: str | None = Field(description="Метод полевого учёта")
    unit: str = Field(description="Единица концентрации", examples=["шт./км²"])
    primary: bool | None = Field(description="Основной профиль кейса")
    show_in_ui: bool = Field(description="Показывается ли в интерфейсе")
    basins: list[str] = Field(description="Бассейны, где модель профиля применима")
    model_version: str | None = Field(None, description="Версия обученной модели; нет — модель не обучена")
    model_type: str | None = Field(None, description="Тип модели: nb_glm, loglinear_ridge…")
    n_train: int | None = Field(None, description="Событий в обучении")
    n_holdout: int | None = Field(None, description="Событий в отложенной выборке")
    holdout_mae: dict[str, float] | None = Field(
        None, description="MAE на отложенной выборке, шт./км²: основная модель и базовые (median, idw)")


class ConcentrationLayerOut(BaseModel):
    profile: str = Field(description="Профиль")
    unit: str = Field(description="Единица: шт./км²")
    dates: list[str] = Field(description="Даты — те же и в том же порядке, что в `series`")
    available: bool = Field(description="false — профиль к акватории не применим, массивов нет")
    reason: str | None = Field(None, description="Почему недоступен (только при available = false)")
    status_codes: dict[str, str] = Field(description="Код → статус концентрации: 0 unavailable, 1 model_estimate, "
                                                     "2 research_estimate")
    model_version: str | None = Field(None, description="Версия модели")
    model_type: str | None = Field(None, description="Тип модели")
    nearest_field_km: list[float] | None = Field(None, description="[гекс] расстояние до ближайшего полевого измерения, км")
    value: list[list[float | None]] | None = Field(None, description="[дата][гекс] концентрация, шт./км²")
    lo80: list[list[float | None]] | None = Field(None, description="[дата][гекс] нижняя граница 80%-интервала")
    hi80: list[list[float | None]] | None = Field(None, description="[дата][гекс] верхняя граница 80%-интервала")
    status: list[list[int]] | None = Field(None, description="[дата][гекс] код статуса из `status_codes`")


class HexEstimateOut(BaseModel):
    aoi: str
    date: str
    hex: int = Field(description="Индекс гекса — `properties.i` в hexes.geojson")
    h3: str = Field(description="Ячейка H3 (разрешение 8, ~0,74 км²)")
    profile: str
    unit: str = Field(description="шт./км²")
    conc_items_km2: float | None = Field(description="Концентрация, шт./км²; null — оценка недоступна")
    lo80: float | None = Field(description="80%-интервал, нижняя граница")
    hi80: float | None = Field(description="80%-интервал, верхняя граница")
    lo95: float | None = Field(description="95%-интервал, нижняя граница")
    hi95: float | None = Field(description="95%-интервал, верхняя граница")
    status: str = Field(description="model_estimate, research_estimate или unavailable")
    status_ru: str = Field(description="Статус по-русски")
    reasons: list[str] = Field(description="Причины статуса: далеко от полевых данных, другой сезон, признак вне диапазона…")
    nearest_field_km: float = Field(description="До ближайшего полевого измерения профиля, км")
    features: dict[str, float] = Field(description="Признаки, на которых считалась модель")
    model_version: str
    model_type: str
    detection_status: str = Field(description="Статус детекции гекса на эту дату")
    detection_status_ru: str
    cover_m2_km2: float = Field(description="Покрытие мусором по детектору, м²/км² — в шт./км² не переводится")
    valid_frac: float = Field(description="Доля воды гекса, пригодной для анализа")
    calc: list[dict[str, str]] = Field(default_factory=list, description="Расчёт по шагам: формула модели "
                                                                   "с подставленными признаками и коэффициентами")
    fusion: "FusionOut | None" = Field(None, description="Сведение модели с полевыми измерениями рядом; null — "
                                                         "концентрация недоступна")


# ---------- снимок на дату: файлы data/web (только документация) ----------

class GeoJSONFeature(BaseModel):
    type: str = Field("Feature")
    geometry: dict[str, Any] = Field(description="Геометрия GeoJSON, WGS 84")
    properties: dict[str, Any] = Field(description="Атрибуты — см. описание ручки")


class FeatureCollection(BaseModel):
    type: str = Field("FeatureCollection")
    features: list[GeoJSONFeature]


class SeriesOut(BaseModel):
    aoi: str
    basin: str | None
    water_type: str
    dates: list[str] = Field(description="Даты снимков; индекс даты — первый индекс всех массивов ниже")
    scenes: list[dict[str, Any]] = Field(description="Метаданные снимков — те же поля, что в `GET /api/aois/{aoi}/scenes`, "
                                                     "без `layers`")
    cover: list[list[float]] = Field(description="[дата][гекс] покрытие мусором, м²/км²")
    ndet: list[list[int]] = Field(description="[дата][гекс] пикселей с детекцией")
    valid: list[list[float]] = Field(description="[дата][гекс] доля пригодной воды")
    status: list[list[int]] = Field(description="[дата][гекс] код статуса детекции из `status_codes`")
    status_codes: dict[str, str] = Field(description="0 not_detected, 1 detected, 2 insufficient_data")
    cover_class_edges: list[float] = Field(description="Границы классов покрытия, м²/км²: низкое, умеренное, высокое, "
                                                       "очень высокое")
    detector: dict[str, Any] = Field(description="Порог детектора `p_det` и хеш его конфига")


class AoiRef(BaseModel):
    id: str = Field(description="Акватория — `id` из `GET /api/aois`")
    name: str = Field(description="Название")
    dates: list[str] = Field(description="Даты обработанных снимков")


class ZoneItem(BaseModel):
    zone_id: str = Field(description="id зоны: `<акватория>-<дата>-<номер>`", examples=["batumi-2024-06-05-001"])
    aoi: str = Field(description="Акватория — `id` из `GET /api/aois`")
    aoi_name: str = Field(description="Название акватории")
    date: str = Field(description="Дата снимка, YYYY-MM-DD (UTC)")
    scene_id: str | None = Field(description="Сцена Sentinel-2 L2A")
    scene_datetime_utc: str = Field(description="Момент съёмки, ISO 8601 UTC")
    lat: float = Field(description="Широта центра зоны, WGS 84")
    lon: float = Field(description="Долгота центра зоны, WGS 84")
    h3: str = Field(description="Ячейка H3 (разрешение 8), где центр зоны")
    zone_area_km2: float = Field(description="Площадь зоны, км²")
    n_pixels: int = Field(description="Пикселей 10 м с детекцией в зоне")
    cover_m2: float = Field(description="Эквивалентная площадь плавающего мусора в зоне, м²")
    cover_m2_km2: float = Field(description="Покрытие на км² зоны, м²/км²")
    p_mean: float = Field(description="Средняя вероятность мусора по пикселям зоны")
    p_max: float = Field(description="Максимальная вероятность мусора в зоне")
    detection_status: str = Field(description="Всегда `detected`")
    detection_status_ru: str = Field(description="Статус детекции по-русски")
    profile: str = Field(description="Профиль концентрации")
    profile_label: str = Field(description="Что считает профиль")
    size_class: str = Field(description="Размерный класс профиля")
    unit: str = Field(description="Единица концентрации: шт./км²")
    conc_items_km2: float | None = Field(description="Модельная концентрация в центре зоны, шт./км²; null — недоступна")
    conc_lo80: float | None = Field(description="80%-интервал, нижняя граница")
    conc_hi80: float | None = Field(description="80%-интервал, верхняя граница")
    conc_lo95: float | None = Field(description="95%-интервал, нижняя граница")
    conc_hi95: float | None = Field(description="95%-интервал, верхняя граница")
    conc_status: str | None = Field(description="model_estimate, research_estimate или unavailable")
    conc_status_ru: str | None = Field(description="Статус концентрации по-русски")
    conc_reasons: str | None = Field(description="Причины статуса через «; »")
    model_version: str | None = Field(description="Версия модели концентрации")
    valid_frac_scene: float = Field(description="Доля воды акватории, пригодной для анализа на этом снимке")
    wind_ms: float | None = Field(description="Ветер ERA5 в момент съёмки, м/с")
    sea: str = Field(description="Состояние моря: спокойное, волнение, шторм")
    scene_reliable: bool = Field(description="false — шторм, лёд или сильный блик: зона менее надёжна")
    field_event_id: str | None = Field(description="Ближайшее полевое измерение — `event_id`")
    field_profile: str | None = Field(description="Профиль ближайшего измерения")
    field_date: str | None = Field(description="Дата ближайшего измерения")
    field_distance_km: float | None = Field(description="Расстояние до него, км")
    field_date_gap_days: int | None = Field(description="Разница дат со снимком, сут.")
    field_conc_items_km2: float | None = Field(description="Измеренная там концентрация C = N / A, шт./км²")
    geometry: dict[str, Any] | None = Field(None, description="Контур зоны (GeoJSON-геометрия) — только при "
                                                              "`geometry=true`")


class ZonesOut(BaseModel):
    total: int = Field(description="Сколько зон подходит под фильтры")
    count: int = Field(description="Сколько зон в этом ответе")
    offset: int = Field(description="Сколько пропущено")
    limit: int = Field(description="Размер страницы")
    profile: str = Field(description="Профиль, чья концентрация в `conc_*`")
    sort: str = Field(description="Порядок: cover, date или p")
    items: list[ZoneItem] = Field(description="Зоны скопления")


class FieldEventOut(BaseModel):
    event_id: str
    rows: list[dict[str, Any]] = Field(description="Все строки реестра с этим событием и решение по каждой: "
                                                   "`decision` included/excluded, `reason_code`, `reason_text`")
    measurements: list[dict[str, Any]] = Field(description="Принятые измерения (как в `GET /api/field`) плюс `formula` — "
                                                           "расчёт C = N / A текстом")
    pairs: list[dict[str, Any]] = Field(description="Пары события со снимками из реестра пар и решение по каждой. "
                                                    "У пары со сценой ещё: `collection`; `tiles_url` — XYZ-тайлы сцены "
                                                    "в естественных цветах (Planetary Computer, без ключа); "
                                                    "`scene_bbox` — границы сцены; `aoi`, `aoi_date` — обработанный "
                                                    "снимок сервиса на ту же дату, если он есть")
    aois: list[AoiRef] = Field(description="Акватории сервиса, внутри которых лежит событие")


class FieldSourceOut(BaseModel):
    source_id: str = Field(description="Источник реестра", examples=["S4_BLACK_SEA_DOORS3"])
    code: str = Field(description="Короткий код из постановки", examples=["S4"])
    region: str = Field(description="Район", examples=["Чёрное море"])
    area: str = Field(description="Уточнение района и рейса")
    observations: str = Field(description="Вид наблюдений (как в постановке)")
    features: str = Field(description="Особенности (как в постановке)")
    imagery: str = Field(description="Что есть в архивах снимков для этого источника")
    n_rows: int = Field(description="Строк реестра")
    n_events: int = Field(description="Событий реестра")
    n_measurements: int = Field(description="Событий с полевым измерением концентрации на карте (`GET /api/field`)")
    profiles: dict[str, int] = Field(description="Измерения по профилю и роли: `B/train`, `B/transfer_check`…")
    date_from: str = Field(description="Первая дата наблюдений, YYYY-MM-DD")
    date_to: str = Field(description="Последняя дата наблюдений, YYYY-MM-DD")
    bbox: list[float] = Field(description="Границы наблюдений [lon_min, lat_min, lon_max, lat_max]")
    pairs: dict[str, Any] = Field(description="Реестр пар по источнику: `events_by_outcome` (accepted / context / "
                                              "rejected), `reasons` (коды причин по строкам), `scenes` — число "
                                              "найденных сцен по коллекциям")
    aois: list[AoiRef] = Field(description="Акватории сервиса со снимками на даты измерений этого источника")


class PairsOut(BaseModel):
    summary: dict[str, Any] = Field(description="Сводка реестра: события, кандидаты, исходы, причины отказа")
    rows: list[dict[str, Any]] = Field(description="Строки реестра «событие ↔ снимок» с признаками детектора в следе")


class DriftOut(BaseModel):
    n: int = Field(description="Частиц в ансамбле (затравок × 4 члена ансамбля); 0 — на дату нет детекций")
    hours: int | None = Field(None, description="Горизонт, ч")
    t0: str | None = Field(None, description="Старт — момент съёмки, ISO 8601 UTC")
    frames: list[list[LonLat]] = Field(description="[час][частица] → [lon, lat]; кадр 0 — положение на снимке")
    beached: list[int] | None = Field(None, description="[частица] 1 — выброшена на берег")
    hexes: dict[str, dict[str, float]] = Field(
        description="Снимки распределения на 24, 48 ч и на горизонт: ключ — часы, значение — ячейка H3 → м² мусора")


# ---------- дрейф и маршрут ----------

class DriftPointOut(BaseModel):
    tracks: list[list[LonLat]] = Field(description="[член ансамбля][час] → [lon, lat]")
    center: list[LonLat] = Field(description="[час] центр ансамбля")
    spread_km: list[float] = Field(description="[час] разброс ансамбля (СКО от центра), км — ширина конуса")
    beached_frac: float = Field(description="Доля членов ансамбля, выброшенных на берег")
    t0: str = Field(description="Старт — момент съёмки, ISO 8601 UTC")


class DriftAtOut(DriftPointOut):
    t0: str = Field(description="Момент старта, ISO 8601 UTC")
    sources: list[str] = Field(description="Откуда поля: течения, стоксов дрейф, ветер")


class FlowField(BaseModel):
    u: list[list[float | None]] = Field(description="[час][ячейка] → восточная составляющая, м/с; ячейки построчно "
                                                    "с юга на север (`lats`), в строке с запада на восток (`lons`); "
                                                    "null — суша")
    v: list[list[float | None]] = Field(description="[час][ячейка] → северная составляющая, м/с")


class WaterMaskOut(BaseModel):
    lon0: float = Field(description="Долгота юго-западной клетки")
    lat0: float = Field(description="Широта юго-западной клетки")
    step: float = Field(description="Шаг клетки, градусы")
    nx: int = Field(description="Клеток с запада на восток")
    ny: int = Field(description="Клеток с юга на север")
    bits: str = Field(description="«1» — вода, «0» — суша; nx × ny символов построчно с юга на север")


class FlowOut(BaseModel):
    t0: str = Field(description="Кадр 0 — момент съёмки (или старта дрейфа), ISO 8601 UTC; дальше кадры через 1 ч")
    hours: int = Field(description="Последний кадр, ч от t0")
    lons: list[float] = Field(description="Долготы узлов сетки, с запада на восток")
    lats: list[float] = Field(description="Широты узлов сетки, с юга на север")
    currents: FlowField | None = Field(description="Течения; null — внутренний водоём, течений нет")
    wind: FlowField = Field(description="Ветер 10 м")
    water: WaterMaskOut = Field(description="Маска воды: где рисовать течения")
    sources: list[str] = Field(description="Откуда поля")


class AccumulationOut(BaseModel):
    factor: dict[str, float] = Field(description="Ячейка H3 → во сколько раз частиц в ней через 72 ч больше, чем "
                                                 "в средней ячейке на старте. >1 — зона вероятного скопления")
    beached_frac: float = Field(description="Доля частиц, выброшенных на берег")
    n: int = Field(description="Частиц засеяно (равномерно по воде, шаг 600 м)")


class RouteStop(BaseModel):
    order: int = Field(description="Порядковый номер остановки")
    h3: str = Field(description="Ячейка H3 цели")
    area_m2: float = Field(description="Покрытие мусором в цели, м²")
    n_pixels: int = Field(description="Пикселей детекции в цели")
    observed: LonLat = Field(description="Где цель была на снимке")
    predicted: LonLat = Field(description="Где она будет к прибытию судна (с учётом дрейфа)")
    drift_km: float = Field(description="Смещение за время до прибытия, км")
    eta_h: float = Field(description="Прибытие, ч от момента съёмки")
    eta: str = Field(description="Прибытие, ISO 8601 UTC")
    leg_km: float = Field(description="Длина перехода от предыдущей точки, км")


class RouteOut(BaseModel):
    stops: list[RouteStop] = Field(description="Остановки по порядку; пусто — целей нет или ни одна не успевается за смену")
    line: list[LonLat] = Field(description="Линия маршрута: порт → остановки → порт")
    note: str | None = Field(None, description="Пояснение, если на дату нет детекций")
    port: LonLat = Field(description="Порт выхода")
    total_km: float = Field(description="Длина маршрута с возвратом, км")
    duration_h: float = Field(description="Время в море, ч")
    speed_kn: float = Field(description="Скорость судна, узлы")
    delay_h: float = Field(description="Задержка выхода после съёмки, ч")
    pass_time: str = Field(description="Момент съёмки, ISO 8601 UTC")
    covered_m2: float = Field(description="Покрытие целей маршрута, м²")
    total_m2: float = Field(description="Покрытие всех кандидатов на дату (до 25 гексов с наибольшим покрытием), м²")


# ---------- сохранённые запросы ----------

class QueryIn(BaseModel):
    aoi: str = Field(description="Акватория", examples=["batumi"])
    date: str = Field(pattern=DATE_RE, description="Дата снимка YYYY-MM-DD", examples=["2024-06-05"])
    profile: str = Field("B", pattern=PROFILE_RE, description="Профиль концентрации")
    layer: str = Field("zones", pattern="^(zones|hexes)$", description="`zones` — зоны детекции, `hexes` — все гексы")
    format: str = Field("geojson", pattern="^(geojson|csv)$", description="`geojson` или `csv`")


class SavedQueryOut(BaseModel):
    id: str = Field(description="id запроса — первые 12 символов sha256 от параметров", examples=["0a0b3aaaf8f9"])
    params: QueryIn = Field(description="Параметры выгрузки")
    created_utc: str = Field(description="Когда сохранён впервые, ISO 8601 UTC")
    result_sha256: str = Field(description="sha256 байтов выгрузки на момент сохранения")
    result_bytes: int = Field(description="Размер выгрузки, байт")
    versions: dict[str, Any] = Field(description="Хеши конфигов и версии моделей на момент сохранения")


class RerunOut(BaseModel):
    id: str
    params: QueryIn
    saved_sha256: str = Field(description="sha256 при сохранении")
    rerun_sha256: str = Field(description="sha256 сейчас")
    match: bool = Field(description="true — результат побайтно совпал с сохранённым")
    saved_versions: dict[str, Any] = Field(description="Версии при сохранении")
    current_versions: dict[str, Any] = Field(description="Версии сейчас — при `match: false` показывают, что изменилось")


class FusionEvidence(BaseModel):
    event_id: str = Field(description="Полевое измерение — `event_id` из `GET /api/field`")
    date_utc: str = Field(description="Дата измерения")
    conc_items_km2: float = Field(description="Измерено: C = N / A, шт./км²")
    distance_km: float = Field(description="Расстояние от точки оценки, км")
    age_days: float = Field(description="Давность относительно момента оценки, сут.")
    st_distance_km: float = Field(description="Пространственно-временное расстояние h = √(d² + (v·Δt)²), км")
    weight: float = Field(description="Вес измерения в сведённой оценке (кригинг)")
    model_factor: float = Field(description="Измерено / модель в месте измерения: >1 — модель там занижает")
    model_at_event: float = Field(description="Что модель даёт в месте и в момент измерения, шт./км²")
    counting_noise: bool = Field(description="Известен счётный шум (N и A): точность измерения учтена в весе")
    n_items: float | None = Field(description="Число предметов N; null — опубликована только плотность")
    drift_buffer_km: float = Field(description="R = r_следа + Δt·v: насколько могло сместиться пятно, км")
    same_patch: bool = Field(description="Точка внутри дрейфового буфера — снимок и измерение описывают одно пятно")


class FusedValue(BaseModel):
    value: float = Field(description="Сведённая концентрация, шт./км²")
    lo80: float
    hi80: float
    lo95: float
    hi95: float


class FusionOut(BaseModel):
    profile: str
    method: str = Field(description="Название метода")
    version: str = Field(description="Версия параметров сведения")
    model: dict[str, float] = Field(description="Оценка модели `value`, шт./км², и её вес `weight` (1 − Σ весов измерений)")
    fused: FusedValue = Field(description="Сведённая оценка с интервалами")
    field_weight: float = Field(description="Суммарный вес полевых измерений, 0–1")
    variance_reduction: float = Field(description="На сколько измерения сузили дисперсию оценки, 0–1")
    evidence: list[FusionEvidence] = Field(description="Измерения с заметным весом, по убыванию веса")
    n_candidates: int = Field(description="Измерений, попавших в систему кригинга")
    params: dict[str, float] = Field(description="Параметры: psill s, nugget τ², range_km ρ, v_km_day v")
    unit: str = Field(description="шт./км²")


class SeparationOut(BaseModel):
    aoi: str
    date: str
    unit: str = Field(description="пиксели 10 м")
    valid_px: int = Field(description="Пригодных пикселей воды")
    water_px: int = Field(description="Пикселей в маске воды")
    glint_frac: float = Field(description="Доля воды под сильным бликом")
    model_classes: dict[str, int] = Field(description="Аномальные пиксели по классам модели: debris, organic, ship, "
                                                      "foam, cloud")
    model_classes_ru: dict[str, str] = Field(description="Подписи классов")
    candidates: int = Field(description="Кандидатов в мусор: P ≥ порога")
    rejected: dict[str, int] = Field(description="Отбраковано кандидатов по причинам (первая сработавшая)")
    reasons_ru: dict[str, str] = Field(description="Подписи причин")
    kept: int = Field(description="Итоговых детекций — столько же, сколько `n_det` снимка")


class MethodologyOut(BaseModel):
    target: list[dict[str, Any]] = Field(description="Целевая величина по профилям: что, единица, размер, метод")
    steps: list[dict[str, Any]] = Field(description="Шаги расчёта: метод, формулы с текущими коэффициентами, код")
    objects: list[dict[str, Any]] = Field(description="Объекты, которые можно спутать с мусором: как отделяем, "
                                                      "доля ошибок на MARIDA test и MADOS, статус")
    fusion_rules: list[dict[str, str]] = Field(description="Как разбираются расхождения источников")
    fusion_eval: dict[str, Any] = Field(description="Проверка сведения на отложенной выборке по профилям")


class DatasetOut(BaseModel):
    id: str
    name: str
    provider: str | None = None
    version: str | None = None
    access: str | None = None
    local: str | None = None
    license: str
    terms: str | None = None
    role: list[str] = Field(description="train, eval, feature, input, display")
    used_for: str | None = None
    code: str | None = None
    fields: list[dict[str, str]] = Field(default_factory=list, description="Поля, которые использует сервис")
    notes: str | None = None


class MetricsOut(BaseModel):
    detector: dict[str, Any] | None = Field(description="Детектор на тесте MARIDA: P/R/F1/IoU по методам и порогам")
    concentration: dict[str, Any] = Field(description="Модели концентрации по профилям: групповая CV, отложенная "
                                                      "выборка, покрытие интервалов, проверка переноса")
    pairs: dict[str, Any] | None = Field(description="Сводка реестра пар «событие ↔ снимок»")
    pair_features: list[dict[str, Any]] | None = Field(None, description="Признаки детектора в следе принятых пар")
    transfer: dict[str, Any] | None = Field(None, description="Связь детекций в следе с полевой концентрацией")
    drift_check: dict[str, Any] | None = Field(None, description="Проверка прогноза дрейфа на парах соседних снимков")
    detector_lro: dict[str, Any] | None = Field(None, description="Детектор на регионе, исключённом из обучения")
    detector_mados: dict[str, Any] | None = Field(None, description="Детектор на новых сценах MADOS test: P/R/F1 и "
                                                               "ложные срабатывания на нефти, слизи, медузах, платформах")
    review: dict[str, Any] | None = Field(None, description="Ручная проверка фрагментов с детекциями")
    fusion: dict[str, Any] | None = Field(None, description="Сведение модели с полевыми измерениями: вариограмма и "
                                                         "проверка на отложенной выборке по профилям")
    validated_where: list[dict[str, str]] = Field(description="Что чем подтверждено: утверждение → данные проверки")


HexEstimateOut.model_rebuild()
