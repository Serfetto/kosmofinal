"""Детекция мусора и оценка концентрации по сценам акватории.

python -m pipeline.detect [aoi ...]

Для каждой даты сохраняет det.tif (вероятность, доля покрытия, класс, маска валидных
пикселей, флаги фильтров) и rgb.jpg (истинные цвета) для подложки карты.
"""
from __future__ import annotations

import json
import sys
import warnings

import joblib
import numpy as np
import rasterio
import requests
from PIL import Image
from rasterio.warp import transform as warp_transform
from scipy.ndimage import (binary_dilation, binary_erosion, binary_opening, distance_transform_edt, label,
                           uniform_filter)

from .config import AOIS, BAND_IDX, GROUPS, MODELS, PROCESSED
from .features import WATER_REF, features, fdi
from .provenance import load_yaml

warnings.filterwarnings("ignore")

CLEAR_SCL = {2, 4, 5, 6, 7, 11}
BAD_SCL = {0, 1, 3, 8, 9, 10, 11}  # нет данных, дефект, тень облака, облака, перистые, снег/лёд
P_DET = load_yaml("detector.yaml")["p_det"]
REF_FDI = float(fdi(WATER_REF[:, None, None])[0, 0])
UNMIX_BANDS = [BAND_IDX[b] for b in ("B05", "B06", "B07", "B08", "B8A", "B11", "B12")]

_model = None


def scene_wind(aoi_id: str, date: str) -> float | None:
    """Скорость ветра 10 м (ERA5) в момент пролёта в центре акватории, м/с."""
    meta = json.loads((PROCESSED / aoi_id / date / "meta.json").read_text(encoding="utf-8"))
    hour = int(meta["datetime"][11:13])
    lon0, lat0, lon1, lat1 = AOIS[aoi_id]["bbox"]
    try:
        r = requests.get("https://archive-api.open-meteo.com/v1/archive", timeout=30, params=dict(
            latitude=(lat0 + lat1) / 2, longitude=(lon0 + lon1) / 2, hourly="wind_speed_10m",
            start_date=date, end_date=date, wind_speed_unit="ms", timezone="GMT"))
        v = r.json()["hourly"]["wind_speed_10m"][hour]
        return None if v is None else round(float(v), 1)
    except Exception:
        return None


def model():
    global _model
    if _model is None:
        _model = joblib.load(MODELS / "xgb.joblib")
    return _model


def dates(aoi_id: str) -> list[str]:
    return sorted(d.name for d in (PROCESSED / aoi_id).iterdir() if (d / "meta.json").exists())


def main_basin(water: np.ndarray, width: int = 30) -> np.ndarray:
    """Только основная акватория: лиманы и озёра за протоками уже 2·width пикселей отбрасываются.

    Ядро — вода дальше width от берега; берётся крупнейшая связная часть ядра и
    наращивается обратно по воде (через сушу — косы, дамбы — не перекидывается).
    """
    core = distance_transform_edt(water) > width
    lab, n = label(core)
    if n == 0:
        return water
    main = lab == np.argmax(np.bincount(lab.ravel())[1:]) + 1
    # Квадратный шаг (шахматная метрика ≤ евклидовой): иначе на косом берегу теряется прибрежная полоса
    return binary_dilation(main, np.ones((3, 3), bool), iterations=width + 1, mask=water)


def build_water_mask(aoi_id: str) -> np.ndarray:
    """Постоянная вода: пиксель — вода по SCL в ≥50% безоблачных наблюдений; минус 30 м от берега.

    sea_only в описании акватории — оставить только основную акваторию (см. main_basin).
    """
    water_n = clear_n = None
    for d in dates(aoi_id):
        with rasterio.open(PROCESSED / aoi_id / d / "scl.tif") as src:
            scl = src.read(1)
            prof = src.profile
        clear = np.isin(scl, list(CLEAR_SCL))
        if water_n is None:
            water_n = np.zeros(scl.shape, np.uint16)
            clear_n = np.zeros(scl.shape, np.uint16)
        water_n += scl == 6
        clear_n += clear
    # Для коротких рядов (акватория-проверка на несколько дат) хватает одного безоблачного наблюдения
    min_clear = 3 if len(dates(aoi_id)) > 5 else 1
    water = (clear_n >= min_clear) & (water_n >= 0.5 * clear_n)
    water = binary_opening(water, iterations=2)  # убрать одиночные «водные» пиксели на суше
    water = binary_erosion(water, iterations=3)
    if AOIS[aoi_id].get("sea_only"):
        water = main_basin(water)
    prof.update(dtype="uint8", count=1, nodata=None)
    with rasterio.open(PROCESSED / aoi_id / "water.tif", "w", **prof) as dst:
        dst.write(water.astype(np.uint8), 1)
    return water


def load_water(aoi_id: str) -> np.ndarray:
    with rasterio.open(PROCESSED / aoi_id / "water.tif") as src:
        return src.read(1).astype(bool)


def corners_lonlat(prof) -> list[list[float]]:
    """Углы растра для image-source MapLibre: TL, TR, BR, BL."""
    t, w, h = prof["transform"], prof["width"], prof["height"]
    xs = [t.c, t.c + t.a * w, t.c + t.a * w, t.c]
    ys = [t.f, t.f, t.f + t.e * h, t.f + t.e * h]
    lon, lat = warp_transform(prof["crs"], "EPSG:4326", xs, ys)
    return [[round(a, 6), round(b, 6)] for a, b in zip(lon, lat)]


