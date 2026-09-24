"""Общие настройки: пути, акватории, каналы."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
MODELS = DATA / "models"

# Порядок каналов как в MARIDA (B09/B10 не используются)
BANDS = ["B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B11", "B12"]
BAND_IDX = {b: i for i, b in enumerate(BANDS)}

# Центральные длины волн Sentinel-2A, нм
WAVELENGTH = {
    "B01": 443, "B02": 490, "B03": 560, "B04": 665, "B05": 705, "B06": 740,
    "B07": 783, "B08": 842, "B8A": 865, "B11": 1610, "B12": 2190,
}

H3_RES = 8  # ~0.74 км² на гекс

# Акватории для демо. bbox = (lon_min, lat_min, lon_max, lat_max)
# kind: sea — течения берём из модели океана, inland — только ветровой дрейф
# port — точка старта судна для планирования маршрута (lon, lat); tz — смещение от UTC, ч
AOIS = {
    "sochi": {
        "name": "Сочи — Адлер (Чёрное море)",
        "bbox": (39.55, 43.36, 40.02, 43.62),
        "kind": "sea",
        "tz": 3,
        "port": (39.7215, 43.5790),
        "rivers": {"Сочи": (39.7196, 43.5806), "Мзымта": (39.9248, 43.4192), "Псоу": (40.0063, 43.3869)},
    },
    "vladivostok": {
        "name": "Амурский залив (Японское море)",
        "bbox": (131.60, 42.98, 131.98, 43.32),
        "kind": "sea",
        "tz": 10,
        "port": (131.8735, 43.1115),
        "rivers": {"Раздольная": (131.8000, 43.3100)},
    },
    "neva": {
        "name": "Невская губа (Финский залив)",
        "bbox": (29.55, 59.84, 30.26, 60.08),
        "kind": "sea",
        "tz": 3,
        "port": (30.2050, 59.9300),
        "rivers": {"Нева": (30.2300, 59.9400)},
    },
    "kuibyshev": {
        "name": "Куйбышевское водохранилище (Волга)",
        "bbox": (49.05, 53.44, 49.62, 53.72),
        "kind": "inland",
        "tz": 4,
        "port": (49.4200, 53.5100),
        "rivers": {},
    },
}

# Классы MARIDA и их укрупнение для модели
MARIDA_CLASSES = {
    1: "Marine Debris", 2: "Dense Sargassum", 3: "Sparse Sargassum",
    4: "Natural Organic Material", 5: "Ship", 6: "Clouds", 7: "Marine Water",
    8: "Sediment-Laden Water", 9: "Foam", 10: "Turbid Water", 11: "Shallow Water",
    12: "Waves", 13: "Cloud Shadows", 14: "Wakes", 15: "Mixed Water",
}
GROUPS = ["debris", "organic", "ship", "cloud", "water", "foam"]
MARIDA_TO_GROUP = {
    1: 0,
    2: 1, 3: 1, 4: 1,
    5: 2,
    6: 3, 13: 3,
    7: 4, 8: 4, 10: 4, 11: 4, 15: 4,
    9: 5, 12: 5, 14: 5,
}

# Пользовательские акватории (python -m pipeline.build add ...) хранятся в data/aois.json
_EXTRA = DATA / "aois.json"
if _EXTRA.exists():
    import json as _json

    for _k, _v in _json.loads(_EXTRA.read_text(encoding="utf-8")).items():
        _v["bbox"] = tuple(_v["bbox"])
        _v["port"] = tuple(_v["port"])
        _v.setdefault("rivers", {})
        AOIS[_k] = _v
