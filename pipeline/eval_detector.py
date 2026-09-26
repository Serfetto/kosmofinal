"""Проверка детектора на MARIDA: базовые и основной алгоритм на одной выборке.

python -m pipeline.eval_detector            # → data/eval/detector/{metrics.json, errors.csv, pred_test.npz, ref_test.npz,
                                            #   gallery.png}, data/splits/marida/

Положительный класс — MARIDA 1 «Marine Debris» (плавающий мусор любого материала, не только пластик).
Подтверждённый фон — размеченные классы 2–15. Неразмеченные пиксели (0) в метриках игнорируются;
срабатывания на них считаются отдельно. Пороги базовых алгоритмов подбираются на val, порог основного
(P ≥ 0,5) задан до проверки; test используется только для итоговых чисел.
"""
from __future__ import annotations

import json

import numpy as np
import rasterio
from PIL import Image
from scipy.ndimage import label
from sklearn.naive_bayes import GaussianNB

from .config import BAND_IDX, DATA, GROUPS, MARIDA_CLASSES, MARIDA_TO_GROUP, RAW
from .detect import P_DET, detect_array
from .provenance import run_meta, write_json

MARIDA = RAW / "marida"
OUT = DATA / "eval" / "detector"
METHODS = ["fdi_window", "biermann_nb", "xgb", "xgb_filters"]
METHOD_NAMES = {
    "fdi_window": "Пороги FDI и NDVI (базовый, подобраны на val)",
    "biermann_nb": "Наивный Байес на FDI+NDVI, как Biermann et al. 2020 (базовый)",
    "xgb": "XGBoost, P ≥ 0,5 (основной)",
    "xgb_filters": "XGBoost + фильтры пены/шума/CFAR (основной, как в сервисе)",
}
N_BOOT = 1000


def patch_ids(split: str) -> list[str]:
    return (MARIDA / "splits" / f"{split}_X.txt").read_text().split()


def load_patch(pid: str):
    scene = "S2_" + pid.rsplit("_", 1)[0]
    base = MARIDA / "patches" / scene / f"S2_{pid}"
    with rasterio.open(f"{base}.tif") as s:
        x = s.read().astype(np.float32)
    with rasterio.open(f"{base}_cl.tif") as s:
        cl = s.read(1).astype(np.int16)
    return x, cl


def scene_of(pid: str) -> str:
    return pid.rsplit("_", 1)[0]


def run_patch(x: np.ndarray) -> dict:
    valid = np.isfinite(x).all(0)
    r = detect_array(x, valid)
    names = r["names"]
    return {"P": r["P"], "flags": r["flags"], "d_fdi": r["d_fdi"], "ndvi": r["F"][names.index("NDVI")],
            "fdi": r["F"][names.index("FDI")], "valid": valid}


def collect(split: str) -> list[dict]:
    out = []
    for pid in patch_ids(split):
        x, cl = load_patch(pid)
        r = run_patch(x)
        r.update(pid=pid, cl=cl, x=x[[BAND_IDX["B04"], BAND_IDX["B03"], BAND_IDX["B02"]]])
        out.append(r)
    return out


def best_threshold(score: np.ndarray, y: np.ndarray) -> float:
    """Порог по максимуму F1 (подбирается только на val)."""
    qs = np.unique(np.quantile(score, np.linspace(0.5, 0.9995, 400)))
    best, bt = -1, qs[0]
    for t in qs:
        p = score > t
        tp = (p & y).sum()
        f1 = 2 * tp / max(p.sum() + y.sum(), 1)
        if f1 > best:
            best, bt = f1, t
    return float(bt)


def fdi_window(d_fdi: np.ndarray, ndvi: np.ndarray, y: np.ndarray) -> dict:
    """Пороговый базовый алгоритм: аномалия FDI в окне [lo, hi] и NDVI ниже порога (водоросли зеленее).

    Верхняя граница нужна, потому что мутная вода и плотный саргассум дают FDI выше мусора.
    Сетка перебирается только на val.
    """
    best, out = -1.0, None
    for lo in (0.005, 0.01, 0.015, 0.02, 0.03):
        for hi in (0.04, 0.06, 0.08, 0.1, 0.15, 0.2, 1.0):
            for nmax in (0.0, 0.1, 0.2, 0.3, 1.0):
                p = (d_fdi > lo) & (d_fdi < hi) & (ndvi < nmax)
                tp = (p & y).sum()
                f1 = 2 * tp / max(p.sum() + y.sum(), 1)
                if f1 > best:
                    best, out = f1, {"fdi_lo": lo, "fdi_hi": hi, "ndvi_max": nmax, "val_f1": float(f1)}
    return out


def counts(pred: np.ndarray, cl: np.ndarray) -> dict:
    lab = cl > 0
    y = cl == 1
    return {"tp": int((pred & y).sum()), "fp": int((pred & lab & ~y).sum()), "fn": int((~pred & y).sum()),
            "unlabeled_pos": int((pred & ~lab).sum()), "n_debris": int(y.sum()),
            "fp_by_class": {int(c): int((pred & (cl == c)).sum()) for c in np.unique(cl[lab & ~y])}}


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / max(tp + fp, 1)
    r = tp / max(tp + fn, 1)
    return {"precision": p, "recall": r, "f1": 2 * p * r / max(p + r, 1e-12), "iou": tp / max(tp + fp + fn, 1)}


