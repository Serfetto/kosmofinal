"""Ручная проверка детекций на наших акваториях (разбор удачных и ошибочных обнаружений).

python -m pipeline.review sample [--n 120 --controls 60]   # → data/eval/review/{review.csv, sheet_XX.png}
python -m pipeline.review score                            # → data/eval/review/score.json

В review.csv команда заполняет столбец label одним из кодов LABELS (по фрагменту снимка RGB 480×480 м,
крест — центр зоны). Контрольные точки — случайная вода без детекций: по ним оценивается доля пропусков.
"""
from __future__ import annotations

import csv
import json
import sys

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.warp import transform as warp_transform

from .aggregate import WEB
from .config import AOIS, DATA, PROCESSED

OUT = DATA / "eval" / "review"
HALF = 24  # пикс. → фрагмент 48×48 пикс. = 480 м
LABELS = {
    "debris": "скопление мусора / плавающие объекты",
    "foam": "пена, барашки, волны",
    "ship": "судно, кильватер",
    "algae": "водоросли, цветение",
    "glint": "блик",
    "coast": "берег, мелководье, взвесь",
    "oil": "нефтепродукты",
    "unclear": "не ясно",
    "none": "ничего нет (для контрольных точек)",
}


def _crop(aoi: str, date: str, lon: float, lat: float) -> np.ndarray | None:
    path = PROCESSED / aoi / date / "bands.tif"
    if not path.exists():
        return None
    with rasterio.open(path) as s:
        x, y = warp_transform("EPSG:4326", s.crs, [lon], [lat])
        r, c = s.index(x[0], y[0])
        win = rasterio.windows.Window(c - HALF, r - HALF, 2 * HALF, 2 * HALF)
        rgb = s.read([4, 3, 2], window=win, boundless=True, fill_value=0).astype(np.float32) / 10000
    rgb = rgb.transpose(1, 2, 0)
    # Растяжка по фрагменту: над водой мусор отличается от фона на доли процента отражения
    lo, hi = np.percentile(rgb[rgb.sum(-1) > 0], [2, 99.5]) if (rgb.sum(-1) > 0).any() else (0, 0.2)
    rgb = (np.clip((rgb - lo) / max(hi - lo, 1e-4), 0, 1) ** (1 / 1.3) * 255).astype(np.uint8)
    return np.kron(rgb, np.ones((3, 3, 1), np.uint8))


def sample(n: int = 120, controls: int = 60, seed: int = 7) -> None:
    rng = np.random.default_rng(seed)
    zones, water = [], []
    for aoi, a in AOIS.items():
        s_path = WEB / aoi / "series.json"
        if a["kind"] != "sea" or not s_path.exists():
            continue
        s = json.loads(s_path.read_text(encoding="utf-8"))
        hexes = json.loads((WEB / aoi / "hexes.geojson").read_text(encoding="utf-8"))["features"]
        for di, d in enumerate(s["dates"]):
            zp = WEB / aoi / d / "zones.geojson"
            if zp.exists():
                for f in json.loads(zp.read_text(encoding="utf-8"))["features"]:
                    p = f["properties"]
                    zones.append({"aoi": aoi, "date": d, "lon": p["lon"], "lat": p["lat"], "zone_id": p["zone_id"],
                                  "p_max": p["p_max"], "n_pixels": p["n_pixels"], "kind": "detection"})
            for i, h in enumerate(hexes):
                if s["status"][di][i] == 0:  # «не обнаружено»
                    water.append({"aoi": aoi, "date": d, "lon": h["properties"]["lon"], "lat": h["properties"]["lat"],
                                  "zone_id": "", "p_max": "", "n_pixels": 0, "kind": "control"})
    pick = [zones[i] for i in rng.choice(len(zones), min(n, len(zones)), replace=False)]
    pick += [water[i] for i in rng.choice(len(water), min(controls, len(water)), replace=False)]
    rng.shuffle(pick)
    OUT.mkdir(parents=True, exist_ok=True)
    tiles = []
    for k, r in enumerate(pick):
        r["id"] = f"R{k + 1:03d}"
        img = _crop(r["aoi"], r["date"], r["lon"], r["lat"])
        if img is None:
            img = np.zeros((2 * HALF * 3, 2 * HALF * 3, 3), np.uint8)
        im = Image.fromarray(img)
        dr = ImageDraw.Draw(im)
        c = HALF * 3
        dr.line([(c - 12, c), (c - 5, c)], fill=(0, 255, 255))
        dr.line([(c + 5, c), (c + 12, c)], fill=(0, 255, 255))
        dr.line([(c, c - 12), (c, c - 5)], fill=(0, 255, 255))
        dr.line([(c, c + 5), (c, c + 12)], fill=(0, 255, 255))
        dr.text((3, 2), r["id"], fill=(255, 255, 0))
        tiles.append(np.asarray(im))
    per, cols = 30, 6
    for sh in range(0, len(tiles), per):
        chunk = tiles[sh:sh + per]
        h, w, _ = chunk[0].shape
        rows_n = -(-len(chunk) // cols)
        sheet = np.zeros((rows_n * (h + 4), cols * (w + 4), 3), np.uint8)
        for j, t in enumerate(chunk):
            rr, cc = divmod(j, cols)
            sheet[rr * (h + 4):rr * (h + 4) + h, cc * (w + 4):cc * (w + 4) + w] = t
        Image.fromarray(sheet).save(OUT / f"sheet_{sh // per + 1:02d}.png")
    with open(OUT / "review.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "kind", "aoi", "date", "lon", "lat", "zone_id", "p_max", "n_pixels",
                                          "label", "comment"])
        w.writeheader()
        for r in pick:
            w.writerow({**r, "label": "", "comment": ""})
    (OUT / "labels.json").write_text(json.dumps(LABELS, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(pick)} фрагментов → {OUT}; заполните столбец label кодами из labels.json")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (float(c - h), float(c + h))


def score() -> dict:
    rows = list(csv.DictReader(open(OUT / "review.csv", encoding="utf-8")))
    lab = [r for r in rows if r["label"]]
    det = [r for r in lab if r["kind"] == "detection"]
    ctl = [r for r in lab if r["kind"] == "control"]
    tp = sum(r["label"] == "debris" for r in det)
    miss = sum(r["label"] == "debris" for r in ctl)
    res = {"labelled": len(lab), "detections": len(det), "controls": len(ctl),
           "precision_local": tp / len(det) if det else None, "precision_ci95": wilson(tp, len(det)),
           "false_positive_types": {k: sum(r["label"] == k for r in det) for k in LABELS if k != "debris"},
           "debris_in_controls": miss, "debris_in_controls_ci95": wilson(miss, len(ctl))}
    (OUT / "score.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sample"
    if cmd == "sample":
        a = sys.argv[2:]
        n = int(a[a.index("--n") + 1]) if "--n" in a else 120
        c = int(a[a.index("--controls") + 1]) if "--controls" in a else 60
        sample(n, c)
    else:
        print(json.dumps(score(), ensure_ascii=False, indent=1))
