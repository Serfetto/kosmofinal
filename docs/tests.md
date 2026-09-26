# Тесты AquaFlow

57 тестов в [tests/](../tests/). Они проверяют формулы концентрации на примерах из постановки, контракт полевого
реестра, отсутствие утечек между обучением и проверкой, проверку детектора на MADOS, поля Copernicus Marine и
API сервиса.

```bash
.venv/Scripts/python -m pytest -q                      # все тесты
.venv/Scripts/python -m pytest -q tests/test_mados.py  # один файл
```

Сеть тестам не нужна: загрузки Open-Meteo и Copernicus Marine подменяются. Тесты API и утечек читают данные
репозитория (`data/web`, `data/models`, `data/eval`, `data/splits`). Два теста MADOS сверяют сцены с исходными
архивами; если в `data/raw` нет `MADOS.zip` и распакованной MARIDA, они пропускаются.

## Формулы концентрации — [test_measure.py](../tests/test_measure.py)

Контрольные примеры из раздела постановки «Оценка концентрации».

| Тест | Что проверяет |
|---|---|
| `test_case_example_items_per_km2` | 12 предметов на 0,20 км² дают 60 шт./км² |
| `test_case_example_absolute_error` | модель дала 75 шт./км², поле 60: абсолютная ошибка 15 |
| `test_strip_area_units` | площадь полосы учёта: 19,68 км × 10 м = 0,1968 км² (S2, трансекта T1) |
| `test_real_rows` | C = N / A на реальных строках реестра (S2 T1 и T4) |
| `test_mask_share_is_not_concentration` | концентрация считается только из числа предметов и площади: нулевая площадь и отрицательное число дают ошибку |
| `test_poisson_ci_contains_estimate_and_zero_case` | 95% ДИ Гарвуда для 4 предметов на 0,2 км² содержит оценку 20 и равен 5,45–51,2; при N = 0 нижняя граница 0 |
| `test_rmse` | формула RMSE |

## Полевой реестр — [test_field.py](../tests/test_field.py)

| Тест | Что проверяет |
|---|---|
| `test_schema` | в реестре 935 строк, 56 столбцов, 318 событий, `sample_id` уникален |
| `test_empty_is_not_zero` | у S4 нет числителя и площади, и это NaN, а не 0 |
| `test_every_row_has_decision` | у каждой из 935 строк есть решение об отборе, у исключённых — код причины |
| `test_scopes_not_mixed` | профиль не смешивает совокупности: у A только суммарный пластик, у B только весь мусор, отдельные предметы не входят |
| `test_midnight_crossing` | трансекта через полночь даёт правильную длительность |
| `test_date_without_time_is_whole_day` | дата без времени считается целыми сутками, время неизвестно |
| `test_events_n_over_a` | 63 события профиля A считаются как N / A; прерванные трансекты S2 восстановлены сегментами из PANGAEA; у профиля B опубликованная плотность |
| `test_published_matches_n_over_a_within_rounding` | опубликованная плотность совпадает с N / A с точностью до округления (< 1%) |

## Утечки и порог детектора — [test_leakage.py](../tests/test_leakage.py)

| Тест | Что проверяет |
|---|---|
| `test_candidate_features_are_allowed` | кандидаты в признаки концентрации не содержат запрещённых предикторов |
| `test_check_features_rejects_target` | целевая величина и состояние моря не проходят как признаки |
| `test_groups_do_not_cross_holdout[A, B]` | группа событий целиком лежит либо в обучении, либо в отложенной выборке, и не разбита между фолдами CV |
| `test_same_day_events_share_group[A, B]` | события одного источника за один день в одной группе |
| `test_served_model_trained_without_holdout[A, B]` | модель сервиса обучена без отложенных событий, и отложенные события совпадают с разбиением |
| `test_detector_threshold_fixed_before_test` | порог в метриках MARIDA test равен `p_det` из конфига; сцены MARIDA test не встречаются в train и val |

## MADOS в детекторе — [test_mados.py](../tests/test_mados.py)

Как и зачем используется MADOS, описано в [mados.md](mados.md).

