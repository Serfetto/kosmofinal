"""Обучение пиксельного классификатора на MARIDA (XGBoost на GPU, CUDA).

python -m pipeline.train
"""
import json

import joblib
import numpy as np
import rasterio
import xgboost as xgb
from sklearn.metrics import average_precision_score, classification_report, confusion_matrix

from .config import GROUPS, MARIDA_TO_GROUP, MODELS, RAW
from .features import features

MARIDA = RAW / "marida"
MAX_PER_GROUP = 250_000
DEVICE = "cuda" if xgb.build_info().get("USE_CUDA") else "cpu"
rng = np.random.default_rng(0)


def load_split(name: str):
    X, y = [], []
    for pid in (MARIDA / "splits" / f"{name}_X.txt").read_text().split():
        scene = "S2_" + pid.rsplit("_", 1)[0]
        base = MARIDA / "patches" / scene / f"S2_{pid}"
        with rasterio.open(f"{base}.tif") as s:
            x = s.read().astype(np.float32)
        with rasterio.open(f"{base}_cl.tif") as s:
            cl = s.read(1).astype(np.int16)
        f, names = features(x)
        m = cl > 0
        X.append(f[:, m].T)
        y.append(np.vectorize(MARIDA_TO_GROUP.get)(cl[m]))
    return np.concatenate(X), np.concatenate(y), names


def balance(X, y):
    keep = []
    for g in np.unique(y):
        i = np.flatnonzero(y == g)
        if len(i) > MAX_PER_GROUP:
            i = rng.choice(i, MAX_PER_GROUP, replace=False)
        keep.append(i)
    keep = np.concatenate(keep)
    return X[keep], y[keep]


def main():
    MODELS.mkdir(parents=True, exist_ok=True)
    Xtr, ytr, names = load_split("train")
    Xva, yva, _ = load_split("val")
    Xte, yte, _ = load_split("test")
    print("pixels train/val/test:", len(ytr), len(yva), len(yte))
    print("train groups:", {GROUPS[g]: int((ytr == g).sum()) for g in np.unique(ytr)})
    Xtr, ytr = balance(Xtr, ytr)

    counts = np.bincount(ytr, minlength=len(GROUPS))
    w = (len(ytr) / (len(GROUPS) * np.maximum(counts, 1)))[ytr]
    model = xgb.XGBClassifier(
        objective="multi:softprob", n_estimators=1500, learning_rate=0.05, max_depth=8,
        subsample=0.8, colsample_bytree=0.8, min_child_weight=5, tree_method="hist",
        device=DEVICE, early_stopping_rounds=60, eval_metric="mlogloss",
    )
    model.fit(Xtr, ytr, sample_weight=w, eval_set=[(Xva, yva)], verbose=False)
    print("device:", DEVICE, "trees:", model.best_iteration + 1)

    proba = model.predict_proba(Xte)
    pred = proba.argmax(1)
    rep = classification_report(yte, pred, labels=list(range(len(GROUPS))), target_names=GROUPS,
                                output_dict=True, zero_division=0)
    print(classification_report(yte, pred, labels=list(range(len(GROUPS))), target_names=GROUPS, zero_division=0))
    ap = average_precision_score(yte == 0, proba[:, 0])
    print("debris AP:", round(ap, 3))

    # Эталон «плотного скопления» для смешения пикселя: 99-й перцентиль FDI среди пикселей мусора
    deb = Xtr[ytr == 0]
    fdi = deb[:, names.index("FDI")]
    dense = deb[fdi >= np.percentile(fdi, 99), :11].mean(0)

    imp = sorted(zip(names, model.feature_importances_), key=lambda t: -t[1])
    metrics = {
        "report": rep, "debris_ap": ap,
        "confusion": confusion_matrix(yte, pred, labels=list(range(len(GROUPS)))).tolist(),
        "groups": GROUPS, "n_test": int(len(yte)),
        "importance": [(n, float(v)) for n, v in imp],
    }
    joblib.dump({"model": model, "names": names, "dense_endmember": dense.tolist()}, MODELS / "xgb.joblib")
    (MODELS / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
    print("top features:", [n for n, _ in imp[:10]])


if __name__ == "__main__":
    main()
