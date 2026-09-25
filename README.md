# Flux — мониторинг океанического пластика

Веб-сервис для выявления и оценки загрязнения акваторий плавающим мусором по снимкам Sentinel-2:
интерактивная карта концентрации, сравнение участков, динамика во времени, прогноз дрейфа
и маршрут судна для обследования.

Демо-акватории (РФ): Сочи — Адлер (Чёрное море), Амурский залив (Японское море),
Невская губа (Финский залив), Куйбышевское водохранилище (Волга). Период — апрель 2025 … сентябрь 2026.

## Чем отличается от «обучили YOLO»

| | Типичное решение | Дрейф |
|---|---|---|
| Что на выходе | маска «мусор / нет» | **концентрация**: м² мусора на км² (доля покрытия пикселя) |
| Сравнение участков | — | гексы H3 одинаковой площади, рисование двух участков, вердикт |
| Время | один снимок | ряд 30 дат: устойчивость зон, тренд |
| Прогноз | — | лагранжев ансамбль: течения SMOC + ветер ERA5, выброс на берег |
| Действие | — | маршрут судна по целям с поправкой на дрейф к моменту прибытия, GPX |
| Ложные срабатывания | как повезёт | CFAR, маска постоянных объектов, фильтр пены, учёт ветра (шторм) |

## Архитектура

```
Planetary Computer STAC ──► Sentinel-2 L2A, 11 каналов, сетка 10 м UTM на акваторию
                              │
                              ▼
            нормализация фона (вычитание локального спектра воды: блик, дымка)
                              │
       FDI, FAI, NDVI, PI, текстура ──► LightGBM (обучен на MARIDA) ──► P(мусор)
                              │
     фильтры: CFAR 5σ · спектр против пены · постоянные объекты · ветер ERA5
                              │
     линейное смешение «вода + плотное скопление» ──► доля покрытия пикселя
                              │
                 H3 (res 8, ~0,74 км²): концентрация, устойчивость, тренд
                              │
 Open-Meteo: течения SMOC, ветер ERA5 ──► дрейф (RK2, ансамбль) ──► прогноз, зоны скопления, маршрут
                              │
              FastAPI API ──► React + Tailwind + MapLibre + Chart.js
```

Структура:

- `pipeline/config.py` — акватории, каналы, классы
- `pipeline/s2.py`, `download.py` — поиск и загрузка сцен
- `pipeline/features.py` — индексы, нормализация фона
- `pipeline/train.py` — обучение на MARIDA → `data/models/lgbm.joblib`, `metrics.json`
- `pipeline/detect.py` — детекция по сценам → `data/processed/<aoi>/<date>/det.tif`
- `pipeline/aggregate.py` — гексы и ряды → `data/web/<aoi>/`
- `pipeline/drift.py` — течения/ветер, лагранжев дрейф, карта скопления
- `pipeline/route.py` — маршрут обследования
- `backend/app.py` — API и раздача фронтенда
- `frontend/src/App.tsx` — корневая композиция приложения
- `frontend/src/components/` — боковая панель, карта, тулбар и рабочая панель
- `frontend/src/components/panels/` — отдельные сценарии участка, сравнения, дрейфа и маршрута
- `frontend/src/legacy.ts` — картографическая и аналитическая логика MapLibre + Chart.js
- `frontend/src/styles.css` — Tailwind и визуальная система приложения

## Быстрый запуск на готовых данных

Результаты обработки всех четырёх акваторий лежат в репозитории (`data/web`, маски воды, метаданные сцен),
поэтому для запуска сервиса скачивать снимки и обучать модель не нужно. Нужен Python 3.12 и интернет
(течения и ветер для прогноза подгружаются из Open-Meteo).

```bash
git clone <url репозитория> && cd kosmofinal
python -m pip install uv
python -m uv venv --python 3.12 .venv
python -m uv pip install --python .venv/Scripts/python.exe -r requirements.txt   # Linux/macOS: .venv/bin/python
cd frontend && npm install && npm run build && cd ..
.venv/Scripts/python -m uvicorn backend.app:app --port 8000                       # Linux/macOS: .venv/bin/python
# http://localhost:8000
```

## Полный запуск (пересчёт данных)

```bash
python -m pip install uv
python -m uv venv --python 3.12 .venv
python -m uv pip install --python .venv/Scripts/python.exe -r requirements.txt

# 1. обучение (MARIDA: https://zenodo.org/records/5151941 → распаковать в data/raw/marida)
.venv/Scripts/python -m pipeline.train

# 2. данные для всех акваторий (скачивание ~15 ГБ, детекция ~40 мин)
.venv/Scripts/python -m pipeline.build run

# 3. интерфейс и сервис
cd frontend
npm install
npm run build
cd ..
.venv/Scripts/python -m uvicorn backend.app:app --port 8000
# http://localhost:8000
```

Для разработки интерфейса с hot reload запустите API на порту `8000`, а во втором терминале:

```bash
cd frontend
npm install
npm run dev
# http://localhost:5173 — запросы /api и /data проксируются в FastAPI
```

На Windows с кириллицей в консоли: `set PYTHONIOENCODING=utf-8`.

Новая акватория:

```bash
.venv/Scripts/python -m pipeline.build add azov "Таганрогский залив" 38.6 46.9 39.3 47.3 --port 38.93,47.2 --tz 3
.venv/Scripts/python -m pipeline.build run azov
```

Ключи API не нужны: Planetary Computer и Open-Meteo открыты.

## API

| Метод | Что возвращает |
|---|---|
| `GET /api/aois` | акватории, даты, суммарная площадь мусора по датам |
| `GET /api/aois/{aoi}/hexes` | гексы H3 (GeoJSON): средняя концентрация, устойчивость, тренд |
| `GET /api/aois/{aoi}/series` | концентрация / число детекций / доля валидных пикселей по гексам и датам |
| `GET /api/aois/{aoi}/{date}/points` | пиксели мусора: lon, lat, P, доля покрытия |
| `GET /api/aois/{aoi}/{date}/drift?hours=72` | ансамбль частиц по часам, прогнозная площадь по гексам на 24/48/72 ч |
| `GET /api/aois/{aoi}/{date}/drift_point?lon=&lat=` | конус неопределённости из точки |
| `GET /api/aois/{aoi}/{date}/accumulation` | фактор скопления частиц по гексам (зоны схождения течений) |
| `GET /api/aois/{aoi}/{date}/route?n=8&speed=12&delay=6` | маршрут, ETA, прогнозные позиции целей |
| `GET /api/metrics` | качество классификатора на тестовой выборке MARIDA |

## Ограничения

- При 10 м видны скопления и полосы мусора (от ~20–30% пикселя), а не отдельные предметы.
  Концентрация — эквивалентная площадь покрытия, а не число предметов.
- Пластик, плавник и водоросли спектрально разделяются не полностью. Подтверждать нужно судном или дроном,
  и сервис строит для них маршрут.
- Модель обучена на MARIDA (в основном тропики). Нормализация фона снижает сдвиг доменов,
  но разметки по российским акваториям нет: достоверность на местных данных проверяется визуально.
- Течения SMOC (1/12°) не разрешают мелкие бухты. Для водохранилищ течений нет, считается только ветровой дрейф.