| Тест | Что проверяет |
|---|---|
| `test_mados_classes_mapped_to_groups` | все 15 классов MADOS сведены к группам детектора; мусор — только Marine Debris, а слизь, медузы и нефть нет; нефть — «вода» |
| `test_marida_and_mados_classes_agree` | один и тот же класс в MARIDA и MADOS попадает в одну группу |
| `test_marida_scenes_found_inside_mados` | все 63 сцены MARIDA найдены внутри MADOS, и разбиение у них то же (нужны оба архива) |
| `test_mados_training_excludes_test_and_duplicates` | в эксперимент с обучением на MADOS идут только новые сцены train и val: ни сцен test, ни повторов MARIDA; новых тестовых сцен 27 (нужны оба архива) |
| `test_mados_eval_uses_service_threshold` | проверка на MADOS идёт с порогом сервиса и на 27 новых сценах |

## Copernicus Marine для дрейфа без снимка — [test_cmems.py](../tests/test_cmems.py)

Загрузка подменена: сеть и аккаунт не нужны.

| Тест | Что проверяет |
|---|---|
| `test_open_ocean_uses_glorys_with_stokes` | в открытом океане берутся GLORYS12, стоксов дрейф волн и ветер; суточное среднее относится к полудню; суша в течениях остаётся сушей, а ветер у берега не обнуляется |
| `test_north_sea_shelf_uses_hourly_tidal_model` | на шельфе Северного моря берутся ежечасные течения NWS с приливом |
| `test_cached_fields_are_reused` | запрос в том же месте (с точностью 0,25°) и в тот же день читает кеш (float16) и ничего не скачивает |
| `test_dates_outside_reanalysis_are_404` | дата вне периода реанализа даёт понятную ошибку 404 |
| `test_env_file` | логин из `.env`: комментарии, кавычки, `export`; переменная окружения важнее файла |

## API — [test_api.py](../tests/test_api.py)

| Тест | Что проверяет |
|---|---|
| `test_invalid_inputs` | несуществующие акватория, дата и событие дают 404; неверные дата, профиль, слой, рамка и тело запроса — 422 |
| `test_empty_selection_is_empty_collection` | пустая рамка возвращает пустую коллекцию, а не ошибку |
| `test_export_matches_zones_and_units` | CSV-выгрузка совпадает с зонами на карте: те же зоны, концентрация, статус, единица «шт./км²» |
| `test_same_request_same_bytes` | одинаковый запрос отдаёт одинаковые байты, заголовок `x-result-sha256` верен |
| `test_saved_query_rerun_matches` | сохранённый запрос при повторе совпадает по sha256 |
| `test_profile_unavailable_is_explicit` | неприменимый профиль (A у Сочи) возвращает `available: false` с причиной |
| `test_zones_consistent_with_series` | число зон и пикселей детекций совпадает со сводкой ряда, статусы из допустимого списка |
| `test_report_is_pdf` | PDF-отчёт собирается; ошибочные акватория, дата и профиль дают 404 и 422 |
| `test_report_districts_take_densest_window` | районы отчёта выбираются по самому плотному окну |
| `test_every_endpoint_documented` | у каждой ручки есть раздел, название, описание и описания параметров; `docs/api.md` существует |
| `test_service_endpoints` | `/api/health` и справочник статусов детекции и концентрации |
| `test_aoi_and_scenes` | карточка акватории, список снимков и все слои последнего снимка доступны |
| `test_raster_grid_matches_corners` | узлы привязки растров сходятся с углами снимка (как интерполирует фронтенд) |
| `test_errors_are_readable` | ошибки 422 и 404 объясняют, что не так: формат даты, допустимый горизонт дрейфа |
| `test_weather_outage_is_503` | отказ Open-Meteo (429) превращается в 503 с `Retry-After` |
| `test_drift_without_imagery` | дрейф от точки S1 (Тихий океан, 2015) без снимка: снос 0,5 м/с за сутки ≈ 43 км; старт на суше — 404 |
| `test_drift_without_credentials_is_503` | без аккаунта Copernicus Marine и без кеша — 503 с объяснением |
| `test_flow_for_map_animation` | течения и ветер по часам для штрихов на карте, маска воды; у водохранилища течений нет |
| `test_flow_without_imagery` | поля течений для точки без снимка; на суше — 404 |
| `test_field_sources_cover_s1_to_s4` | четыре источника S1–S4, все 935 строк и 318 событий; над S1 сцен нет |
| `test_field_aois_on_measurement_dates` | снимки районов полевых данных — только на даты событий ± окно реестра пар |
| `test_field_event_scene_links` | сцену-кандидат можно открыть на карте, даже если сервис её не обрабатывал (Landsat над S2) |
| `test_zones_flat_json` | плоский JSON зон согласован с GeoJSON и CSV; сортировка, страницы, фильтры, ошибки параметров |
