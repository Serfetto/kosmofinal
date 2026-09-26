# Тесты AquaFlow

83 теста в [tests/](../tests/). Они проверяют формулы концентрации и агрегации на контрольных примерах, пересчёт
метрик из сохранённых предсказаний, контракт полевого реестра, отсутствие утечек между обучением и проверкой,
проверку детектора на MADOS, сведение источников, поля Copernicus Marine, устойчивость и API сервиса.

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
| `test_pooled_concentration_is_ratio_of_sums` | агрегация участков: 1 предмет на 2 км и 7 на 18 км полосы 10 м дают ΣN / ΣA = 40 шт./км², а не среднее концентраций 44,4 |
| `test_mae_rmse_medae_same_sample` | MAE, RMSE и медианная ошибка на одном примере: ошибки 15, 0, 60 → 25; 35,7; 15 |
| `test_mae_log_is_factor_error` | ошибка в лог-шкале: промах вдвое вверх и вдвое вниз даёт ln 2 |
| `test_group_bootstrap_resamples_whole_groups` | бутстреп ДИ MAE выбирает группы событий целиком; при одной группе ДИ вырождается в точку; при том же seed результат повторяется |

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
| `test_interrupted_transect_area_is_sum_of_segments` | площадь 4 прерванных трансект S2 — сумма сегментов PANGAEA (Σ Lᵢ × 10 м); сводная площадь реестра округлена, C сдвигается ≤ 3,5% и остаётся в 95% ДИ |
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
| `test_mados_is_external_frozen_test` | MADOS — внешняя проверка: порог сервиса, без обучения и подбора порога; сцены test не пересекаются с train и val |

## Метрики концентрации — [test_metrics.py](../tests/test_metrics.py)

| Тест | Что проверяет |
|---|---|
| `test_metrics_recomputed_from_saved_predictions` | MAE, RMSE, медианная ошибка, ошибка в лог-шкале и 95% ДИ бутстрепа пересчитываются из `data/eval/concentration/*.csv` и совпадают с метриками; корреляция детекций с полевой концентрацией — из `transfer.json` |
| `test_main_and_baselines_on_same_events[A, B]` | основная модель и базовые предсказывают одни и те же события каждой выборки (CV, вложенная CV, отложенная, перенос); отложенная выборка не пересекается с CV и совпадает с моделью сервиса |
| `test_nested_cv_reselects_features_per_fold[A, B]` | во вложенной CV признаки выбраны заново в каждом внешнем фолде из кандидатов конфига; у базовых вложенная CV равна обычной |
| `test_serve_rule[A, B]` | модель сервиса — основная, пока базовая не точнее значимо на вложенной CV; в `conc_*.json` та же модель |
| `test_estimate_refers_to_strip_area` | полевая C относится ко всей полосе, модель — к её середине: для профиля A среднее модели вдоль полосы отличается от значения в середине меньше чем на 0,1% |
| `test_transfer_predictions_are_saved` | предсказания переноса S4 → S3 сохранены для всех 41 события S3, которых нет в обучении |

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

## Сведение источников, методика, воспроизводимость — [test_fusion.py](../tests/test_fusion.py)

| Тест | Что проверяет |
|---|---|
| `test_no_measurements_nearby_means_model` | вдали от всех измерений сведённая оценка и интервалы равны модельным |
| `test_fresh_colocated_measurement_dominates` | измерение в той же точке и в тот же момент весит больше модели и сужает интервал |
| `test_weight_falls_with_distance_and_age[A, B]` | вес измерения падает с расстоянием и давностью |
| `test_variogram_selected_without_holdout` | параметры сведения подобраны без отложенных событий |
| `test_fusion_api_and_hex_card` | `/api/fusion` отдаёт веса модели и измерений (в сумме 1) в шт./км²; карточка гекса показывает расчёт по шагам с тем же числом |
| `test_separation_matches_published_detections` | разбор снимка сходится с картой: кандидаты = отбраковано + осталось |
| `test_methodology_in_ui_uses_served_models` | панель «Методика» показывает коэффициенты моделей сервиса |
| `test_dataset_registry_complete_and_documented` | реестр датасетов полон и совпадает с `docs/datasets.md` |
| `test_metrics_reproduce_from_saved_predictions` | `pipeline.verify`: метрики детектора, концентрации и сведения пересчитываются из предсказаний и эталонов |

## Устойчивость сервиса — [test_robustness.py](../tests/test_robustness.py)

| Тест | Что проверяет |
|---|---|
| `test_garbage_never_500[…]` | мусор, пустые и отсутствующие параметры ни на одной ручке не дают 500 |
| `test_missing_required_params_are_422` | без обязательных параметров — 422 |
| `test_bad_json_bodies` | битое тело сохранённого запроса — 422; несуществующий запрос или акватория — 404 |
| `test_network_outage_is_503_not_500` | нет сети и кеша — 503 с понятным текстом |
| `test_empty_date_is_valid_everywhere` | снимок без зон: пустые коллекции, выгрузки с одной шапкой, отчёт собирается |
| `test_unknown_but_wellformed_ids_are_404` | корректные, но несуществующие идентификаторы — 404 |

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