def detect_array(refl: np.ndarray, valid: np.ndarray, gate_all: bool = False) -> dict:
    """Ядро детектора для массива отражений [11,H,W] и маски пригодных пикселей.

    Пригодность пикселя (valid) и принадлежность целевому классу (P, flags) считаются раздельно.
    gate_all — прогнать модель по всем пригодным пикселям (для оценки на MARIDA), а не только по аномальным.
    """
    F, names = features(refl)
    xn = F[:11]
    d_fdi = F[names.index("FDI")] - REF_FDI
    d8 = xn[BAND_IDX["B08"]] - WATER_REF[BAND_IDX["B08"]]
    d2 = xn[BAND_IDX["B02"]] - WATER_REF[BAND_IDX["B02"]]
    # Модель гоняем только по пикселям с аномалией — остальное заведомо чистая вода
    gate = valid if gate_all else valid & ((d_fdi > 0.01) | (d8 > 0.01) | (d2 > 0.03))

    m = model()
    shape = valid.shape
    P = np.zeros(shape, np.float32)
    G = np.full(shape, GROUPS.index("water"), np.uint8)
    if gate.any():
        pp = m["model"].predict_proba(F[:, gate].T)  # XGBoost на GPU
        P[gate] = pp[:, 0]
        G[gate] = pp.argmax(1)
    G[~valid] = 255

    # Доля покрытия пикселя: линейное смешение «вода + плотное скопление мусора»
    dense = np.asarray(m["dense_endmember"], np.float32)[UNMIX_BANDS] - WATER_REF[UNMIX_BANDS]
    anom = xn[UNMIX_BANDS] - WATER_REF[UNMIX_BANDS, None, None]
    frac = np.clip(np.tensordot(dense, anom, axes=1) / float(dense @ dense), 0, 1)
    frac[~valid] = 0

    # Фильтры ложных срабатываний (флаги, финальное решение — в aggregate.py):
    # 1 — NIR-аномалия сильнее синей (белая пена и барашки ярче в видимом диапазоне)
    # 2 — есть соседи с P>0.3 (одиночные «искры» — шум)
    # 4 — CFAR: NIR-аномалия ≥ 5σ локального шума (блик и рябь поднимают порог)
    nb = uniform_filter((P > 0.3).astype(np.float32), 3) * 9
    b8 = xn[BAND_IDX["B08"]]
    hp = b8 - uniform_filter(b8, 5, mode="nearest")
    # uniform_filter на скользящих суммах даёт крошечные отрицательные значения — без клипа sqrt вернёт NaN
    noise = np.sqrt(np.maximum(uniform_filter(hp * hp, 51, mode="nearest"), 0))
    snr = d8 / np.maximum(noise, 1e-4)
    flags = ((d8 > d2) * 1 + (nb >= 2) * 2 + (snr >= 5) * 4).astype(np.uint8)
    return {"P": P, "G": G, "frac": frac, "flags": flags, "noise": noise, "d_fdi": d_fdi, "F": F, "names": names}


def detect_scene(aoi_id: str, date: str, water: np.ndarray) -> dict:
    from .s2 import load_saved

    out = PROCESSED / aoi_id / date
    refl, scl, prof = load_saved(aoi_id, date)
    valid = water & np.isfinite(refl[1]) & ~np.isin(scl, list(BAD_SCL))
    r = detect_array(refl, valid)
    P, G, frac, flags, noise = r["P"], r["G"], r["frac"], r["flags"], r["noise"]

    prof_out = prof.copy()
    prof_out.update(count=5, dtype="uint8", nodata=None)
    with rasterio.open(out / "det.tif", "w", **prof_out) as dst:
        dst.write((P * 255).round().astype(np.uint8), 1)
        dst.write((frac * 255).round().astype(np.uint8), 2)
        dst.write(G, 3)
        dst.write(valid.astype(np.uint8), 4)
        dst.write(flags, 5)

    rgb = np.nan_to_num(refl[[BAND_IDX["B04"], BAND_IDX["B03"], BAND_IDX["B02"]]]).transpose(1, 2, 0)
    rgb = (np.clip(rgb / 0.22, 0, 1) ** (1 / 1.4) * 255).astype(np.uint8)
    Image.fromarray(rgb[::2, ::2]).save(out / "rgb.jpg", quality=82)

    water_px = water.sum()
    b11 = refl[BAND_IDX["B11"]][valid]
    stats = {
        "date": date,
        "valid_frac": float(valid.sum() / max(water_px, 1)),
        "glint": float(np.nanmedian(b11)) if b11.size else None,
        "noise": float(np.median(noise[valid])) if valid.any() else None,
        "wind": scene_wind(aoi_id, date),
        "n_raw": int(((P >= P_DET) & valid).sum()),
        "corners": corners_lonlat(prof),
    }
    (out / "det.json").write_text(json.dumps(stats), encoding="utf-8")
    return stats


def run(aoi_id: str, force: bool = False) -> None:
    water = build_water_mask(aoi_id)
    print(f"{aoi_id}: вода {water.sum() * 100 / 1e6:.1f} км²", flush=True)
    for d in dates(aoi_id):
        if not force and (PROCESSED / aoi_id / d / "det.json").exists():
            continue
        s = detect_scene(aoi_id, d, water)
        print(f"  {d} valid={s['valid_frac']:.2f} glint={s['glint'] or 0:.3f} "
              f"noise={s['noise'] or 0:.4f} wind={s['wind']} raw={s['n_raw']}", flush=True)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    for a in args or list(AOIS):
        run(a, force="--force" in sys.argv)
