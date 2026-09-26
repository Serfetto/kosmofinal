# API AquaFlow — справочник ручек

AquaFlow по снимкам Sentinel-2 находит зоны вероятного скопления плавающего мусора, показывает полевые измерения
и модельную концентрацию в шт./км², прогнозирует дрейф и планирует маршрут судна. Всё, что видно в интерфейсе,
доступно через HTTP API ниже: интерфейс пользуется теми же ручками.

- Базовый адрес локально: `http://localhost:8000`. Запуск: `.venv/Scripts/python -m uvicorn backend.app:app --port 8000`.
- Интерактивная документация с кнопкой «Try it out»: **`/api/docs`** (Swagger UI), **`/api/redoc`** (ReDoc).
- Машиночитаемая схема OpenAPI 3.1: **`/api/openapi.json`** — из неё можно сгенерировать клиент.
- Авторизации нет. Данные меняет только `POST /api/queries` (сохраняет запрос); остальные ручки их читают
  и лишь кешируют тяжёлые расчёты.

## Содержание

- [Соглашения](#соглашения)
- [Все ручки одной таблицей](#все-ручки-одной-таблицей)
- [Типовые сценарии](#типовые-сценарии)
- [Служебное](#служебное): `health`, `statuses`
- [Акватории и снимки](#акватории-и-снимки): `aois`, `aois/{aoi}`, `scenes`, `hexes`, `series`, `grid`
- [Детекции на снимке](#детекции-на-снимке): `zones`, `points`, `/api/zones` (все скопления одним JSON)
- [Концентрация](#концентрация): `profiles`, `concentration`, `hex/{i}`
- [Полевые данные](#полевые-данные): `field`, `field/objects`, `field/sources`, `field/{event_id}`
- [Реестр пар](#реестр-пар): `pairs`
- [Дрейф и маршрут](#дрейф-и-маршрут): `drift`, `drift_point`, `/api/drift` (без снимка), `flow`, `/api/flow`,
  `accumulation`, `route`
- [Выгрузка и отчёт](#выгрузка-и-отчёт): `export`, `report`
- [Сохранённые запросы](#сохранённые-запросы): `queries`, `queries/{qid}`, `rerun`
- [Метрики качества](#метрики-качества): `metrics`
- [Растровые слои (статика)](#растровые-слои-статика)
- [Ошибки](#ошибки)
- [Словарь статусов](#словарь-статусов)

## Соглашения

| Что | Как |
|---|---|
| Координаты | WGS 84, порядок **`[долгота, широта]`**, как в GeoJSON |
| Рамка `bbox` | `[lon_min, lat_min, lon_max, lat_max]` |
| Дата снимка | `YYYY-MM-DD` по UTC — одна из `dates` акватории |
| Момент времени | ISO 8601 в UTC, например `2026-09-13T08:20:21.024000+00:00` |
| Акватория `aoi` | строковый id из `GET /api/aois`: мониторинг — `sochi`, `novorossiysk`, `kerch`, `neva`, `vladivostok`, `kuibyshev`, `volgograd`; районы полевых данных кейса — `batumi`, `s4_*` (Чёрное море, DOORS 2024), `s3_*` (Северное море, 2016) |
| Профиль `profile` | одна заглавная буква из `GET /api/profiles`: `A` или `B` (по умолчанию `B`) |
| Гексы | H3 разрешения 8, ~0,74 км². У каждого гекса есть индекс `i` — его позиция в `hexes.geojson` |
| Массивы `[дата][гекс]` | в `series` и `concentration`: первый индекс — позиция даты в `dates`, второй — индекс гекса `i` |
| Кодировка | UTF-8; CSV — UTF-8 с BOM (открывается в Excel без настройки) |

**Три величины не смешиваются** — ни в ответах, ни на карте:

| Величина | Откуда | Единица | Где в API |
|---|---|---|---|
| Полевое измерение | C = N / A по полосе учёта, 95% ДИ по Гарвуду | шт./км² | `field`, `field/{event_id}` |
| Модельная концентрация | модель профиля по полевым данным, с интервалом и статусом | шт./км² | `concentration`, `hex/{i}`, `conc_*` у зон, `export` |
| Покрытие по детектору | эквивалентная площадь мусора по снимку | м², м²/км² | `zones`, `series`, `scenes`, `hexes` |

Покрытие в шт./км² **не переводится**: это разные совокупности и разные методы.

## Все ручки одной таблицей

| Метод | Путь | Что возвращает | Формат |
|---|---|---|---|
| GET | [`/api/health`](#get-apihealth) | сервис жив, версии конфигов и моделей | JSON |
| GET | [`/api/statuses`](#get-apistatuses) | словарь статусов, маски качества и классов модели | JSON |
| GET | [`/api/aois`](#get-apiaois) | все акватории и их даты | JSON |
| GET | [`/api/aois/{aoi}`](#get-apiaoisaoi) | одна акватория | JSON |
| GET | [`/api/aois/{aoi}/scenes`](#get-apiaoisaoiscenes) | снимки: условия съёмки и ссылки на слои | JSON |
| GET | [`/api/aois/{aoi}/hexes`](#get-apiaoisaoihexes) | сетка гексов с многолетними метриками | GeoJSON |
| GET | [`/api/aois/{aoi}/series`](#get-apiaoisaoiseries) | ряды по датам и гексам | JSON |
| GET | [`/api/aois/{aoi}/grid`](#get-apiaoisaoigrid) | привязка растров снимка к карте | JSON |
| GET | [`/api/aois/{aoi}/{date}/zones`](#get-apiaoisaoidatezones) | зоны детекции на снимке | GeoJSON |
| GET | [`/api/aois/{aoi}/{date}/points`](#get-apiaoisaoidatepoints) | пиксели с детекцией | JSON |
| GET | `/api/aois/{aoi}/{date}/model-classes` | цветная маска шести групп детектора до порога и фильтров; первый запрос строит и кеширует слой | PNG |
| GET | [`/api/zones`](#get-apizones) | скопления мусора по всем акваториям и датам: где, когда, сколько | JSON |
| GET | [`/api/profiles`](#get-apiprofiles) | профили концентрации и качество моделей | JSON |
| GET | [`/api/aois/{aoi}/concentration`](#get-apiaoisaoiconcentration) | концентрация по гексам на все даты | JSON |
| GET | [`/api/aois/{aoi}/{date}/hex/{i}`](#get-apiaoisaoidatehexi) | концентрация в гексе с причинами статуса | JSON |
| GET | [`/api/field`](#get-apifield) | полевые измерения с фильтрами | GeoJSON |
| GET | [`/api/field/objects`](#get-apifieldobjects) | отдельные предметы (контекст) | GeoJSON |
| GET | [`/api/field/sources`](#get-apifieldsources) | источники S1–S4: период, снимки, акватории | JSON |
| GET | [`/api/field/{event_id}`](#get-apifieldevent_id) | расшифровка расчёта по событию | JSON |
| GET | [`/api/pairs`](#get-apipairs) | реестр пар «событие ↔ снимок» | JSON / CSV |
| GET | [`/api/aois/{aoi}/{date}/drift`](#get-apiaoisaoidatedrift) | прогноз дрейфа всех детекций | JSON |
| GET | [`/api/aois/{aoi}/{date}/drift_point`](#get-apiaoisaoidatedrift_point) | дрейф из точки (конус) | JSON |
| GET | [`/api/drift`](#get-apidrift) | дрейф из точки без снимка, с любого момента (реанализ Copernicus Marine) | JSON |
| GET | [`/api/aois/{aoi}/{date}/flow`](#get-apiaoisaoidateflow) | течения и ветер по часам — для анимации на карте | JSON |
| GET | [`/api/flow`](#get-apiflow) | то же вокруг точки без снимка (реанализ Copernicus Marine) | JSON |
| GET | [`/api/aois/{aoi}/{date}/accumulation`](#get-apiaoisaoidateaccumulation) | где течения собирают мусор | JSON |
| GET | [`/api/aois/{aoi}/{date}/route`](#get-apiaoisaoidateroute) | маршрут судна с учётом дрейфа | JSON |
| GET | [`/api/export`](#get-apiexport) | выгрузка зон или гексов | GeoJSON / CSV |
| GET | [`/api/report`](#get-apireport) | отчёт по снимку | PDF |
| POST | [`/api/queries`](#post-apiqueries) | сохранить запрос выгрузки | JSON |
| GET | [`/api/queries/{qid}`](#get-apiqueriesqid) | сохранённый запрос | JSON |
| POST | [`/api/queries/{qid}/rerun`](#post-apiqueriesqidrerun) | повторить и сверить sha256 | JSON |
| GET | [`/api/metrics`](#get-apimetrics) | метрики детектора, моделей и проверок | JSON |
| GET | [`/data/{aoi}/{date}/…`](#растровые-слои-статика) | растры снимка: rgb, debris, quality | JPEG / PNG |

## Типовые сценарии

**Карта мусора на дату**

```bash
curl http://localhost:8000/api/aois                                  # выбрать aoi и дату из dates
curl http://localhost:8000/api/aois/sochi/scenes                     # условия съёмки, corners и ссылки на слои
curl http://localhost:8000/api/aois/sochi/hexes                      # сетка гексов (один раз на акваторию)
curl http://localhost:8000/api/aois/sochi/2026-09-13/zones           # зоны детекции
curl "http://localhost:8000/api/aois/sochi/concentration?profile=B"  # концентрация: взять строку value[индекс даты]
```

**Все скопления мусора одним JSON** — дата, координаты, площадь, покрытие, концентрация:

```bash
curl "http://localhost:8000/api/zones?limit=20"                                   # крупнейшие по покрытию
curl "http://localhost:8000/api/zones?aoi=batumi&date_from=2024-06-01&date_to=2024-06-30&sort=date"
curl "http://localhost:8000/api/zones?bbox=41.0,41.5,41.8,42.0&min_cover_m2=50&reliable_only=true"
```

**Что в конкретном месте** — клик по гексу: `GET /api/aois/sochi/2026-09-13/hex/5?profile=B` — число,
интервалы, статус и почему такой статус.

**Отдать данные в ГИС и доказать воспроизводимость**

```bash
curl -OJ "http://localhost:8000/api/export?aoi=sochi&date=2026-09-13&profile=B&layer=zones&format=csv"
curl -X POST http://localhost:8000/api/queries -H "Content-Type: application/json" \
     -d '{"aoi":"sochi","date":"2026-09-13","profile":"B","layer":"zones","format":"csv"}'   # → id
curl -X POST http://localhost:8000/api/queries/<id>/rerun                                      # → match: true
```

**Полевые данные кейса и снимки на даты измерений**

```bash
curl http://localhost:8000/api/field/sources                  # S1–S4: период, итог пар, акватории со снимками
curl http://localhost:8000/api/aois/s4_zonguldak/2024-06-17/zones   # снимок в день измерений T30–T32
curl http://localhost:8000/api/field/S4:DOORS3:T30            # расшифровка C = N / A, пары, tiles_url сцены
```

**Выйти на сбор**: `GET /api/aois/sochi/2026-09-13/route?n=8&speed=12&delay=6` — остановки с учётом дрейфа;
`GET /api/report?aoi=sochi&date=2026-09-13` — PDF с координатами крупнейших зон для экипажа.

---

## Служебное

### `GET /api/health`

Сервис жив: сколько акваторий с готовыми данными и какие версии конфигов и моделей сейчас работают.
Для мониторинга и проверки развёртывания. Параметров нет.

| Поле | Тип | Описание |
|---|---|---|
| `status` | string | `ok` |
| `aois` | int | акваторий с готовыми данными |
| `versions.configs` | object | имя конфига → первые 12 символов sha256 его содержимого |
| `versions.conc_<профиль>` | string | версия модели концентрации профиля |

```json
{
  "status": "ok",
  "aois": 8,
  "versions": {
    "configs": {"profiles.yaml": "8b243937721a", "concentration.yaml": "c6bce5631d2b",
                "detector.yaml": "828442195860", "pairs.yaml": "ab6c138aae22"},
    "conc_A": "conc-A-c6bce5631d2b",
    "conc_B": "conc-B-c6bce5631d2b"
  }
}
```

### `GET /api/statuses`

Все статусы, которые встречаются в ответах, с подписями и смыслом, и коды маски качества снимка с цветами.
Удобно, чтобы не зашивать подписи в клиент. Параметров нет. Содержимое — в [словаре статусов](#словарь-статусов).

| Поле | Описание |
|---|---|
| `detection[]` | статусы детекции: `id`, числовой `code` в массивах `series.status`, `label`, `meaning` |
| `concentration[]` | статусы концентрации: `id`, `code` в массивах `concentration.status`, `label`, `meaning` |
| `value_type[]` | тип значения в реестре и выгрузках (`code` = null) |
| `quality[]` | коды пикселей маски `quality.png`: `code`, `id`, `label`, `rgba` |

```json
{
  "detection": [
    {"id": "detected", "code": 1, "label": "обнаружено",
     "meaning": "детектор нашёл плавающий мусор хотя бы в одном пикселе"}
  ],
  "quality": [
    {"code": 3, "id": "cloud", "label": "облако", "rgba": [235, 235, 245, 190]}
  ]
}
```

---

## Акватории и снимки

### `GET /api/aois`

Все акватории с готовыми данными. Отсюда берутся `aoi` и `date` для остальных ручек. Параметров нет.

| Поле | Тип | Описание |
|---|---|---|
| `id` | string | идентификатор для путей API |
| `name` | string | название |
| `bbox` | number[4] | границы `[lon_min, lat_min, lon_max, lat_max]` |
| `kind` | string | `sea` — море (дрейф: течения + ветер), `inland` — водохранилище или река (только ветер) |
| `port` | [lon, lat] | порт — точка старта в планировщике маршрута и ориентир в отчёте |
| `tz` | int | смещение местного времени от UTC, ч |
| `rivers` | object | устья рек: название → [lon, lat] |
| `note` | string \| null | пояснение к акватории |
| `group` | string | `field` — район полевых данных кейса: снимки подобраны на даты измерений ±1 сут. (как в реестре пар); `monitoring` — оперативный мониторинг |
| `source` | string \| null | источник реестра, для которого подобраны снимки: `S4_BLACK_SEA_DOORS3`, `S3_SE_NORTH_SEA`; null у мониторинга |
| `dates` | string[] | даты снимков по возрастанию; индекс даты — индекс в `series` и `concentration` |
| `basin` | string \| null | морской бассейн (`black_sea`…), от него зависит применимость профилей; null для пресных вод |
| `water_type` | string | `marine` или `inland` |
| `concentration` | object | профиль → `{available, reason}`: есть ли здесь концентрация и почему нет |
| `total_area` | number[] | покрытие мусором всей акватории на каждую дату, м² |

```json
[{
  "id": "sochi", "name": "Сочи — Адлер (Чёрное море)",
  "bbox": [39.55, 43.36, 40.02, 43.62], "kind": "sea", "port": [39.7215, 43.579], "tz": 3,
  "rivers": {"Сочи": [39.7196, 43.5806], "Мзымта": [39.9248, 43.4192], "Псоу": [40.0063, 43.3869]},
  "note": null, "group": "monitoring", "source": null,
  "dates": ["2025-04-18", "2025-04-29", "…", "2026-09-13"],
  "basin": "black_sea", "water_type": "marine",
  "concentration": {
    "A": {"available": false, "reason": "профиль не применим к этой акватории: другой бассейн или пресные воды"},
    "B": {"available": true, "reason": null}
  },
  "total_area": [104.3, 0.0, 191.8, "…", 137.6]
}]
```

### `GET /api/aois/{aoi}`

Одна акватория — то же, что элемент списка `GET /api/aois`.

| Параметр | Где | Описание |
|---|---|---|
| `aoi` | путь | id акватории, например `sochi` |

Ошибки: `404` — акватории нет.

### `GET /api/aois/{aoi}/scenes`

Снимки акватории по датам: сцена, условия съёмки, надёжность, итоги детекции и ссылки на все слои снимка.
Главная ручка, чтобы понять, можно ли верить снимку.

| Параметр | Где | Описание |
|---|---|---|
| `aoi` | путь | id акватории |

| Поле | Тип | Описание |
|---|---|---|
| `date` | string | дата снимка |
| `datetime` | string | момент съёмки, UTC |
| `scene_id`, `platform`, `tile` | string | сцена Sentinel-2 L2A, спутник (2A/2B/2C), тайл MGRS |
| `tile_cloud_pct` | number | облачность всего тайла по метаданным, % |
| `valid_frac` | number | доля воды, пригодной для анализа (0–1) |
| `wind`, `wind_max` | number | ветер в момент съёмки и максимум за сутки до неё, м/с |
| `sea` | string | `спокойное`, `волнение`, `шторм`, `лёд` |
| `storm` | bool | **ненадёжная сцена** (шторм ≥ 8 м/с, лёд или блик > 30% воды): остаются только крупные скопления, «не обнаружено» не ставится, в статистику по времени сцена не идёт |
| `reason` | string \| null | почему сцена ненадёжна |
| `glinty` | bool | сильный солнечный блик |
| `ice_frac` | number | доля воды подо льдом или шугой |
| `n_det`, `n_zones` | int | пикселей 10 м с детекцией и зон |
| `area_m2` | number | покрытие мусором по акватории, м² |
| `cover` | number | покрытие на км² пригодной воды, м²/км² |
| `quality` | object | доли воды по кодам маски качества: `ok`, `cloud`, `shadow`, `cirrus`, `glint`, `ice`, `ship`, `static`, `nodata` |
| `status` | object | число гексов по статусам детекции |
| `corners` | [lon, lat][4] | углы растров: верх-лево, верх-право, низ-право, низ-лево. Растр целиком по ним ложится со сдвигом до 100 м внутри снимка — точная привязка в [`grid`](#get-apiaoisaoigrid) |
| `layers` | object | ссылки: `rgb`, `debris`, `model_classes`, `quality` (растры), `zones`, `points`, `report` |

```json
[{
  "date": "2025-04-18", "datetime": "2025-04-18T08:10:21.024000+00:00",
  "scene_id": "S2A_MSIL2A_20250418T081021_R078_T37TEJ_20250418T120317",
  "platform": "Sentinel-2A", "tile": "37TEJ", "tile_cloud_pct": 0.14,
  "valid_frac": 0.99, "wind": 3.6, "wind_max": 3.6, "sea": "спокойное",
  "storm": false, "reason": null, "glinty": false, "ice_frac": 0.0,
  "n_det": 24, "n_zones": 15, "area_m2": 104.3, "cover": 0.144,
  "quality": {"ok": 0.9895, "cloud": 0.0, "glint": 0.0001, "ship": 0.009, "…": "…"},
  "status": {"not_detected": 1076, "detected": 12, "insufficient_data": 0},
  "corners": [[39.549952, 43.623283], [40.024488, 43.620015], [40.020048, 43.356771], [39.547569, 43.360009]],
  "layers": {
    "rgb": "/data/sochi/2025-04-18/rgb.jpg", "debris": "/data/sochi/2025-04-18/debris.png",
    "model_classes": "/api/aois/sochi/2025-04-18/model-classes",
    "quality": "/data/sochi/2025-04-18/quality.png", "zones": "/api/aois/sochi/2025-04-18/zones",
    "points": "/api/aois/sochi/2025-04-18/points", "report": "/api/report?aoi=sochi&date=2025-04-18"
  }
}]
```

Ошибки: `404` — акватории нет.

### `GET /api/aois/{aoi}/hexes`

Сетка гексов H3 (разрешение 8) по воде акватории — GeoJSON FeatureCollection с полигонами. Геометрия
не меняется по датам: загрузить один раз, дальше красить по массивам `series` и `concentration`.

| Параметр | Где | Описание |
|---|---|---|
| `aoi` | путь | id акватории |

Атрибуты гекса (`properties`):

| Поле | Описание |
|---|---|
| `i` | индекс гекса — второй индекс в массивах `[дата][гекс]` и `{i}` в `hex/{i}` |
| `h3` | ячейка H3 |
| `lon`, `lat` | центр гекса |
| `water_km2` | площадь воды в гексе, км² |
| `dist_coast_km` | расстояние до берега, км |
| `n_obs` | число надёжных наблюдений (видно > 50% воды, сцена надёжна) |
| `persistence` | доля надёжных наблюдений, где был мусор |
| `mean_cover`, `max_cover` | среднее и максимальное покрытие, м²/км² |
| `trend_cover` | наклон покрытия, м²/км² в месяц (от 4 наблюдений) |
| `hot` | индекс устойчивого скопления: `persistence × ln(1 + mean_cover)` |

```json
{"type": "FeatureCollection", "features": [{
  "type": "Feature", "id": 2,
  "geometry": {"type": "Polygon", "coordinates": [[[40.008241, 43.381549], "…"]]},
  "properties": {"i": 2, "h3": "882d560209fffff", "lon": 40.01157, "lat": 43.37727, "water_km2": 0.742,
                 "dist_coast_km": 1.3, "persistence": 0.045, "mean_cover": 1.176, "max_cover": 25.88,
                 "trend_cover": -0.2276, "hot": 0.0353, "n_obs": 22}
}]}
```

### `GET /api/aois/{aoi}/series`

Всё по датам одним файлом — для графиков динамики и анимации по времени.

| Параметр | Где | Описание |
|---|---|---|
| `aoi` | путь | id акватории |

| Поле | Описание |
|---|---|
| `aoi`, `basin`, `water_type` | акватория |
| `dates` | даты; индекс даты — первый индекс массивов ниже |
| `scenes` | метаданные снимков — те же поля, что в `scenes`, без `layers` (плюс служебные `glint`, `noise`, `n_raw`) |
| `cover` | `[дата][гекс]` покрытие, м²/км² |
| `ndet` | `[дата][гекс]` пикселей с детекцией |
| `valid` | `[дата][гекс]` доля пригодной воды |
| `status` | `[дата][гекс]` код статуса детекции |
| `status_codes` | код → статус: `0` not_detected, `1` detected, `2` insufficient_data |
| `cover_class_edges` | границы классов покрытия, м²/км²: `[0, 15, 40, 100]` — низкое, умеренное, высокое, очень высокое |
| `detector` | порог детектора `p_det` и хеш конфига |

Пример чтения: покрытие гекса `i = 2` на дату `2026-09-13` — `cover[dates.index("2026-09-13")][2]`.

### `GET /api/aois/{aoi}/grid`

Привязка растров снимка к карте. Растры лежат в сетке UTM 10 м, а в меркаторе веб-карты эта сетка не
прямоугольник и не трапеция: если растянуть растр целиком по четырём `corners`, середина снимка уезжает
до 100 м от зон и гексов. Здесь узлы сетки через `step` пикселей — растр режется на куски, углы каждого куска
берутся билинейно между узлами (пример — в [растровых слоях](#растровые-слои-статика)). Сетка одна на все даты.

| Параметр | Где | Описание |
|---|---|---|
| `aoi` | путь | id акватории |

| Поле | Тип | Описание |
|---|---|---|
| `width`, `height` | int | размер сетки 10 м в пикселях — это размер `debris.png` |
| `step` | int | шаг узлов, пикселей сетки 10 м |
| `lonlat` | [lon, lat][строка][столбец] | узел `[j][i]` — точка (столбец `i·step`, строка `j·step`), угол пикселя; узлы накрывают снимок с запасом |

```json
{"width": 3829, "height": 2924, "step": 256,
 "lonlat": [[[39.5499524, 43.6232829], [39.5816812, 43.6231258], "…"], "…"]}
```

Ошибки: `404` — акватории нет.

---

## Детекции на снимке

### `GET /api/aois/{aoi}/{date}/zones`

Зоны вероятного скопления плавающего мусора на снимке — связные группы пикселей 10 м, где детектор видит
мусор. GeoJSON FeatureCollection, полигоны. Пустой список — на снимке ничего не найдено (статус снимка
смотрите в `scenes`: «не обнаружено» и «недостаточно данных» — разные вещи).

| Параметр | Где | Описание |
|---|---|---|
| `aoi` | путь | id акватории |
| `date` | путь | дата снимка `YYYY-MM-DD` |

Атрибуты зоны (`properties`):

| Поле | Описание |
|---|---|
| `zone_id` | `<aoi>-<date>-NNN` — сквозной номер зоны на снимке |
| `aoi`, `date`, `scene_id`, `scene_datetime_utc` | откуда зона |
| `lon`, `lat`, `h3` | центр зоны и его ячейка H3 |
| `n_pixels`, `zone_area_km2` | размер зоны: пикселей 10 м и км² |
| `cover_m2` | эквивалентная площадь мусора в зоне, м² |
| `cover_m2_km2` | та же площадь на км² зоны |
| `p_mean`, `p_max` | средняя и максимальная вероятность мусора по пикселям зоны |
| `detection_status`, `detection_status_ru` | всегда `detected` / «обнаружено» |
| `valid_frac_scene`, `wind_ms`, `sea`, `scene_reliable` | условия съёмки всей сцены |
| `field_event_id`, `field_profile`, `field_date`, `field_distance_km`, `field_date_gap_days`, `field_conc_items_km2` | ближайшее полевое измерение: какое, как далеко и насколько раньше |
| `conc_<P>_items_km2` | модельная концентрация профиля `P` в центре зоны, шт./км²; null — недоступна |
| `conc_<P>_lo80`, `_hi80`, `_lo95`, `_hi95` | 80% и 95% интервалы |
| `conc_<P>_status`, `_status_ru`, `_reasons` | статус и причины статуса через `; ` |
| `conc_<P>_model` | версия модели |

```json
{"type": "FeatureCollection", "features": [{
  "type": "Feature",
  "geometry": {"type": "Polygon", "coordinates": [[[39.72121831, 43.55867153], "…"]]},
  "properties": {
    "zone_id": "sochi-2026-09-13-001", "aoi": "sochi", "date": "2026-09-13",
    "scene_id": "S2A_MSIL2A_20260913T082021_R121_T37TEJ_20260913T115558",
    "lon": 39.72128, "lat": 43.55849, "h3": "882d568e47fffff", "n_pixels": 2, "zone_area_km2": 0.0008,
    "cover_m2": 11.8, "cover_m2_km2": 14750.0, "p_mean": 0.951, "p_max": 0.984,
    "detection_status": "detected", "detection_status_ru": "обнаружено",
    "valid_frac_scene": 1.0, "wind_ms": 2.5, "sea": "спокойное", "scene_reliable": true,
    "field_event_id": "S4:DOORS3:T17", "field_distance_km": 183.1, "field_date_gap_days": 831,
    "conc_B_items_km2": 591.0, "conc_B_lo80": 126.1, "conc_B_hi80": 1985.7,
    "conc_B_status": "research_estimate", "conc_B_status_ru": "исследовательская оценка",
    "conc_B_reasons": "ближайшее полевое измерение в 183 км (> 60 км); месяц 9 вне месяцев обучения [6]; …",
    "conc_A_items_km2": null, "conc_A_status": "unavailable", "conc_A_reasons": "вне бассейна профиля: nw_atlantic_subtropical"
  }
}]}
```

Ошибки: `404` — нет акватории или снимка на эту дату; `422` — дата не в формате `YYYY-MM-DD`.

### `GET /api/aois/{aoi}/{date}/points`

Каждый пиксель 10 м с детекцией — массив `[lon, lat, p, frac]`:

| Позиция | Описание |
|---|---|
| `lon`, `lat` | центр пикселя |
| `p` | вероятность мусора по детектору, 0–1 |
| `frac` | доля пикселя, занятая мусором; эквивалентная площадь = `frac × 100` м² |

Это затравки для прогноза дрейфа и кандидаты для маршрута.

```json
[[39.72128, 43.55854, 0.98, 0.07], [39.72128, 43.55845, 0.92, 0.05], [39.76594, 43.53592, 0.63, 0.12]]
```

Ошибки: как у `zones`.

### `GET /api/zones`

Скопления мусора — зоны детекции со **всех** обработанных снимков всех акваторий одним плоским JSON, без
GeoJSON-обёртки: удобно для таблиц, скриптов и сторонних сервисов. Поля зоны — те же, что в
[`export?layer=zones`](#get-apiexport), плюс `aoi_name` и `scene_reliable`.

| Параметр | Где | Описание |
|---|---|---|
| `aoi` | query | акватории через запятую (`batumi,sochi`); по умолчанию все |
| `date` | query | только снимок этой даты, `YYYY-MM-DD` |
| `date_from`, `date_to` | query | период снимков, границы включительно |
| `bbox` | query | центр зоны внутри рамки `lon_min,lat_min,lon_max,lat_max` |
| `min_cover_m2` | query | покрытие мусором в зоне не меньше, м² (по умолчанию 0) |
| `reliable_only` | query | `true` — только надёжные сцены: без шторма, льда и сильного блика |
| `profile` | query | чья концентрация в `conc_*` (по умолчанию `B`) |
| `sort` | query | `cover` — по убыванию покрытия (по умолчанию), `date` — сначала новые снимки, `p` — по убыванию `p_max` |
| `limit`, `offset` | query | страница: 1–10000 зон (по умолчанию 100), сколько пропустить |
| `geometry` | query | `true` — добавить контур зоны в поле `geometry` (GeoJSON) |

Ответ: `total` — сколько зон подходит под фильтры, `count` — сколько в этом ответе, `offset`, `limit`,
`profile`, `sort` и `items`. Главные поля зоны:

| Поле | Описание |
|---|---|
| `zone_id`, `aoi`, `aoi_name` | зона и акватория |
| `date`, `scene_datetime_utc`, `scene_id` | когда снято и какой сценой |
| `lat`, `lon`, `h3` | центр зоны и ячейка H3 |
| `zone_area_km2`, `n_pixels` | размер зоны |
| `cover_m2`, `cover_m2_km2` | эквивалентная площадь мусора в зоне, м², и она же на км² зоны |
| `p_mean`, `p_max` | вероятность мусора по детектору |
| `conc_items_km2`, `conc_lo80`…`conc_hi95`, `conc_status`, `conc_reasons` | модельная концентрация профиля, шт./км², интервалы и статус |
| `valid_frac_scene`, `wind_ms`, `sea`, `scene_reliable` | условия съёмки |
| `field_event_id`, `field_distance_km`, `field_date_gap_days`, `field_conc_items_km2` | ближайшее полевое измерение |

```json
{"total": 103, "count": 1, "offset": 0, "limit": 1, "profile": "B", "sort": "cover",
 "items": [{"zone_id": "batumi-2024-06-05-102", "aoi": "batumi",
            "aoi_name": "Батуми — Кобулети (Грузия, полевые данные DOORS 2024)",
            "date": "2024-06-05", "scene_datetime_utc": "2024-06-05T08:06:09.024000+00:00",
            "lat": 41.64163, "lon": 41.6016, "h3": "882c21c939fffff",
            "zone_area_km2": 0.001, "n_pixels": 3, "cover_m2": 35.3, "p_mean": 0.992, "p_max": 0.996,
            "unit": "шт./км²", "conc_items_km2": 662.6, "conc_lo80": 141.5, "conc_hi80": 2226.1,
            "conc_status": "research_estimate",
            "conc_reasons": "log_dist_coast_km = 0.9 вне диапазона обучения [1.7; 5.0]",
            "wind_ms": 2.7, "sea": "спокойное", "scene_reliable": true,
            "field_event_id": "S4:DOORS3:T21", "field_distance_km": 6.7, "field_conc_items_km2": 67.34,
            "geometry": null, "…": "…"}]}
```

Покрытие и площадь зоны — показатели детектора, в шт./км² они не переводятся. Пустая выборка — `total: 0`, не
ошибка. Ошибки: `404` — неизвестная акватория в `aoi`; `422` — неверная дата, `bbox`, `sort`, `limit` или профиль.

---

## Концентрация

### `GET /api/profiles`

Профили концентрации — что именно считается в шт./км²: материал, класс размера, метод полевого учёта
и бассейны, где модель применима. Профили — разные совокупности: складывать или сравнивать их нельзя.
Параметров нет.

| Поле | Описание |
|---|---|
| `id` | буква профиля — значение параметра `profile` |
| `code` | код профиля в реестре |
| `label`, `material`, `size_class`, `method`, `unit` | что и как считали |
| `primary` | основной профиль кейса |
| `show_in_ui` | `false` — профиль есть в реестре, но модели для него нет |
| `basins` | бассейны, где модель применима |
| `model_version`, `model_type` | обученная модель (нет у профилей без модели) |
| `n_train`, `n_holdout` | событий в обучении и в отложенной выборке |
| `holdout_mae` | MAE на отложенной выборке, шт./км²: основная модель и базовые `median`, `idw` |

```json
[{
  "id": "B", "code": "litter_visual_gt2",
  "label": "Весь плавающий макромусор ≳2,5 см (не только пластик)",
  "material": "все материалы, преимущественно пластик",
  "size_class": ">2,5 см (S4); >2 см (S3, только проверка переноса)",
  "method": "судовой визуальный учёт в полосе", "unit": "шт./км²",
  "primary": false, "show_in_ui": true, "basins": ["black_sea"],
  "model_version": "conc-B-c6bce5631d2b", "model_type": "loglinear_ridge",
  "n_train": 26, "n_holdout": 7,
  "holdout_mae": {"loglinear_ridge": 230.9, "median": 132.3, "idw": 198.7}
}]
```

### `GET /api/aois/{aoi}/concentration`

Модельная концентрация профиля в центре каждого гекса на момент каждого снимка — компактными массивами.

| Параметр | Где | По умолчанию | Описание |
|---|---|---|---|
| `aoi` | путь | — | id акватории |
| `profile` | query | `B` | профиль |

| Поле | Описание |
|---|---|
| `profile`, `unit` | профиль и `шт./км²` |
| `dates` | те же даты и в том же порядке, что в `series` |
| `available` | `false` — профиль к акватории не применим; тогда есть `reason` и нет массивов. Это не ошибка |
| `status_codes` | код → статус: `0` unavailable, `1` model_estimate, `2` research_estimate |
| `model_version`, `model_type` | модель |
| `nearest_field_km` | `[гекс]` до ближайшего полевого измерения профиля, км |
| `value` | `[дата][гекс]` концентрация, шт./км² |
| `lo80`, `hi80` | `[дата][гекс]` 80%-интервал |
| `status` | `[дата][гекс]` код статуса |

```json
{
  "profile": "B", "unit": "шт./км²", "dates": ["2025-04-18", "…"], "available": true,
  "status_codes": {"0": "unavailable", "1": "model_estimate", "2": "research_estimate"},
  "model_version": "conc-B-c6bce5631d2b", "model_type": "loglinear_ridge",
  "nearest_field_km": [169.3, 168.6, "…"],
  "value": [[666.3, 591.0, "…"], "…"], "lo80": [[142.3, 126.1, "…"], "…"],
  "hi80": [[2238.3, 1985.6, "…"], "…"], "status": [[2, 2, "…"], "…"]
}
```

Профиль не применим:

```json
{"profile": "A", "unit": "шт./км²", "dates": ["…"], "available": false,
 "reason": "профиль не применим к этой акватории: другой бассейн или пресные воды",
 "status_codes": {"0": "unavailable", "1": "model_estimate", "2": "research_estimate"},
 "model_version": "conc-A-c6bce5631d2b", "model_type": "nb_glm"}
```

Ошибки: `404` — нет акватории; `422` — неизвестный профиль.

### `GET /api/aois/{aoi}/{date}/hex/{i}`

Концентрация в центре одного гекса на момент снимка — с интервалами, статусом, **причинами статуса**
и признаками модели. Плюс статус детекции того же гекса. Считается на лету той же моделью, что и слой.

| Параметр | Где | По умолчанию | Описание |
|---|---|---|---|
| `aoi` | путь | — | id акватории |
| `date` | путь | — | дата снимка |
| `i` | путь | — | индекс гекса `properties.i`, ≥ 0 |
| `profile` | query | `B` | профиль |

| Поле | Описание |
|---|---|
| `conc_items_km2` | концентрация, шт./км²; null — недоступна |
| `lo80`, `hi80`, `lo95`, `hi95` | интервалы |
| `status`, `status_ru` | статус концентрации |
| `reasons` | почему такой статус: далеко от полевых данных, другой сезон, признак вне диапазона обучения |
| `nearest_field_km` | до ближайшего полевого измерения профиля, км |
| `features` | признаки, на которых считалась модель |
| `model_version`, `model_type` | модель |
| `detection_status`, `detection_status_ru` | статус детекции гекса на эту дату |
| `cover_m2_km2`, `valid_frac` | покрытие по детектору и доля пригодной воды гекса |

```json
{
  "aoi": "sochi", "date": "2026-09-13", "hex": 5, "h3": "882d560229fffff", "profile": "B", "unit": "шт./км²",
  "conc_items_km2": 590.9, "lo80": 126.1, "hi80": 1985.3, "lo95": 78.6, "hi95": 3717.4,
  "status": "research_estimate", "status_ru": "исследовательская оценка",
  "reasons": ["ближайшее полевое измерение в 168 км (> 60 км)", "месяц 9 вне месяцев обучения [6]",
              "log_dist_coast_km = 1.2 вне диапазона обучения [1.7; 5.0]"],
  "nearest_field_km": 168.0,
  "features": {"lon": 40.022, "lat": 43.356, "dist_coast_km": 2.294, "log_dist_coast_km": 1.192, "wind24_ms": 3.599},
  "model_version": "conc-B-c6bce5631d2b", "model_type": "loglinear_ridge",
  "detection_status": "not_detected", "detection_status_ru": "не обнаружено",
  "cover_m2_km2": 0.0, "valid_frac": 1.0
}
```

Ошибки: `404` — нет акватории, снимка или гекса с таким индексом; `422` — неверная дата, профиль или `i < 0`.

---

## Полевые данные

### `GET /api/field`

Полевые измерения концентрации из реестра кейса (`value_type = measurement`) — GeoJSON. Геометрия — полоса
учёта (`LineString`) или точка, если трек в источнике не опубликован. Пустая выборка — пустая коллекция.

| Параметр | Где | Описание |
|---|---|---|
| `profile` | query | только этот профиль |
| `bbox` | query | только внутри рамки `lon_min,lat_min,lon_max,lat_max` |
| `date_from`, `date_to` | query | период `YYYY-MM-DD`, границы включительно |

Главные атрибуты измерения:

| Поле | Описание |
|---|---|
| `event_id` | id события — для `GET /api/field/{event_id}` |
| `profile`, `role` | профиль; роль: `train` — обучение модели, `transfer_check` — только проверка переноса |
| `date_utc`, `t_start_utc`, `t_end_utc`, `time_known` | когда; известно ли время наблюдения |
| `length_km`, `width_m`, `area_km2` | полоса учёта: длина, ширина, площадь A |
| `n_items` | число предметов N |
| `conc_items_km2` | C = N / A, шт./км² |
| `conc_lo95`, `conc_hi95` | 95% ДИ по Гарвуду |
| `conc_source` | `n_over_a` — посчитано из N и A; иначе опубликованное значение |
| `target_scope`, `material`, `size_class`, `sampling_method`, `quality_flags` | что считали и оговорки |
| `source_id`, `source_doi`, `source_license`, `provenance` | источник и лицензия |

```bash
curl "http://localhost:8000/api/field?profile=B&bbox=27,40.5,42,47&date_from=2024-01-01"
```

Ошибки: `422` — неверный `bbox`, дата или профиль.

### `GET /api/field/objects`

Отдельные предметы из реестра — точки: где и что видели (`item_type`, `category`, `material`).
Это контекст для карты, а не метки концентрации: по отдельным предметам C = N / A не считается
(`value_type = object_context`). Параметров нет.

```json
{"type": "FeatureCollection", "features": [{
  "type": "Feature", "geometry": {"type": "Point", "coordinates": [7.666, 54.085]},
  "properties": {"sample_id": "MPL-0003", "event_id": "S3:HE419_MarLitter_transect01",
                 "source_id": "S3_SE_NORTH_SEA", "date_utc": "2014-04-03", "item_type": "plastic fragment",
                 "category": "plastic", "material": "plastic", "position_role": "sighting_position",
                 "value_type": "object_context"}
}]}
```

### `GET /api/field/sources`

Состав наблюдений кейса по источникам — как в постановке. Параметров нет. Для каждого источника: сколько строк,
событий и измерений на карте, период и границы наблюдений, итог реестра пар и акватории сервиса со снимками на даты
измерений.

| Источник | Район | Что есть в архивах снимков |
|---|---|---|
| S1 | Тихий океан (мусорное пятно), июль 2015 — октябрь 2016 | ни одной сцены Sentinel-2 и Landsat ни в Planetary Computer, ни в Copernicus Data Space, ни в Earth Search: открытый океан вдали от берега они не снимают; есть только Sentinel-3 OLCI 300 м за октябрь 2016 |
| S2 | Саргассово море, апрель 2015 | до запуска Sentinel-2A (23.06.2015); две трансекты попадают на сцену Landsat-8 — детектор к ней не применим, снимок открывается по `tiles_url` |
| S3 | Северное море, апрель 2014 и 2016 | 2016 — Sentinel-2 (`s3_helgoland`, `s3_bight_nw`); 2014 — только Landsat-7/8 |
| S4 | Чёрное море, 2–18 июня 2024 | Sentinel-2 почти на все даты: `batumi` и `s4_*` |

| Поле | Описание |
|---|---|
| `source_id`, `code` | источник реестра и короткий код из постановки (`S1`…`S4`) |
| `region`, `area`, `observations`, `features` | район, рейс, вид наблюдений и особенности — как в постановке |
| `imagery` | что есть в архивах снимков для источника |
| `n_rows`, `n_events` | строк и событий реестра |
| `n_measurements` | событий с полевым измерением C = N / A на карте (`GET /api/field`) |
| `profiles` | измерения по профилю и роли: `B/train`, `B/transfer_check`, `C/optional`… |
| `date_from`, `date_to`, `bbox` | период и границы наблюдений |
| `pairs` | `events_by_outcome` — события по итогу пар (accepted / context / rejected), `reasons` — строки реестра пар по причинам, `scenes` — найдено сцен по коллекциям |
| `aois` | акватории сервиса со снимками на даты измерений: `{id, name, dates}` |

```json
[{"source_id": "S1_GPGP2018", "code": "S1", "region": "Тихий океан", "n_rows": 350, "n_events": 181,
  "n_measurements": 83, "profiles": {"C/optional": 83}, "date_from": "2015-07-25", "date_to": "2016-10-06",
  "pairs": {"events_by_outcome": {"rejected": 83}, "reasons": {"NO_SCENE": 83}, "scenes": {}}, "aois": [], "…": "…"},
 {"source_id": "S4_BLACK_SEA_DOORS3", "code": "S4", "region": "Чёрное море", "n_events": 33,
  "pairs": {"events_by_outcome": {"context": 21, "accepted": 9, "rejected": 3}, "…": "…"},
  "aois": [{"id": "s4_zonguldak", "name": "S4 DOORS · к северу от Зонгулдака (T30–T32)", "dates": ["2024-06-17"]}]}]
```

### `GET /api/field/{event_id}`

Как получено число по событию — для проверки расчёта без чтения кода.

| Параметр | Где | Описание |
|---|---|---|
| `event_id` | путь | id события из `GET /api/field`, например `S3:HE419_MarLitter_transect01` |

| Поле | Описание |
|---|---|
| `rows` | все строки реестра с этим событием и решение по каждой: `decision` included/excluded, `reason_code`, `reason_text` |
| `measurements` | принятые измерения (поля как в `field`) плюс `formula` — расчёт текстом |
| `pairs` | снимки, найденные для события, и решение по каждой паре (см. [реестр пар](#реестр-пар)). У пары со сценой ещё `collection`; `tiles_url` — XYZ-тайлы сцены в естественных цветах (Planetary Computer, без ключа; `{z}/{x}/{y}` подставляет карта); `scene_bbox` — границы сцены; `aoi`, `aoi_date` — обработанный снимок сервиса на ту же дату или null |
| `aois` | акватории сервиса, внутри которых лежит событие: `{id, name, dates}` |

```json
{
  "event_id": "S3:HE419_MarLitter_transect01",
  "rows": [
    {"sample_id": "MPL-0001", "record_type": "transect_density", "target_scope": "all_litter",
     "decision": "included", "profile": "B", "role": "transfer_check", "reason_code": "", "reason_text": ""},
    {"sample_id": "MPL-0002", "target_scope": "fisheries_litter_category", "decision": "excluded",
     "reason_code": "subpopulation_not_target",
     "reason_text": "Отдельная категория мусора — подсовокупность, другая целевая величина"}
  ],
  "measurements": [{"event_id": "S3:HE419_MarLitter_transect01", "n_items": 4.0, "area_km2": 0.251,
                    "conc_items_km2": 15.94, "formula": "C = N / A = 4 / 0.251 км² = 15.94 шт./км²", "…": "…"}],
  "pairs": [{"collection": "landsat-c2-l2", "scene_id": "LE07_L2SP_198022_20140402_02_T1", "decision": "rejected",
             "reason_code": "SENSOR_UNSUPPORTED",
             "reason_text": "есть 2 сцен(ы) landsat-c2-l2, но детектору нужен красный край Sentinel-2",
             "tiles_url": "https://planetarycomputer.microsoft.com/api/data/v1/item/tiles/WebMercatorQuad/{z}/{x}/{y}@1x?collection=landsat-c2-l2&item=LE07_L2SP_198022_20140402_02_T1&…",
             "scene_bbox": [4.63555, 53.50931, 8.32963, 55.50167], "aoi": null, "aoi_date": null}],
  "aois": []
}
```

Ошибки: `404` — события нет.

---

## Реестр пар

### `GET /api/pairs`

Для каждого полевого события — снимки Sentinel-2 в окне ±1 сутки и решение по паре: принята для сравнения,
оставлена как контекст или отклонена — с причиной.

| Параметр | Где | По умолчанию | Описание |
|---|---|---|---|
| `format` | query | `json` | `json` — сводка и строки с признаками детектора; `csv` — файл `pairs.csv` |

Ответ JSON: `summary` — сводка (события, кандидаты, исходы по событиям, строки по причинам, принятые по
профилю и классу синхронности, метаданные сборки), `rows` — строки реестра.

| Поле строки | Описание |
|---|---|
| `pair_id`, `event_id`, `profile`, `role` | пара и событие |
| `scene_id`, `scene_datetime_utc`, `platform`, `tile`, `tile_cloud_pct` | снимок |
| `dt_min_h`, `dt_max_h`, `abs_dt_max_h` | сдвиг снимка относительно наблюдения, ч |
| `sync_tier` | класс синхронности: `A` — время наблюдения известно и сдвиг ≤ 3 ч; `B` — сдвиг ≤ 18 ч (или ≤ 3 ч, но время неизвестно); `C` — больше 18 ч |
| `drift_buffer_km`, `footprint_area_km2`, `footprint_coverage` | буфер на дрейф за время сдвига, след, покрытие следа данными |
| `clear_water_frac`, `cloud_frac`, `glint_b11` | качество снимка в следе |
| `decision` | `accepted`, `context`, `rejected` |
| `reason_code`, `reason_text` | причина решения, см. ниже |
| `det_px`, `det_per_km2`, `n_zones`, `cover_m2_km2`, `p95` | признаки детектора в следе (у принятых пар) |

| `reason_code` | Решение | Смысл |
|---|---|---|
| `ACCEPTED` | accepted | сдвиг ≤ 3 ч, чистая вода — пара для количественного сравнения |
| `ACCEPTED_UNCERTAIN_TIME` | accepted | время неизвестно или сдвиг несколько часов — только исследовательское сравнение |
| `UNRELIABLE_SYNC` | context | сдвиг больше 18 ч, мусор мог уплыть — только контекст |
| `NO_SCENE` | rejected | нет сцен Sentinel-2 L2A в окне ±1 сут. |
| `SENSOR_UNSUPPORTED` | rejected | есть только сцены другого сенсора (Landsat), детектору нужен красный край Sentinel-2 |
| `INSUFFICIENT_COVERAGE` | rejected | данные сцены покрывают малую часть следа |
| `UNUSABLE_PIXELS` | rejected | мало чистой воды — облака |
| `GLINT` | rejected | сильный блик |
| `DUPLICATE_BETTER_SCENE` | rejected | для события выбрана лучшая сцена |

Ошибки: `404` — реестр не построен (`python -m pipeline.pairs build`); `422` — неверный `format`.

---

## Дрейф и маршрут

Дрейф считается по течениям и ветру из Open-Meteo от момента съёмки: интегрирование RK2 с шагом 30 мин,
парусность 2% ветра на море и 3% на внутренних водах, в ансамбле — разброс парусности ±50% и турбулентная
диффузия. Для `inland`-акваторий течений нет, только ветер. Частица, коснувшаяся берега, считается выброшенной.
Первый запрос на дату скачивает погоду, поэтому медленнее; дальше — кеш. Если Open-Meteo недоступен или
ограничил частоту запросов, ручки раздела отвечают `503` — повторить через минуту.

Там, где снимка нет, — [`GET /api/drift`](#get-apidrift): та же модель, но поля берутся из реанализов
Copernicus Marine, а старт — любая морская точка и любой момент.

### `GET /api/aois/{aoi}/{date}/drift`

Куда унесёт обнаруженный мусор: до 300 самых крупных детекций × 4 члена ансамбля.

| Параметр | Где | По умолчанию | Описание |
|---|---|---|---|
| `aoi`, `date` | путь | — | акватория и дата снимка |
| `hours` | query | `72` | горизонт, ч (6–90) |

| Поле | Описание |
|---|---|
| `n` | частиц; `0` — на дату нет детекций (тогда `frames` и `hexes` пусты) |
| `hours`, `t0` | горизонт и старт (момент съёмки) |
| `frames` | `[час][частица]` → `[lon, lat]`; кадр 0 — положение на снимке |
| `beached` | `[частица]` 1 — выброшена на берег |
| `hexes` | распределение на 24 ч, 48 ч и на горизонт: `"часы" → {ячейка H3 → м² мусора}` |

```json
{"n": 96, "hours": 72, "t0": "2026-09-13T08:20:21.024000+00:00",
 "frames": [[[39.72128, 43.55854], "…"], "…"], "beached": [1, 0, "…"],
 "hexes": {"24": {"882d5680b7fffff": 4.58, "…": 0}, "48": {"…": 0}, "72": {"…": 0}}}
```

### `GET /api/aois/{aoi}/{date}/drift_point`

Ансамбль траекторий из одной точки — «конус неопределённости»: куда может уйти предмет из этой точки.

| Параметр | Где | По умолчанию | Описание |
|---|---|---|---|
| `aoi`, `date` | путь | — | акватория и дата снимка (старт — момент съёмки) |
| `lon`, `lat` | query | — | точка старта, обязательно |
| `hours` | query | `72` | горизонт, ч (6–90) |
| `n` | query | `40` | членов ансамбля (1–200) |

| Поле | Описание |
|---|---|
| `tracks` | `[член][час]` → `[lon, lat]` |
| `center` | `[час]` центр ансамбля |
| `spread_km` | `[час]` разброс от центра (СКО), км — ширина конуса |
| `beached_frac` | доля выброшенных на берег |
| `t0` | старт, UTC |

```json
{"tracks": [[[39.72, 43.55], [39.71579, 43.55774], "…"], "…"],
 "center": [[39.72, 43.55], [39.71503, 43.55687], "…"],
 "spread_km": [0.0, 0.11, 0.27, 0.28, 0.56, "…"], "beached_frac": 0.0,
 "t0": "2026-09-13T08:20:21.024000+00:00"}
```

### `GET /api/drift`

Тот же конус, что у `drift_point`, но без акватории и снимка: из любой морской точки с любого момента. Нужен
там, где снимков нет, — например, из места полевого измерения S1, S2 или рейса HE419 (2014–2016 гг.). Течений
Open-Meteo на эти годы тоже нет: они начинаются с 2022 г.

Поля — реанализы Copernicus Marine в рамке вокруг точки (полуширина — сколько частица пройдёт за горизонт при 1 м/с):

- течения: на шельфе Северного моря — NWS (~7 км, ежечасно, с приливом), иначе GLORYS12 (1/12°, среднесуточные,
  без прилива);
- стоксов дрейф волн: реанализ волн (0,2°, раз в 3 ч);
- ветер 10 м: L4 по скаттерометрам и модели (0,125°, ежечасно; до 2007 г. — 0,25°).

Суша — где у реанализа течений нет морских клеток и по глобальной маске суши (~1 км). Это реконструкция задним
числом: второго наблюдения того же мусора нет, точность не проверена.

Нужен бесплатный аккаунт Copernicus Marine: логин и пароль — в `COPERNICUSMARINE_SERVICE_USERNAME` и
`COPERNICUSMARINE_SERVICE_PASSWORD` в файле `.env` (образец — `.env.example`) или в окружении. Без них — `503`. Первый запрос для
места и дня скачивает поля (до минуты), дальше они берутся из кеша — в том числе для соседних точек того же дня
(центр рамки округляется до 0,25°).

| Параметр | Где | По умолчанию | Описание |
|---|---|---|---|
| `lon`, `lat` | query | — | точка старта, обязательно; широта до ±80° |
| `t0` | query | — | момент старта, ISO 8601, обязательно; без часового пояса — UTC |
| `hours` | query | `72` | горизонт, ч (6–90) |
| `n` | query | `40` | членов ансамбля (1–200) |

Поля ответа — как у `drift_point`, плюс `sources` — откуда течения, стоксов дрейф и ветер. `404` — на эти даты
реанализа нет (течения — с 1993 г., ветер — с 01.06.1994; конец — несколько месяцев назад, реанализ отстаёт)
или точка старта на суше.

Пример — трансекта S1 1.3 (Тихий океан, 27.07.2015, старт — середина трансекты): за 72 ч центр ансамбля ушёл
на ~85 км к западу; поля скачались за ~15 с, повтор — из кеша.

```json
{"tracks": [[[-134.056, 29.072], [-134.06941, 29.07223], "…"], "…"],
 "center": [[-134.056, 29.07201], [-134.06985, 29.07373], "…"],
 "spread_km": [0.0, 0.28, 0.42, 0.56, 0.71, "…"], "beached_frac": 0.0,
 "t0": "2015-07-27T17:23:00+00:00",
 "sources": [
  "течения: GLORYS12, реанализ Copernicus Marine (1/12°, среднесуточные, без прилива)",
  "стоксов дрейф: реанализ волн Copernicus Marine (0,2°, раз в 3 ч)",
  "ветер 10 м: L4 по скаттерометрам и модели, Copernicus Marine (0,125°, ежечасно)"]}
```

### `GET /api/aois/{aoi}/{date}/flow`

Поля, по которым считается дрейф, — чтобы показать их на карте: течения и ветер 10 м на сетке вокруг акватории,
кадры через 1 ч от момента съёмки. Кадр `h` — это час `h` прогноза дрейфа. Интерфейс рисует их бегущими штрихами
(слои «Течения» и «Ветер»), ползунок прогноза дрейфа двигает и их.

| Параметр | Где | По умолчанию | Описание |
|---|---|---|---|
| `aoi`, `date` | путь | — | акватория и дата снимка |
| `hours` | query | `72` | последний кадр, ч (6–90) |

| Поле | Описание |
|---|---|
| `t0`, `hours` | кадр 0 — момент съёмки, UTC; последний кадр |
| `lons`, `lats` | узлы сетки с запада на восток и с юга на север (не больше 32 по большей стороне) |
| `currents` | `{u, v}`: `[час][ячейка]` → м/с, ячейки построчно с юга на север; `null` в ячейке — суша. У внутренних водоёмов весь `currents` — `null` |
| `wind` | `{u, v}` ветра 10 м в том же формате |
| `water` | маска воды для штрихов течений: `lon0`, `lat0`, `step`, `nx`, `ny` и `bits` — строка из «1» (вода) и «0» (суша) построчно с юга на север; по маске снимка, за её краем — по глобальной маске суши |
| `sources` | откуда поля |

```json
{"t0": "2026-09-11T08:16:01.025000+00:00", "hours": 72,
 "lons": [39.3, 39.4, "…"], "lats": [43.11, 43.21, "…"],
 "currents": {"u": [[0.14, 0.15, "…"], "…"], "v": [[-0.02, -0.03, "…"], "…"]},
 "wind": {"u": [[1.8, 2.0, "…"], "…"], "v": [[-1.0, -1.3, "…"], "…"]},
 "water": {"lon0": 39.3, "lat0": 43.11, "step": 0.0035, "nx": 257, "ny": 200, "bits": "1111…0000"},
 "sources": ["течения: Meteo-France SMOC через Open-Meteo (1/12°, ежечасно, с приливом и стоксовым дрейфом)",
             "ветер 10 м: ERA5 / прогноз через Open-Meteo (ежечасно)"]}
```

### `GET /api/flow`

То же, что `flow`, но вокруг точки без снимка — поля `/api/drift` из реанализов Copernicus Marine в той же рамке.
Параметры — `lon`, `lat`, `t0` (кадр 0) и `hours`, как у [`/api/drift`](#get-apidrift). Нужен аккаунт Copernicus
Marine (без него — `503`), точка на суше — `404`. Ответ — ~1 МБ: сетка прорежена до 32 узлов по большей стороне.

### `GET /api/aois/{aoi}/{date}/accumulation`

Куда течения и ветер сгоняют плавающий мусор независимо от детекций: частицы засеваются равномерно по воде
с шагом 600 м и прогоняются 72 ч. Первый расчёт на дату занимает до минуты, результат сохраняется.

| Поле | Описание |
|---|---|
| `factor` | ячейка H3 → во сколько раз частиц в ней через 72 ч больше, чем в средней ячейке на старте. `> 1` — зона вероятного скопления |
| `beached_frac` | доля частиц, выброшенных на берег |
| `n` | частиц засеяно |

```json
{"factor": {"882d5695a1fffff": 8.565, "882d545b2dfffff": 3.527, "882d569419fffff": 0.504, "…": 0},
 "beached_frac": 0.138, "n": 1971}
```

### `GET /api/aois/{aoi}/{date}/route`

Маршрут судна-сборщика из порта за 12-часовую смену. Кандидаты — до 25 гексов с наибольшим покрытием
на снимке. На каждом шаге выбирается цель с наибольшим покрытием на час пути, причём идём не туда, где цель
была на снимке, а туда, куда её снесёт к прибытию. На каждой остановке — 15 мин обследования.

| Параметр | Где | По умолчанию | Описание |
|---|---|---|---|
| `aoi`, `date` | путь | — | акватория и дата снимка |
| `n` | query | `8` | максимум остановок (1–20) |
| `speed` | query | `12` | скорость судна, узлы (>1 … 40) |
| `delay` | query | `6` | выход из порта через столько часов после съёмки (0–48) |

| Поле | Описание |
|---|---|
| `stops[]` | остановки: `order`, `h3`, `area_m2`, `n_pixels`, `observed` (где цель была на снимке), `predicted` (где будет к прибытию), `drift_km`, `eta_h` (ч от съёмки), `eta` (UTC), `leg_km` (переход от предыдущей точки) |
| `line` | линия маршрута `[lon, lat][]`: порт → остановки → порт |
| `port` | порт выхода |
| `total_km`, `duration_h` | длина маршрута с возвратом и время в море |
| `speed_kn`, `delay_h`, `pass_time` | параметры и момент съёмки |
| `covered_m2`, `total_m2` | покрытие целей маршрута и всех кандидатов, м² |
| `note` | пояснение, если на дату нет детекций; иначе null |

```json
{
  "stops": [{"order": 1, "h3": "882d568e47fffff", "area_m2": 11.5, "n_pixels": 2,
             "observed": [39.72128, 43.5585], "predicted": [39.72016, 43.57756], "drift_km": 2.12,
             "eta_h": 6.01, "eta": "2026-09-13T14:20+00:00", "leg_km": 0.19}, "…"],
  "line": [[39.7215, 43.579], [39.72016, 43.57756], "…", [39.7215, 43.579]],
  "note": null, "port": [39.7215, 43.579], "total_km": 35.3, "duration_h": 2.34,
  "speed_kn": 12.0, "delay_h": 6.0, "pass_time": "2026-09-13T08:20+00:00",
  "covered_m2": 45.3, "total_m2": 122.5
}
```

Нет детекций: `stops` пуст, `note: "на эту дату детекций нет"`, суммы нулевые. Пустой `stops` без `note` —
ни одна цель не успевается за смену.

---

## Выгрузка и отчёт

### `GET /api/export`

Файл для ГИС и таблиц на выбранную дату и профиль. Скачивается как вложение.

| Параметр | Где | По умолчанию | Описание |
|---|---|---|---|
| `aoi` | query | — | акватория, обязательно |
| `date` | query | — | дата снимка, обязательно |
| `profile` | query | `B` | профиль концентрации |
| `layer` | query | `zones` | `zones` — зоны детекции; `hexes` — все гексы акватории |
| `format` | query | `geojson` | `geojson` или `csv` |

Заголовки ответа:

| Заголовок | Описание |
|---|---|
| `Content-Disposition` | `attachment; filename=<layer>_<aoi>_<date>_<profile>.<geojson\|csv>` |
| `X-Result-SHA256` | sha256 тела ответа — одинаковый запрос даёт побайтно одинаковый файл |

Колонки `layer=zones` (в GeoJSON — те же атрибуты):
`zone_id, aoi, date, scene_id, scene_datetime_utc, lon, lat, h3, zone_area_km2, n_pixels, cover_m2, cover_m2_km2,
p_mean, p_max, detection_status, detection_status_ru, profile, profile_label, size_class, unit, conc_items_km2,
conc_lo80, conc_hi80, conc_lo95, conc_hi95, conc_status, conc_status_ru, conc_reasons, model_version,
valid_frac_scene, wind_ms, sea, field_event_id, field_profile, field_date, field_distance_km, field_date_gap_days,
field_conc_items_km2` — смысл как у [зон](#get-apiaoisaoidatezones), концентрация — выбранного профиля.

Колонки `layer=hexes`:
`aoi, date, scene_id, h3, hex_index, lon, lat, water_km2, valid_frac, detection_status, detection_status_ru,
n_det_px, cover_m2_km2, profile, unit, conc_items_km2, conc_lo80, conc_hi80, conc_status, conc_status_ru,
model_version`.

GeoJSON дополнительно содержит `meta` — параметры и версии, по которым файл построен:

```json
"meta": {"aoi": "sochi", "date": "2026-09-13", "profile": "B", "layer": "zones", "unit": "шт./км²",
         "note": "Концентрация — модель по полевым данным профиля; площадь зоны и покрытие — отдельные показатели детектора и в концентрацию не переводятся.",
         "versions": {"configs": {"concentration.yaml": "c6bce5631d2b", "…": "…"}, "conc_B": "conc-B-c6bce5631d2b"}}
```

```bash
curl -OJ "http://localhost:8000/api/export?aoi=sochi&date=2026-09-13&profile=B&layer=hexes&format=csv"
```

Ошибки: `404` — нет акватории или снимка; `422` — пропущена дата, неверный профиль, `layer` или `format`.

### `GET /api/report`

PDF-отчёт по снимку, A4, 1–2 страницы, `report_<aoi>_<date>_<profile>.pdf`.

| Параметр | Где | По умолчанию | Описание |
|---|---|---|---|
| `aoi` | query | — | акватория |
| `date` | query | — | дата снимка |
| `profile` | query | `B` | профиль для колонки концентрации |

Страница 1 — шапка снимка и ключевые числа (зоны, покрытие, гексы с мусором, видимость воды, медианная
концентрация), вывод «где больше всего мусора», обзорная карта с гексами по классам покрытия, зонами и районами
скопления 2,4 × 1,6 км, таблица районов. Страница 2 (если есть зоны) — фрагменты снимка по районам и таблица
12 крупнейших зон: координаты, ориентир (румб и расстояние от порта или устья), расстояние до берега, покрытие,
P и концентрация с 80%-интервалом. Сборка занимает несколько секунд, повторный запрос отдаётся из кеша.

```bash
curl -OJ "http://localhost:8000/api/report?aoi=sochi&date=2026-09-13&profile=B"
```

Ошибки: `404` — нет акватории или снимка; `422` — неверная дата или профиль.

---

## Сохранённые запросы

Сохранённый запрос фиксирует параметры выгрузки, sha256 результата и версии конфигов и моделей.
Повтор показывает, воспроизводится ли результат побайтно. Файлы лежат в `data/queries/<id>.json`.

### `POST /api/queries`

Выполняет выгрузку (как `GET /api/export`) и сохраняет отпечаток. `id` — первые 12 символов sha256 от
параметров, поэтому тот же запрос получает тот же `id`; уже сохранённый запрос не перезаписывается.

Тело запроса (JSON):

| Поле | Обязательно | По умолчанию | Описание |
|---|---|---|---|
| `aoi` | да | — | акватория |
| `date` | да | — | дата `YYYY-MM-DD` |
| `profile` | нет | `B` | профиль |
| `layer` | нет | `zones` | `zones` или `hexes` |
| `format` | нет | `geojson` | `geojson` или `csv` |

```bash
curl -X POST http://localhost:8000/api/queries -H "Content-Type: application/json" \
     -d '{"aoi": "batumi", "date": "2024-06-05", "profile": "B", "layer": "zones", "format": "geojson"}'
```

```json
{
  "id": "0a0b3aaaf8f9",
  "params": {"aoi": "batumi", "date": "2024-06-05", "profile": "B", "layer": "zones", "format": "geojson"},
  "created_utc": "2026-09-25T18:58:51+00:00",
  "result_sha256": "e3edd970e295bc88204caed013e5155ccb50eca50774f35635f90c4356a06b31",
  "result_bytes": 173555,
  "versions": {"configs": {"profiles.yaml": "8b243937721a", "…": "…"}, "conc_A": "conc-A-c6bce5631d2b",
               "conc_B": "conc-B-c6bce5631d2b"}
}
```

Ошибки: `404` — нет акватории или снимка; `422` — неверные поля тела.

### `GET /api/queries/{qid}`

Сохранённый запрос — тот же объект, что вернул `POST /api/queries`.

| Параметр | Где | Описание |
|---|---|---|
| `qid` | путь | id запроса, 12 hex-символов |

Ошибки: `404` — запроса нет; `422` — id не из 12 hex-символов.

### `POST /api/queries/{qid}/rerun`

Заново строит выгрузку по сохранённым параметрам и сравнивает sha256.

| Поле | Описание |
|---|---|
| `id`, `params` | запрос |
| `saved_sha256`, `rerun_sha256` | sha256 при сохранении и сейчас |
| `match` | `true` — результат совпал побайтно |
| `saved_versions`, `current_versions` | версии конфигов и моделей тогда и сейчас: при `match: false` видно, что изменилось |

```bash
curl -X POST http://localhost:8000/api/queries/a8a53f70b253/rerun
```

```json
{"id": "a8a53f70b253",
 "params": {"aoi": "sochi", "date": "2026-09-13", "profile": "B", "layer": "zones", "format": "csv"},
 "saved_sha256": "4b48fe368308514a89a53be13762730f250661361e8c80b9c988c438b30cb4d5",
 "rerun_sha256": "4b48fe368308514a89a53be13762730f250661361e8c80b9c988c438b30cb4d5",
 "match": true,
 "saved_versions": {"configs": {"profiles.yaml": "e893e113d2ea", "…": "…"}, "…": "…"},
 "current_versions": {"configs": {"profiles.yaml": "8b243937721a", "…": "…"}, "…": "…"}}
```

В выгрузку GeoJSON входит `meta.versions` с хешами конфигов, поэтому для `format=geojson` любое изменение
конфига даёт `match: false`, даже если числа те же, — это сигнал «результат построен другими настройками».
CSV содержит только данные: он совпадает, пока не изменились сами числа.

---

## Метрики качества

### `GET /api/metrics`

Все результаты проверок одним ответом. Разделы, для которых проверка не запускалась, отсутствуют или null.
Параметров нет.

| Поле | Что внутри |
|---|---|
| `detector` | детектор на тесте MARIDA: P/R/F1/IoU по методам (`fdi_window`, `biermann_nb`, `xgb`, `xgb_filters`) и порогам |
| `detector_lro` | детектор на регионе, исключённом из обучения |
| `detector_mados` | детектор на новых сценах MADOS test: P/R/F1/IoU с 95% ДИ, доля пикселей каждого класса фона (нефть, слизь, медузы, платформы…), принятых за мусор ([mados.md](mados.md)) |
| `concentration.<профиль>` | модели концентрации: групповая CV, отложенная выборка (`holdout`: MAE, RMSE, медианная ошибка по моделям), покрытие интервалов, проверка переноса |
| `pairs` | сводка реестра пар |
| `pair_features` | признаки детектора в следе принятых пар |
| `transfer` | связь детекций в следе с полевой концентрацией (Спирмен) и вывод |
| `drift_check` | прогноз дрейфа против «пятно на месте» на парах соседних снимков |
| `review` | ручная проверка фрагментов с детекциями |
| `validated_where` | что чем подтверждено: утверждение → полевые данные / спутниковая разметка / пары |

---

## Растровые слои (статика)

Растры снимка отдаются как файлы, без обёртки API. Ссылки на них есть в `layers` у
[`scenes`](#get-apiaoisaoiscenes), привязка — [`grid`](#get-apiaoisaoigrid).

| Путь | Что это |
|---|---|
| `/data/{aoi}/{date}/rgb.jpg` | снимок в естественных цветах, JPEG, 20 м/пиксель |
| `/data/{aoi}/{date}/debris.png` | вероятность мусора по пикселям, прозрачный PNG |
| `/api/aois/{aoi}/{date}/model-classes` | группы детектора до порога и фильтров: мусор, органика, судно, облако, вода или пена; первый запрос строит и кеширует PNG |
| `/data/{aoi}/{date}/quality.png` | маска качества, прозрачный PNG; цвета — `quality` в `GET /api/statuses` |

`debris.png` и `model-classes` — сетка 10 м как есть. `rgb.jpg` и `quality.png` — каждый второй пиксель этой сетки
(`[::2, ::2]`): пиксель `k` — это пиксель `2k` полной сетки, его середина — в `2k + 0,5`.

Источник `image` в MapLibre натягивает картинку на четыре угла проективно, поэтому растр целиком по `corners`
внутри уезжает до 100 м. Точно — кусками 8 × 8 с углами из `grid` (так делает интерфейс, остаток — 1–3 м):

```js
const g = await (await fetch('/api/aois/sochi/grid')).json();
const scene = (await (await fetch('/api/aois/sochi/scenes')).json()).at(-1);
// [lon, lat] точки (col, row) сетки 10 м — билинейно между узлами
function lonLat(col, row) {
  const n = g.lonlat, u = col / g.step, v = row / g.step;
  const i = Math.max(0, Math.min(n[0].length - 2, Math.floor(u)));
  const j = Math.max(0, Math.min(n.length - 2, Math.floor(v)));
  const a = u - i, b = v - j;
  return [0, 1].map((k) => (1 - b) * ((1 - a) * n[j][i][k] + a * n[j][i + 1][k])
                         + b * ((1 - a) * n[j + 1][i][k] + a * n[j + 1][i + 1][k]));
}
const img = new Image();
img.src = scene.layers.debris;
await img.decode();
const f = Math.round(g.width / img.naturalWidth), off = (f - 1) / 2; // 1 — debris, 2 — rgb и quality
const cuts = (n) => Array.from({ length: 9 }, (_, i) => Math.round((i * n) / 8));
const xs = cuts(img.naturalWidth), ys = cuts(img.naturalHeight);
const at = (x, y) => lonLat(x * f - off, y * f - off);
for (let r = 0; r < 8; r++) for (let c = 0; c < 8; c++) {
  const [x0, x1, y0, y1] = [xs[c], xs[c + 1], ys[r], ys[r + 1]];
  const cv = Object.assign(document.createElement('canvas'), { width: x1 - x0, height: y1 - y0 });
  cv.getContext('2d').drawImage(img, x0, y0, cv.width, cv.height, 0, 0, cv.width, cv.height);
  const id = `debris-${r * 8 + c}`;
  map.addSource(id, { type: 'image', coordinates: [at(x0, y0), at(x1, y0), at(x1, y1), at(x0, y1)] });
  map.getSource(id).updateImage({ image: cv });
  map.addLayer({ id, type: 'raster', source: id, paint: { 'raster-resampling': 'nearest' } });
}
```

На сильном увеличении между кусками может мелькать щель: MapLibre округляет углы до сетки тайла, и у соседей
она бывает разной. Интерфейс заранее ставит углы на общую сетку — `snapQuads` в `frontend/src/legacy.ts`.

## Ошибки

Тело ошибки — JSON с текстом по-русски, его можно показывать пользователю:

```json
{"detail": "нет обработанного снимка sochi на 2000-01-01"}
```

При ошибке формата параметров (`422`) добавляется `errors` — разбор по полям в формате pydantic:

```json
{"detail": "date (путь): '2026-99-99x' — ожидается дата в формате YYYY-MM-DD",
 "errors": [{"type": "string_pattern_mismatch", "loc": ["path", "date"], "msg": "String should match pattern '^\\d{4}-\\d{2}-\\d{2}$'", "input": "2026-99-99x"}]}
```

| Код | Когда |
|---|---|
| `200` | успех; пустой результат — пустой массив или коллекция, не ошибка |
| `404` | нет акватории, снимка на эту дату, гекса, полевого события, сохранённого запроса или реестра пар; для `/api/drift` — нет реанализа на эти даты или старт на суше |
| `422` | неверный параметр: формат даты, неизвестный профиль, значение вне диапазона, пропущен обязательный параметр |
| `503` | дрейф, зоны скопления, маршрут: Open-Meteo не ответил или ограничил частоту запросов; повторить через минуту (заголовок `Retry-After`). `/api/drift`: не настроен аккаунт Copernicus Marine или сервис не ответил |

## Словарь статусов

Тот же словарь отдаёт [`GET /api/statuses`](#get-apistatuses).

**Детекция** (гекс, зона, снимок; код — в `series.status`):

| `id` | Код | Подпись | Смысл |
|---|---|---|---|
| `detected` | 1 | обнаружено | детектор нашёл плавающий мусор хотя бы в одном пикселе |
| `not_detected` | 0 | не обнаружено | мусора не видно, и снимок это позволяет утверждать: видно ≥ 50% воды, нет блика, море спокойное |
| `insufficient_data` | 2 | недостаточно данных | облака, блик, шторм или лёд — отсутствие мусора подтвердить нельзя |

**Концентрация** (код — в `concentration.status`):

| `id` | Код | Подпись | Смысл |
|---|---|---|---|
| `model_estimate` | 1 | модельная оценка | модель профиля в области своих обучающих данных |
| `research_estimate` | 2 | исследовательская оценка | перенос не подтверждён: дальше 60 км от полевых измерений, другой сезон или признаки вне диапазона обучения |
| `unavailable` | 0 | концентрация недоступна | профиль к этому месту не применим: другой бассейн или пресные воды |

**Маска качества** (`quality.png`, доли — в `scenes[].quality`):

| Код | `id` | Подпись |
|---|---|---|
| 0 | `ok` | пригоден |
| 1 | `land` | суша / вне маски воды |
| 2 | `nodata` | нет данных |
| 3 | `cloud` | облако |
| 4 | `shadow` | тень облака |
| 5 | `cirrus` | перистые облака |
| 6 | `glint` | сильный блик |
| 7 | `ice` | лёд / шуга |
| 8 | `ship` | судно / кильватер |
| 9 | `static` | постоянный объект |
