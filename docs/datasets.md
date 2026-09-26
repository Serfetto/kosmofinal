# Реестр данных

Все наборы данных, которые использует AquaFlow: откуда они, на каких условиях, какие поля мы берём и зачем. Документ собирается из [configs/datasets.yaml](../configs/datasets.yaml) командой `python -m pipeline.datasets`; тот же реестр отдаёт `GET /api/datasets` и показывает вкладка «Данные» в панели «Методика». Как данные преобразуются — [data.md](data.md).

## Сводка

| Набор | Роль | Лицензия | Доступ |
|---|---|---|---|
| [Полевой реестр кейса (макромусор, S1–S4)](#case_registry) | обучение, проверка | по источникам — CC BY 4.0 (S1, S2, S4) и CC BY 3.0 (S3) | выдан с кейсом — data/field/raw/macroplastic_marine_samples.csv |
| [S1 · Большое тихоокеанское мусорное пятно (Lebreton et al., 2018)](#s1_lebreton2018) | проверка | CC BY 4.0 | https://doi.org/10.6084/m9.figshare.5873142 |
| [S2 · Саргассово море, рейс MSM41 (Gutow et al., 2021)](#s2_pangaea_msm41) | обучение, проверка | CC BY 4.0 | https://doi.pangaea.de/10.1594/PANGAEA.931834 |
| [S3 · Юго-восток Северного моря (Gutow et al., 2018)](#s3_pangaea_he419) | проверка | CC BY 3.0 | https://doi.pangaea.de/10.1594/PANGAEA.890782 |
| [S4 · Чёрное море, рейс DOORS 3 (июнь 2024)](#s4_doors3) | обучение, проверка | CC BY 4.0 | https://zenodo.org/records/15129753 |
| [MARIDA — Marine Debris Archive v1.0.0](#marida) | обучение, проверка | CC BY 4.0 | https://zenodo.org/records/5151941 |
| [MADOS — Marine Debris and Oil Spill](#mados) | проверка | CC BY 4.0 | https://zenodo.org/records/10664073 |
| [Sentinel-2 L2A (Microsoft Planetary Computer)](#sentinel2_l2a) | вход сервиса, проверка | Условия Copernicus Sentinel Data (свободный полный открытый доступ) | https://planetarycomputer.microsoft.com/dataset/sentinel-2-l2a |
| [Landsat Collection 2 Level-2 (Microsoft Planetary Computer)](#landsat_c2_l2) | отображение | общественное достояние США (USGS) | https://planetarycomputer.microsoft.com/dataset/landsat-c2-l2 |
| [ERA5, ветер 10 м (через Open-Meteo Historical Weather API)](#era5_openmeteo) | признак модели, вход сервиса | Open-Meteo — CC BY 4.0; ERA5 — лицензия Copernicus | https://open-meteo.com/en/docs/historical-weather-api |
| [Прогноз ветра Open-Meteo (Forecast API)](#openmeteo_forecast) | вход сервиса | CC BY 4.0 | https://open-meteo.com/en/docs |
| [Течения Meteo-France SMOC (GLOBAL_ANALYSISFORECAST_PHY_001_024) через Open-Meteo Marine API](#smoc_openmeteo) | вход сервиса | Open-Meteo — CC BY 4.0; данные — лицензия Copernicus Marine Service | https://open-meteo.com/en/docs/marine-weather-api |
| [Реанализы Copernicus Marine — течения, волны, ветер](#cmems_reanalysis) | вход сервиса | Copernicus Marine Service licence (свободное использование) | https://data.marine.copernicus.eu (бесплатный аккаунт) |
| [global-land-mask (по данным GLOBE, ~1 км)](#global_land_mask) | признак модели, вход сервиса | MIT (пакет); GLOBE — открытые данные NOAA | https://github.com/toddkarin/global-land-mask |
| [Подложки Esri (World Imagery, Light/Dark Gray Canvas)](#esri_basemaps) | отображение | условия использования Esri | https://server.arcgisonline.com/ArcGIS/rest/services/ |
| [Набор для ручной разметки детекций (свой)](#review_set) | проверка | как у снимков Sentinel-2 (производные данные Copernicus) | python -m pipeline.review sample |

<a id="case_registry"></a>

## Полевой реестр кейса (макромусор, S1–S4)

| | |
|---|---|
| Поставщик | организаторы кейса; сведён из четырёх открытых источников ниже |
| Версия | 935 строк, 56 полей, 318 событий |
| Доступ | выдан с кейсом — data/field/raw/macroplastic_marine_samples.csv |
| Где лежит | data/field/raw/ |
| Лицензия | по источникам — CC BY 4.0 (S1, S2, S4) и CC BY 3.0 (S3) |
| Условия | использование и изменение с указанием авторов первоисточников; ссылки — в полях source_doi и provenance |
| Роль | обучение, проверка |
| Зачем | обучение и проверка моделей концентрации (профили A и B), слой полевых измерений на карте, реестр пар |
| Код | pipeline/field.py, pipeline/measure.py |

| Поле | Тип / единица | Что это и как используем |
|---|---|---|
| `sample_id` | строка | уникальный ключ записи MPL-0001…MPL-0935 |
| `event_id` | строка | событие (трансекта, трал); строки одного события зависимы |
| `source_id` | строка | источник S1…S4 |
| `record_type` | словарь | transect_density — плотность по полосе; item_observation — отдельный предмет |
| `target_scope` | словарь | совокупность: total_plastic, all_litter, категории, object_context — по ней отбираются профили |
| `measurement_profile` | словарь | протокол и порог размера: S2_visual_GT2, S4_visual_GT2_5 и др. |
| `latitude, longitude, lat_start…lon_end` | °, WGS 84 | положение и концы полосы учёта |
| `date_utc, time_start_utc, time_end_utc` | дата, время UTC | интервал наблюдения; дата без времени — целые сутки |
| `items_count` | шт. | число предметов N — числитель C = N / A (только в целевом отборе, не признак) |
| `sampled_area_km2, transect_length_km, transect_width_m` | км², км, м | обследованная площадь A и размеры полосы |
| `concentration_items_km2` | шт./км² | опубликованная плотность — целевая величина; пересчитывается как N / A, где N и A известны |
| `material, size_class, sampling_method` | строка | материал, размерный класс, метод — подписи профиля |
| `quality_flags` | коды через ; | ограничения записи; строки с source_total_vs_object_count_conflict исключены |
| `source_doi, source_license, provenance` | строка | происхождение и лицензия записи |

Запрещены как признаки (производные от ответа или недоступны при применении): концентрации, items_count, parent_*, reported_*, density_numerator_items, волнение и судовой ветер — список forbidden_predictors в configs/concentration.yaml, проверка — tests/test_leakage.py.

<a id="s1_lebreton2018"></a>

## S1 · Большое тихоокеанское мусорное пятно (Lebreton et al., 2018)

| | |
|---|---|
| Поставщик | The Ocean Cleanup; figshare |
| Версия | DOI 10.6084/m9.figshare.5873142 |
| Доступ | https://doi.org/10.6084/m9.figshare.5873142 |
| Лицензия | CC BY 4.0 |
| Условия | использование с указанием авторов (Lebreton et al., 2018, Scientific Reports 8:4666) |
| Роль | проверка |
| Зачем | слой полевых измерений (профиль C — трал 5–50 см); дрейф без снимка от места измерения. Модель не обучалась |
| Код | pipeline/field.py |

| Поле | Тип / единица | Что это и как используем |
|---|---|---|
| `Sampling event ID` | строка | идентификатор трала → event_id S1:<Sampling event ID> |
| `SamplingInformation, Concentration` | таблицы figshare | место и время тралов; плотность по размерным классам, шт./км² и г/км² — в реестре кейса уже сведены |

<a id="s2_pangaea_msm41"></a>

## S2 · Саргассово море, рейс MSM41 (Gutow et al., 2021)

| | |
|---|---|
| Поставщик | PANGAEA |
| Версия | PANGAEA 931833 (предметы), 931834 (трансекты), 931845 (сводная) |
| Доступ | https://doi.pangaea.de/10.1594/PANGAEA.931834 |
| Где лежит | data/field/raw/pangaea_931834_transects.xlsx |
| Лицензия | CC BY 4.0 |
| Условия | использование с указанием авторов и DOI набора |
| Роль | обучение, проверка |
| Зачем | обучение и проверка профиля A; сегменты и время прерванных трансект T18, T22, T35, T49 |
| Код | pipeline/field.py |

| Поле | Тип / единица | Что это и как используем |
|---|---|---|
| `Transect no.` | номер | трансекта → S2:MSM41_litter-T<n>; строка на сегмент прерванной трансекты |
| `Date, Start Time, End time` | дата, время UTC | время сегмента |
| `Start Lat (N), Start Long (W), End Lat (N), End Long (W)` | ° | концы сегмента |
| `Distance (km), Area (km2) covered` | км, км² | длина и площадь полосы 10 м |
| `Total no. of items, Items density (items km-2)` | шт., шт./км² | сверка N / A с реестром кейса |
| `Density of seaweeds clumps` | комков/км² | только контекст события; запрещён как признак |

<a id="s3_pangaea_he419"></a>

## S3 · Юго-восток Северного моря (Gutow et al., 2018)

| | |
|---|---|
| Поставщик | PANGAEA |
| Версия | PANGAEA 890781 (предметы), 890782 (трансекты) |
| Доступ | https://doi.pangaea.de/10.1594/PANGAEA.890782 |
| Лицензия | CC BY 3.0 |
| Условия | использование с указанием авторов и DOI набора |
| Роль | проверка |
| Зачем | только проверка переноса профиля B на другое море; не обучался |
| Код | pipeline/field.py, pipeline/concentration.py |

<a id="s4_doors3"></a>

## S4 · Чёрное море, рейс DOORS 3 (июнь 2024)

| | |
|---|---|
| Поставщик | Институт океанологии БАН; Zenodo |
| Версия | DOI 10.5281/zenodo.15129753 |
| Доступ | https://zenodo.org/records/15129753 |
| Лицензия | CC BY 4.0 |
| Условия | использование с указанием авторов и DOI набора |
| Роль | обучение, проверка |
| Зачем | обучение и проверка профиля B (весь плавающий мусор >2,5 см); пары со снимками Sentinel-2 |
| Код | pipeline/field.py, pipeline/pairs.py |

<a id="marida"></a>

## MARIDA — Marine Debris Archive v1.0.0

| | |
|---|---|
| Поставщик | Kikaki et al., 2022 (PLoS ONE 17(1) e0262247); Zenodo |
| Версия | DOI 10.5281/zenodo.5151941 |
| Доступ | https://zenodo.org/records/5151941 |
| Где лежит | data/raw/marida (не в репозитории); списки разбиения — data/splits/marida/ |
| Лицензия | CC BY 4.0 |
| Условия | использование с указанием авторов статьи и DOI набора |
| Роль | обучение, проверка |
| Зачем | обучение детектора (train), ранняя остановка и пороги базовых (val), итоговые метрики (test) |
| Код | pipeline/train.py, pipeline/eval_detector.py |

| Поле | Тип / единица | Что это и как используем |
|---|---|---|
| `S2_<scene>_<n>.tif` | отражение, 11 каналов | B01–B12 без B09 и B10, 256×256 пикс. по 10 м, ACOLITE |
| `S2_<scene>_<n>_cl.tif` | класс 0–15 | 0 — не размечено (игнорируется), 1 — Marine Debris (положительный класс), 2–15 — фон |
| `S2_<scene>_<n>_conf.tif` | 1–3 | уверенность разметки (в обучении не используется) |
| `splits/{train,val,test}_X.txt` | список патчей | официальное разбиение: 694 / 328 / 359 патчей |

<a id="mados"></a>

## MADOS — Marine Debris and Oil Spill

| | |
|---|---|
| Поставщик | Kikaki et al., 2024 (ISPRS J. Photogramm. Remote Sens.); Zenodo |
| Версия | DOI 10.5281/zenodo.10664073 |
| Доступ | https://zenodo.org/records/10664073 |
| Где лежит | data/raw/MADOS.zip (не в репозитории, ~4 ГБ) |
| Лицензия | CC BY 4.0 |
| Условия | использование с указанием авторов статьи и DOI набора |
| Роль | проверка |
| Зачем | проверка детектора на фоне, которого нет в MARIDA (нефть, слизь, медузы, платформы); в модель сервиса не входит |
| Код | pipeline/mados.py, pipeline/eval_detector_mados.py |

| Поле | Тип / единица | Что это и как используем |
|---|---|---|
| `<scene>_L2R_rhorc_<λ>_<n>.tif` | отражение по каналам 10/20/60 м | патчи 240×240 |
| `<scene>_L2R_cl_<n>.tif` | класс 0–15 | 15 классов, в том числе Oil Spill, Sea snot, Jellyfish, Oil Platform |
| `splits/{train,val,test}_X.txt` | список патчей | разбиение авторов |

<a id="sentinel2_l2a"></a>

## Sentinel-2 L2A (Microsoft Planetary Computer)

| | |
|---|---|
| Поставщик | ESA / Copernicus; каталог STAC Microsoft Planetary Computer |
| Версия | коллекция sentinel-2-l2a, обработка Sen2Cor |
| Доступ | https://planetarycomputer.microsoft.com/dataset/sentinel-2-l2a |
| Где лежит | data/processed/<aoi>/<date>/ (не в репозитории, ~17 ГБ); в репозитории — метаданные сцен и готовые карты data/web |
| Лицензия | Условия Copernicus Sentinel Data (свободный полный открытый доступ) |
| Условия | использование, копирование и изменение бесплатно; указывать «Contains modified Copernicus Sentinel data <год>» |
| Роль | вход сервиса, проверка |
| Зачем | детекция скоплений на акваториях, маска качества, реестр пар «событие ↔ снимок», подложка сцены |
| Код | pipeline/s2.py, pipeline/detect.py, pipeline/pairs.py |

| Поле | Тип / единица | Что это и как используем |
|---|---|---|
| `B01…B12 (без B09, B10)` | отражение = (DN + сдвиг) / 10 000 | сдвиг −1000 для обработки 04.00 и новее (с 25.01.2022) |
| `SCL` | класс сцены 0–11 | облака, тени, вода, снег — маска пригодных пикселей |
| `datetime, s2:mgrs_tile, eo:cloud_cover, s2:processing_baseline` | метаданные STAC | время пролёта, тайл, облачность, версия обработки |

<a id="landsat_c2_l2"></a>

## Landsat Collection 2 Level-2 (Microsoft Planetary Computer)

| | |
|---|---|
| Поставщик | USGS |
| Версия | коллекция landsat-c2-l2 |
| Доступ | https://planetarycomputer.microsoft.com/dataset/landsat-c2-l2 |
| Лицензия | общественное достояние США (USGS) |
| Условия | без ограничений; просьба указывать «Landsat imagery courtesy of the U.S. Geological Survey» |
| Роль | отображение |
| Зачем | учёт в реестре пар и просмотр сцены из карточки события там, где Sentinel-2 нет (S2, S3 2014). Детектор не применяется — нет красного края |
| Код | pipeline/pairs.py, backend/case_api.py |

| Поле | Тип / единица | Что это и как используем |
|---|---|---|
| `red, green, blue` | отражение | естественные цвета для просмотра |

<a id="era5_openmeteo"></a>

## ERA5, ветер 10 м (через Open-Meteo Historical Weather API)

| | |
|---|---|
| Поставщик | ECMWF / Copernicus Climate Change Service; Open-Meteo |
| Версия | ERA5 hourly, 0,25° |
| Доступ | https://open-meteo.com/en/docs/historical-weather-api |
| Где лежит | data/field/cache_era5.json (кеш признака), data/processed/<aoi>/<date>/met.npz (кеш дрейфа) |
| Лицензия | Open-Meteo — CC BY 4.0; ERA5 — лицензия Copernicus |
| Условия | указывать Open-Meteo и «Copernicus Climate Change Service information <год>» |
| Роль | признак модели, вход сервиса |
| Зачем | признак wind24_ms (кандидат), статус «шторм» у снимка, дрейфовый буфер реестра пар, ветер в прогнозе дрейфа |
| Код | pipeline/covariates.py, pipeline/aggregate.py, pipeline/drift.py |

| Поле | Тип / единица | Что это и как используем |
|---|---|---|
| `wind_speed_10m` | м/с | скорость ветра на 10 м, ежечасно |
| `wind_direction_10m` | °, откуда дует | направление ветра |

<a id="openmeteo_forecast"></a>

## Прогноз ветра Open-Meteo (Forecast API)

| | |
|---|---|
| Поставщик | Open-Meteo (модели национальных метеослужб) |
| Доступ | https://open-meteo.com/en/docs |
| Лицензия | CC BY 4.0 |
| Условия | указывать Open-Meteo |
| Роль | вход сервиса |
| Зачем | ветер в дрейфе по свежим снимкам, пока за эти дни нет ERA5 (последние 6 суток) |
| Код | pipeline/drift.py |

| Поле | Тип / единица | Что это и как используем |
|---|---|---|
| `wind_speed_10m, wind_direction_10m` | м/с, ° | как у ERA5 |

<a id="smoc_openmeteo"></a>

## Течения Meteo-France SMOC (GLOBAL_ANALYSISFORECAST_PHY_001_024) через Open-Meteo Marine API

| | |
|---|---|
| Поставщик | Copernicus Marine Service; Open-Meteo |
| Версия | 1/12°, ежечасно, с 2022 г. |
| Доступ | https://open-meteo.com/en/docs/marine-weather-api |
| Где лежит | data/processed/<aoi>/<date>/met.npz |
| Лицензия | Open-Meteo — CC BY 4.0; данные — лицензия Copernicus Marine Service |
| Условия | указывать Open-Meteo и E.U. Copernicus Marine Service Information |
| Роль | вход сервиса |
| Зачем | прогноз дрейфа на 72 ч от снимка, зоны схождения, маршрут судна, проверка дрейфа |
| Код | pipeline/drift.py, pipeline/route.py |

| Поле | Тип / единица | Что это и как используем |
|---|---|---|
| `ocean_current_velocity` | км/ч | суммарная поверхностная скорость: течение + прилив + стоксов дрейф |
| `ocean_current_direction` | °, куда | направление течения |

<a id="cmems_reanalysis"></a>

## Реанализы Copernicus Marine — течения, волны, ветер

| | |
|---|---|
| Поставщик | Copernicus Marine Service |
| Версия | GLORYS12 (GLOBAL_MULTIYEAR_PHY_001_030), NWSHELF_MULTIYEAR_PHY_004_009, GLOBAL_MULTIYEAR_WAV_001_032, WIND_GLO_PHY_L4_MY_012_006 |
| Доступ | https://data.marine.copernicus.eu (бесплатный аккаунт) |
| Где лежит | data/cmems/*.npz (кеш) |
| Лицензия | Copernicus Marine Service licence (свободное использование) |
| Условия | указывать «E.U. Copernicus Marine Service Information» и DOI продукта |
| Роль | вход сервиса |
| Зачем | дрейф без снимка от места полевого измерения (S1–S3, 2014–2016 гг.) |
| Код | pipeline/cmems.py |

| Поле | Тип / единица | Что это и как используем |
|---|---|---|
| `uo, vo` | м/с | течение: GLORYS12 1/12° среднесуточное; на шельфе Северного моря — NWS ~7 км ежечасно с приливом |
| `VSDX, VSDY` | м/с | стоксов дрейф волн, 0,2°, раз в 3 ч |
| `eastward_wind, northward_wind` | м/с | ветер 10 м, L4, 0,125° (до 2007 г. 0,25°), ежечасно |

<a id="global_land_mask"></a>

## global-land-mask (по данным GLOBE, ~1 км)

| | |
|---|---|
| Поставщик | T. Karin; NOAA GLOBE |
| Доступ | https://github.com/toddkarin/global-land-mask |
| Лицензия | MIT (пакет); GLOBE — открытые данные NOAA |
| Условия | сохранять уведомление MIT |
| Роль | признак модели, вход сервиса |
| Зачем | расстояние до берега — признак профиля B; суша для частиц дрейфа за краем снимка |
| Код | pipeline/covariates.py, pipeline/drift.py |

| Поле | Тип / единица | Что это и как используем |
|---|---|---|
| `is_land` | логический | суша / море на сетке ~1 км |

<a id="esri_basemaps"></a>

## Подложки Esri (World Imagery, Light/Dark Gray Canvas)

| | |
|---|---|
| Поставщик | Esri |
| Доступ | https://server.arcgisonline.com/ArcGIS/rest/services/ |
| Лицензия | условия использования Esri |
| Условия | только отображение; атрибуция на карте |
| Роль | отображение |
| Зачем | подложка карты |
| Код | frontend/src/legacy.ts |

<a id="review_set"></a>

## Набор для ручной разметки детекций (свой)

| | |
|---|---|
| Поставщик | команда AquaFlow |
| Доступ | python -m pipeline.review sample |
| Где лежит | data/eval/review/ |
| Лицензия | как у снимков Sentinel-2 (производные данные Copernicus) |
| Условия | «Contains modified Copernicus Sentinel data» |
| Роль | проверка |
| Зачем | будущая локальная точность детектора (120 зон, 60 контрольных точек); пока не размечен, в метрики не входит |
| Код | pipeline/review.py |
