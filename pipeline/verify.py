"""Проверка воспроизводимости без исходных датасетов: веса, разбиения, эталоны и предсказания в репозитории.

python -m pipeline.verify            # сверить sha256 и пересчитать метрики из сохранённых предсказаний
python -m pipeline.verify --update   # переписать data/models/MANIFEST.json после переобучения

Что проверяется:
1. sha256 весов моделей, разбиений train/val/test, эталонных ответов и предсказаний — с MANIFEST.json;
2. метрики детектора на MARIDA test (TP/FP/FN, P, R, F1, IoU) — заново из pred_test.npz (предсказания) и
   ref_test.npz (эталонная разметка MARIDA test), без скачивания MARIDA;
3. MAE и покрытие интервалов моделей концентрации — из A/B_predictions.csv (эталон y_true и предсказания);
4. MAE сведения с полевыми измерениями — из data/eval/fusion/*_predictions.csv;
5. модели сервиса обучены без отложенных событий, а отложенные события совпадают с data/splits.
Код выхода 1, если хоть что-то не сошлось.
"""
from __future__ import annotations

import hashlib
import json
import sys

import numpy as np
import pandas as pd

from .config import DATA, MODELS, ROOT
from .measure import mae

MANIFEST = MODELS / "MANIFEST.json"
DET = DATA / "eval" / "detector"
TRACKED = [
    "data/models/xgb.joblib", "data/models/conc_A.json", "data/models/conc_B.json",
    "data/models/fusion_A.json", "data/models/fusion_B.json",
    "data/splits/A.csv", "data/splits/B.csv", "data/splits/A_links.csv", "data/splits/B_links.csv",
    "data/splits/marida/train_X.txt", "data/splits/marida/val_X.txt", "data/splits/marida/test_X.txt",
    "data/field/events.csv", "data/field/selection.csv", "data/registry/pairs.csv",
    "data/eval/detector/pred_test.npz", "data/eval/detector/ref_test.npz", "data/eval/detector/metrics.json",
    "data/eval/concentration/A_predictions.csv", "data/eval/concentration/B_predictions.csv",
    "data/eval/fusion/A_predictions.csv", "data/eval/fusion/B_predictions.csv",
]
ROLE = {"models": "веса модели", "splits": "разбиение", "field": "полевой эталон", "registry": "реестр пар",
        "eval": "эталон/предсказания"}


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest() -> dict:
    out = {}
    for rel in TRACKED:
        p = ROOT / rel
        if p.exists():
            out[rel] = {"sha256": sha256(p), "bytes": p.stat().st_size, "role": ROLE[rel.split("/")[1]]}
    return out


def check_manifest() -> list[str]:
    if not MANIFEST.exists():
        return [f"нет {MANIFEST.relative_to(ROOT)}: python -m pipeline.verify --update"]
    saved = json.loads(MANIFEST.read_text(encoding="utf-8"))["files"]
    now = manifest()
    bad = [f"{k}: sha256 изменился" for k in saved if k in now and now[k]["sha256"] != saved[k]["sha256"]]
    bad += [f"{k}: файла нет" for k in saved if k not in now]
    return bad


def detector_counts() -> dict:
    """TP/FP/FN по методам из сохранённых предсказаний и эталонной разметки MARIDA test."""
    pred, ref = np.load(DET / "pred_test.npz"), np.load(DET / "ref_test.npz")
    out = {}
    for m in ("fdi_window", "biermann_nb", "xgb", "xgb_filters"):
        tp = fp = fn = 0
        for pid in ref.files:
            cl = ref[pid]
            p = np.unpackbits(pred[f"{m}/{pid}"])[:cl.size].reshape(cl.shape).astype(bool)
            y, lab = cl == 1, cl > 0
            tp += int((p & y).sum())
            fp += int((p & lab & ~y).sum())
            fn += int((~p & y).sum())
        prec, rec = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
        out[m] = {"tp": tp, "fp": fp, "fn": fn, "precision": prec, "recall": rec,
                  "f1": 2 * prec * rec / max(prec + rec, 1e-12), "iou": tp / max(tp + fp + fn, 1)}
    return out