def object_recall(pred: np.ndarray, cl: np.ndarray) -> tuple[int, int]:
    lab, n = label(cl == 1, structure=np.ones((3, 3)))
    if n == 0:
        return 0, 0
    hit = np.unique(lab[pred & (lab > 0)])
    return int(len(hit[hit > 0])), int(n)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("val…", flush=True)
    val = collect("val")
    print("train (для наивного Байеса)…", flush=True)
    rng = np.random.default_rng(0)
    Xtr, ytr = [], []
    for pid in patch_ids("train"):
        x, cl = load_patch(pid)
        r = run_patch(x)
        m = cl > 0
        Xtr.append(np.c_[r["fdi"][m], r["ndvi"][m]])
        ytr.append(np.vectorize(MARIDA_TO_GROUP.get)(cl[m]))
    Xtr, ytr = np.concatenate(Xtr), np.concatenate(ytr)
    keep = np.concatenate([rng.choice(np.flatnonzero(ytr == g), min(50_000, (ytr == g).sum()), replace=False)
                           for g in np.unique(ytr)])
    nb = GaussianNB().fit(Xtr[keep], ytr[keep])
    deb = list(nb.classes_).index(GROUPS.index("debris"))

    def scores(r):
        return {
            "biermann_nb": nb.predict_proba(np.c_[r["fdi"].ravel(), r["ndvi"].ravel()])[:, deb].reshape(r["fdi"].shape),
            "xgb": r["P"],
        }

    # Пороги на val
    vs = {m: [] for m in ("biermann_nb", "xgb")}
    vy, vf, vn = [], [], []
    for r in val:
        s = scores(r)
        lab = r["cl"] > 0
        for m in vs:
            vs[m].append(s[m][lab])
        vy.append(r["cl"][lab] == 1)
        vf.append(r["d_fdi"][lab])
        vn.append(r["ndvi"][lab])
    vy, vf, vn = np.concatenate(vy), np.concatenate(vf), np.concatenate(vn)
    thr = {m: best_threshold(np.concatenate(v), vy) for m, v in vs.items()}
    window = fdi_window(vf, vn, vy)
    thr_used = {"fdi_window": window, "biermann_nb": thr["biermann_nb"], "xgb": P_DET, "xgb_filters": P_DET}
    print("пороги (val):", thr_used, "оптимум XGB на val:", thr["xgb"], flush=True)

    print("test…", flush=True)
    test = collect("test")
    per = []
    preds = {m: {} for m in METHODS}
    for r in test:
        s = scores(r)
        w = thr_used["fdi_window"]
        pm = {
            "fdi_window": (r["d_fdi"] > w["fdi_lo"]) & (r["d_fdi"] < w["fdi_hi"]) & (r["ndvi"] < w["ndvi_max"]),
            "biermann_nb": s["biermann_nb"] > thr_used["biermann_nb"],
            "xgb": r["P"] >= P_DET,
            "xgb_filters": (r["P"] >= P_DET) & (r["flags"] == 7),
        }
        for m, p in pm.items():
            p &= r["valid"]
            c = counts(p, r["cl"])
            oh, on = object_recall(p, r["cl"])
            per.append({"pid": r["pid"], "scene": scene_of(r["pid"]), "method": m, **c, "obj_hit": oh, "obj_n": on})
            preds[m][r["pid"]] = np.packbits(p)
        preds["xgb"][r["pid"] + "__P"] = (r["P"] * 255).round().astype(np.uint8)

    metrics = {"methods": {}, "thresholds": thr_used, "val_optimal_threshold_xgb": thr["xgb"],
               "positive_class": "MARIDA 1 Marine Debris (плавающий мусор любого материала)",
               "background_classes": {int(k): v for k, v in MARIDA_CLASSES.items() if k != 1},
               "ignored": "неразмеченные пиксели MARIDA (класс 0)",
               "split": "официальное разбиение MARIDA: test — 15 сцен, не пересекается с train/val по сценам; "
                        "пересекается по тайлам (16PCC, 16PDC, 16PEC, 16QED, 18QYF, 48PZC)",
               "n_test_patches": len(test), "n_test_scenes": len({scene_of(r["pid"]) for r in test})}
    scenes = sorted({p["scene"] for p in per})
    for m in METHODS:
        rows = [p for p in per if p["method"] == m]
        tp, fp, fn = (sum(p[k] for p in rows) for k in ("tp", "fp", "fn"))
        res = prf(tp, fp, fn)
        res.update(tp=tp, fp=fp, fn=fn, unlabeled_pos=sum(p["unlabeled_pos"] for p in rows),
                   object_recall=sum(p["obj_hit"] for p in rows) / max(sum(p["obj_n"] for p in rows), 1))
        # Ложные срабатывания по классам фона: доля пикселей класса, принятых за мусор
        tot = {}
        for r in test:
            for c, n in zip(*np.unique(r["cl"], return_counts=True)):
                tot[int(c)] = tot.get(int(c), 0) + int(n)
        fpc = {}
        for p in rows:
            for c, n in p["fp_by_class"].items():
                fpc[c] = fpc.get(c, 0) + n
        res["fp_by_class"] = {MARIDA_CLASSES[c]: {"fp_px": fpc.get(c, 0), "class_px": tot.get(c, 0),
                                                  "fp_rate": fpc.get(c, 0) / max(tot.get(c, 0), 1)}
                              for c in sorted(tot) if c not in (0, 1)}
        # Бутстреп по сценам
        by_scene = {s: [p for p in rows if p["scene"] == s] for s in scenes}
        rng = np.random.default_rng(1)
        boot = []
        for _ in range(N_BOOT):
            pick = rng.choice(scenes, len(scenes), replace=True)
            t = sum(p["tp"] for s in pick for p in by_scene[s])
            f = sum(p["fp"] for s in pick for p in by_scene[s])
            n_ = sum(p["fn"] for s in pick for p in by_scene[s])
            boot.append([v for v in prf(t, f, n_).values()])
        lo, hi = np.percentile(np.asarray(boot), [2.5, 97.5], axis=0)
        res["ci95"] = {k: [float(a), float(b)] for k, a, b in zip(("precision", "recall", "f1", "iou"), lo, hi)}
        res["name"] = METHOD_NAMES[m]
        metrics["methods"][m] = res
        print(f"{m:16s} P={res['precision']:.3f} R={res['recall']:.3f} F1={res['f1']:.3f} IoU={res['iou']:.3f}", flush=True)

    metrics["meta"] = run_meta()
    write_json(OUT / "metrics.json", metrics)
    import csv

    with open(OUT / "errors.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["patch", "scene", "method", "tp", "fp", "fn", "unlabeled_pos", "n_debris", "top_fp_class"])
        for p in per:
            top = max(p["fp_by_class"].items(), key=lambda kv: kv[1], default=(None, 0))
            w.writerow([p["pid"], p["scene"], p["method"], p["tp"], p["fp"], p["fn"], p["unlabeled_pos"],
                        p["n_debris"], MARIDA_CLASSES.get(top[0], "") if top[1] else ""])
    np.savez_compressed(OUT / "pred_test.npz", **{f"{m}/{k}": v for m, d in preds.items() for k, v in d.items()})
    # Эталонная разметка MARIDA test и списки разбиения — чтобы метрики пересчитывались без MARIDA (pipeline.verify)
    np.savez_compressed(OUT / "ref_test.npz", **{r["pid"]: r["cl"].astype(np.uint8) for r in test})
    splits = DATA / "splits" / "marida"
    splits.mkdir(parents=True, exist_ok=True)
    for part in ("train", "val", "test"):
        (splits / f"{part}_X.txt").write_bytes((MARIDA / "splits" / f"{part}_X.txt").read_bytes())
    gallery(test, [p for p in per if p["method"] == "xgb_filters"])


def gallery(test: list[dict], rows: list[dict], n: int = 6) -> None:
    """Худшие ложные срабатывания и пропуски основного алгоритма: зелёный — TP, красный — FP, жёлтый — FN."""
    by = {r["pid"]: r for r in test}
    worst_fp = sorted(rows, key=lambda p: -p["fp"])[:n]
    worst_fn = sorted(rows, key=lambda p: -p["fn"])[:n]
    tiles = []
    for p in worst_fp + worst_fn:
        r = by[p["pid"]]
        rgb = np.clip(np.nan_to_num(r["x"]).transpose(1, 2, 0) / 0.15, 0, 1) ** (1 / 1.6)
        rgb = (rgb * 255).astype(np.uint8)
        pred = (r["P"] >= P_DET) & (r["flags"] == 7) & r["valid"]
        y = r["cl"] == 1
        lab = r["cl"] > 0
        # Пиксели мусора мелкие — метки утолщаем, чтобы их было видно на обзорной картинке
        from scipy.ndimage import binary_dilation

        for mask, col in ((~pred & y, [255, 220, 0]), (pred & lab & ~y, [255, 40, 40]), (pred & y, [40, 220, 90])):
            rgb[binary_dilation(mask, iterations=1)] = col
        tiles.append(np.kron(rgb, np.ones((2, 2, 1), np.uint8)))
    if not tiles:
        return
    h, w, _ = tiles[0].shape
    grid = np.zeros((2 * h + 4, n * w + (n - 1) * 4, 3), np.uint8)
    for i, t in enumerate(tiles):
        rr, cc = divmod(i, n)
        grid[rr * (h + 4):rr * (h + 4) + h, cc * (w + 4):cc * (w + 4) + w] = t
    Image.fromarray(grid).save(OUT / "gallery.png")
    (OUT / "gallery.json").write_text(json.dumps({"top_row_false_positives": [p["pid"] for p in worst_fp],
                                                  "bottom_row_misses": [p["pid"] for p in worst_fn]}), encoding="utf-8")


if __name__ == "__main__":
    main()
