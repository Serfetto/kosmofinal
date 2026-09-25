"""Устойчивость детектора к новым районам: проверка «с исключением региона» на MARIDA.

python -m pipeline.eval_detector_lro          # → data/eval/detector/leave_region_out.json

Официальное разбиение MARIDA не пересекается по сценам, но пересекается по тайлам (один район в разные даты).
Здесь сцены группируются по регионам; для каждого региона модель обучается на остальных (те же признаки и
гиперпараметры, что в pipeline/train.py) и проверяется на нём целиком — как при переносе на новую акваторию.
"""
from __future__ import annotations

import numpy as np
import xgboost as xgb

from .config import DATA, GROUPS, MARIDA_TO_GROUP
from .detect import P_DET
from .eval_detector import load_patch, patch_ids, prf
from .features import features
from .provenance import run_meta, write_json

REGIONS = {
    "Центральная Америка": ("16P", "16Q"),
    "Карибы": ("18Q", "19Q"),
    "Северная Европа": ("30V",),
    "Южная Африка": ("36J",),
    "Юго-Восточная Азия": ("48", "50L", "51P"),
    "Восточная Азия": ("51R", "52S"),
}
MAX_PER_GROUP = 150_000
DEVICE = "cuda" if xgb.build_info().get("USE_CUDA") else "cpu"


def region_of(pid: str) -> str:
    tile = pid.rsplit("_", 1)[0].split("_")[1]
    for name, prefixes in REGIONS.items():
        if tile.startswith(prefixes):
            return name
    return "прочее"


def main() -> dict:
    from .detect import detect_array

    pids = patch_ids("train") + patch_ids("val") + patch_ids("test")
    X, y, reg = [], [], []
    for pid in pids:
        x, cl = load_patch(pid)
        f, _ = features(x)
        m = cl > 0
        X.append(f[:, m].T)
        y.append(np.vectorize(MARIDA_TO_GROUP.get)(cl[m]))
        reg += [region_of(pid)] * int(m.sum())
    X, y, reg = np.concatenate(X), np.concatenate(y), np.asarray(reg)
    rng = np.random.default_rng(0)
    out = {}
    for name in REGIONS:
        test = reg == name
        if not test.any() or not (y[test] == 0).any():
            continue
        tr = np.flatnonzero(~test)
        keep = np.concatenate([rng.choice(tr[y[tr] == g], min(MAX_PER_GROUP, (y[tr] == g).sum()), replace=False)
                               for g in np.unique(y[tr])])
        counts = np.bincount(y[keep], minlength=len(GROUPS))
        w = (len(keep) / (len(GROUPS) * np.maximum(counts, 1)))[y[keep]]
        model = xgb.XGBClassifier(objective="multi:softprob", n_estimators=600, learning_rate=0.08, max_depth=8,
                                  subsample=0.8, colsample_bytree=0.8, min_child_weight=5, tree_method="hist",
                                  device=DEVICE)
        model.fit(X[keep], y[keep], sample_weight=w, verbose=False)
        # Полная цепочка сервиса на патчах региона: модель + фильтры пены, соседства и CFAR
        import pipeline.detect as D

        saved = D._model
        base = saved or D.model()
        D._model = {"model": model, "names": base["names"], "dense_endmember": base["dense_endmember"]}
        tp = fp = fn = tpf = fpf = fnf = 0
        n_patches = 0
        try:
            for pid in pids:
                if region_of(pid) != name:
                    continue
                x, cl = load_patch(pid)
                r = detect_array(x, np.isfinite(x).all(0))
                lab, yy = cl > 0, cl == 1
                p = (r["P"] >= P_DET)
                pf = p & (r["flags"] == 7)
                tp += int((p & yy).sum()); fp += int((p & lab & ~yy).sum()); fn += int((~p & yy).sum())
                tpf += int((pf & yy).sum()); fpf += int((pf & lab & ~yy).sum()); fnf += int((~pf & yy).sum())
                n_patches += 1
        finally:
            D._model = saved
        out[name] = {"n_patches": n_patches, "n_debris_px": int((y[test] == 0).sum()),
                     "xgb": prf(tp, fp, fn), "xgb_filters": prf(tpf, fpf, fnf)}
        r = out[name]["xgb_filters"]
        print(f"{name:22s} патчей {n_patches:4d}  P={r['precision']:.3f} R={r['recall']:.3f} F1={r['f1']:.3f} IoU={r['iou']:.3f}",
              flush=True)
    f1 = [v["xgb_filters"]["f1"] for v in out.values()]
    res = {"regions": out, "mean_f1_filters": float(np.mean(f1)), "min_f1_filters": float(np.min(f1)),
           "note": "обучение на остальных регионах (train+val+test MARIDA), проверка на регионе целиком", "meta": run_meta()}
    write_json(DATA / "eval" / "detector" / "leave_region_out.json", res)
    return res


if __name__ == "__main__":
    main()