def check_detector() -> list[str]:
    saved = json.loads((DET / "metrics.json").read_text(encoding="utf-8"))["methods"]
    bad = []
    for m, r in detector_counts().items():
        for k in ("tp", "fp", "fn"):
            if r[k] != saved[m][k]:
                bad.append(f"детектор {m}: {k} {r[k]} ≠ {saved[m][k]}")
        for k in ("precision", "recall", "f1", "iou"):
            if abs(r[k] - saved[m][k]) > 1e-9:
                bad.append(f"детектор {m}: {k} {r[k]:.4f} ≠ {saved[m][k]:.4f}")
    return bad


def check_concentration() -> list[str]:
    bad = []
    for pid in ("A", "B"):
        met = json.loads((DATA / "eval" / "concentration" / f"{pid}_metrics.json").read_text(encoding="utf-8"))
        pr = pd.read_csv(DATA / "eval" / "concentration" / f"{pid}_predictions.csv")
        for part, key in (("cv", "cv"), ("holdout", "holdout")):
            d = pr[pr["part"] == part]
            for m in met[key]:
                v = mae(d["y_true"], d[f"pred_{m}"])
                if abs(v - met[key][m]["mae"]) > 0.05:  # предсказания в CSV округлены до 0,001
                    bad.append(f"концентрация {pid} {part} {m}: MAE {v:.2f} ≠ {met[key][m]['mae']:.2f}")
        h = pr[pr["part"] == "holdout"]
        cov = float(np.mean((h["y_true"] >= h["serve_lo80"]) & (h["y_true"] <= h["serve_hi80"])))
        if abs(cov - met["holdout_interval_coverage"]["0.8"]) > 1e-9:
            bad.append(f"концентрация {pid}: покрытие 80% {cov:.3f} ≠ {met['holdout_interval_coverage']['0.8']:.3f}")
        model = json.loads((MODELS / f"conc_{pid}.json").read_text(encoding="utf-8"))
        sp = pd.read_csv(DATA / "splits" / f"{pid}.csv")
        if set(model["trained_on"]) & set(sp.loc[sp["is_holdout"], "event_id"]):
            bad.append(f"концентрация {pid}: модель сервиса видела отложенные события")
        if set(model["holdout"]) != set(sp.loc[sp["is_holdout"], "event_id"]):
            bad.append(f"концентрация {pid}: отложенные события модели не совпадают с data/splits/{pid}.csv")
    return bad


def check_fusion() -> list[str]:
    bad = []
    for pid in ("A", "B"):
        mp = DATA / "eval" / "fusion" / f"{pid}_metrics.json"
        if not mp.exists():
            bad.append(f"сведение {pid}: нет {mp.name} — python -m pipeline.fusion fit")
            continue
        met = json.loads(mp.read_text(encoding="utf-8"))
        pr = pd.read_csv(DATA / "eval" / "fusion" / f"{pid}_predictions.csv")
        for name, v in [("model", mae(pr["y_true"], pr["pred_model"]))] + [
                (k, mae(pr["y_true"], pr[f"pred_{k}"])) for k in met["scenarios"]]:
            ref = met["model"]["mae"] if name == "model" else met["scenarios"][name]["mae"]
            if abs(v - ref) > 1e-6:
                bad.append(f"сведение {pid} {name}: MAE {v:.2f} ≠ {ref:.2f}")
    return bad


def write_manifest() -> None:
    files = manifest()
    MANIFEST.write_text(json.dumps({"note": "sha256 весов, разбиений, эталонов и предсказаний; проверка — "
                                            "python -m pipeline.verify", "files": files},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{MANIFEST.relative_to(ROOT)}: {len(files)} файлов")


def main() -> None:
    if "--update" in sys.argv:
        write_manifest()
        return
    fails = 0
    for name, fn in (("sha256 весов, разбиений и эталонов", check_manifest),
                     ("метрики детектора из предсказаний и эталона MARIDA test", check_detector),
                     ("метрики концентрации из предсказаний", check_concentration),
                     ("метрики сведения из предсказаний", check_fusion)):
        bad = fn()
        fails += len(bad)
        print(f"{'OK ' if not bad else 'ОШИБКА'} {name}")
        for b in bad:
            print(f"    {b}")
    if fails:
        sys.exit(1)
    d = detector_counts()["xgb_filters"]
    print(f"\nДетектор (как в сервисе): P {d['precision']:.3f}, R {d['recall']:.3f}, F1 {d['f1']:.3f}, IoU {d['iou']:.3f}")


if __name__ == "__main__":
    main()
