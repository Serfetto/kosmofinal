"""Детектор на новых сценах MADOS test: мусор и сложный фон, которого нет в MARIDA (нефть, слизь, медузы, платформы).

python -m pipeline.eval_detector_mados                          # модель сервиса → data/eval/detector/mados.json
python -m pipeline.eval_detector_mados --model m.joblib --out mados_other.json   # другая модель, для сравнения

Берутся только сцены MADOS test, которых нет в MARIDA (27 сцен): ни одна модель их не видела. Цепочка та же,
что в сервисе и на MARIDA test: модель, P ≥ p_det, фильтры пены, соседства и CFAR. Неразмеченные пиксели в
метриках не участвуют. Мусор есть только в 4 из 27 сцен, поэтому интервалы F1 широкие.
"""
from __future__ import annotations

import sys

import joblib
import numpy as np

from . import detect, mados
from .config import DATA, MADOS_CLASSES
from .eval_detector import N_BOOT, object_recall, prf
from .provenance import run_meta, write_json

OUT = DATA / "eval" / "detector"


def evaluate() -> dict:
    per, tot, fp_cls, raw_cls = [], np.zeros(16, np.int64), np.zeros(16, np.int64), np.zeros(16, np.int64)
    conf_n, conf_hit = np.zeros(4, np.int64), np.zeros(4, np.int64)
    obj_hit = obj_n = 0
    for pid in mados.new_ids("test"):
        x, cl, conf = mados.load_patch(pid)
        valid = np.isfinite(x).all(0)
        r = detect.detect_array(x, valid)
        raw = (r["P"] >= detect.P_DET) & valid
        pred = raw & (r["flags"] == 7)
        y, lab = cl == 1, cl > 0
        tot += np.bincount(cl.ravel(), minlength=16)[:16]
        fp_cls += np.bincount(cl[pred].ravel(), minlength=16)[:16]
        raw_cls += np.bincount(cl[raw].ravel(), minlength=16)[:16]
        for c in (1, 2, 3):
            conf_n[c] += int((y & (conf == c)).sum())
            conf_hit[c] += int((y & pred & (conf == c)).sum())
        h, n = object_recall(pred, cl)
        obj_hit += h
        obj_n += n
        per.append({"scene": mados.scene_of(pid), "tp": int((pred & y).sum()), "fp": int((pred & lab & ~y).sum()),
                    "fn": int((~pred & y).sum()), "tp_raw": int((raw & y).sum()),
                    "fp_raw": int((raw & lab & ~y).sum()), "fn_raw": int((~raw & y).sum())})
    scenes = sorted({p["scene"] for p in per})
    res = {}
    for key, sfx in (("xgb_filters", ""), ("xgb", "_raw")):
        tp, fp, fn = (sum(p[k + sfx] for p in per) for k in ("tp", "fp", "fn"))
        m = prf(tp, fp, fn)
        by = {s: np.array([[p["tp" + sfx], p["fp" + sfx], p["fn" + sfx]] for p in per if p["scene"] == s]).sum(0)
              for s in scenes}
        rng = np.random.default_rng(1)
        boot = [list(prf(*np.sum([by[s] for s in rng.choice(scenes, len(scenes))], 0).tolist()).values())
                for _ in range(N_BOOT)]
        lo, hi = np.percentile(np.asarray(boot), [2.5, 97.5], axis=0)
        m.update(tp=tp, fp=fp, fn=fn, ci95={k: [float(a), float(b)] for k, a, b in
                                            zip(("precision", "recall", "f1", "iou"), lo, hi)})
        res[key] = m
    res["xgb_filters"]["object_recall"] = obj_hit / max(obj_n, 1)
    res["xgb_filters"]["objects"] = [obj_hit, obj_n]
    return {
        "set": "MADOS test, только сцены, которых нет в MARIDA",
        "n_scenes": len(scenes), "n_patches": len(per),
        "n_scenes_with_debris": len({p["scene"] for p in per if p["tp"] + p["fn"] > 0}),
        "n_debris_px": int(tot[1]), "p_det": detect.P_DET,
        "methods": res,
        "fp_by_class": {MADOS_CLASSES[c]: {"fp_px": int(fp_cls[c]), "class_px": int(tot[c]),
                                           "fp_rate": float(fp_cls[c] / tot[c]),
                                           "fp_rate_no_filters": float(raw_cls[c] / tot[c])}
                        for c in range(2, 16) if tot[c]},
        "recall_by_label_confidence": {name: {"debris_px": int(conf_n[c]), "found": int(conf_hit[c])}
                                       for c, name in ((1, "high"), (2, "moderate"), (3, "low")) if conf_n[c]},
        "per_scene": per,
    }


def main() -> None:
    args = sys.argv[1:]
    out = args[args.index("--out") + 1] if "--out" in args else "mados.json"
    if "--model" in args:
        detect._model = joblib.load(args[args.index("--model") + 1])
    if not mados.available():
        sys.exit(f"нет {mados.ZIP}: скачайте MADOS ({mados.URL})")
    res = evaluate()
    m = res["methods"]["xgb_filters"]
    print(f"MADOS test ({res['n_scenes']} новых сцен): P={m['precision']:.3f} R={m['recall']:.3f} F1={m['f1']:.3f} "
          f"(95% ДИ {m['ci95']['f1'][0]:.2f}–{m['ci95']['f1'][1]:.2f})")
    for k, v in res["fp_by_class"].items():
        print(f"  {k:26s} {100 * v['fp_rate']:6.2f}% пикселей принято за мусор")
    res["model"] = detect.model().get("training_data")
    res["meta"] = run_meta("detector.yaml")
    write_json(OUT / out, res)


if __name__ == "__main__":
    main()
