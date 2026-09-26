"""Сквозной пересчёт: полевые данные → реестр пар → разбиение → концентрация → детектор → сводка.

python -m pipeline.reproduce                 # всё, что не требует скачивания снимков
python -m pipeline.reproduce --offline       # реестр пар из кеша STAC и качества (без сети)
python -m pipeline.reproduce --skip-marida   # без проверки детектора (если MARIDA не скачана)
python -m pipeline.reproduce --with-web      # плюс пересборка карт акваторий (нужны data/processed)
python -m pipeline.reproduce --summary       # только пересобрать сводку по готовым метрикам

Итог — docs/summary.md с метриками, на которые ссылается docs/report.md.
"""
from __future__ import annotations

import json
import sys
import time

from .config import DATA, RAW, ROOT

SUMMARY = ROOT / "docs" / "summary.md"


def step(name: str, fn) -> None:
    t = time.time()
    print(f"\n=== {name}", flush=True)
    fn()
    print(f"    готово за {time.time() - t:.0f} с", flush=True)


def summary() -> str:
    ev = DATA / "eval"
    lines = ["# Сводка метрик", ""]
    det = ev / "detector" / "metrics.json"
    if det.exists():
        m = json.loads(det.read_text(encoding="utf-8"))
        lines += [f"## Детектор — MARIDA test ({m['n_test_scenes']} сцен, {m['n_test_patches']} патчей)", "",
                  "| Метод | P | R | F1 (95% ДИ по сценам) | IoU |", "|---|---:|---:|---:|---:|"]
        for k, v in m["methods"].items():
            lines.append(f"| {v['name']} | {v['precision']:.3f} | {v['recall']:.3f} | {v['f1']:.3f} "
                         f"({v['ci95']['f1'][0]:.2f}–{v['ci95']['f1'][1]:.2f}) | {v['iou']:.3f} |")
        lines.append("")
    mad = ev / "detector" / "mados.json"
    if mad.exists():
        m = json.loads(mad.read_text(encoding="utf-8"))
        v = m["methods"]["xgb_filters"]
        lines += [f"## Детектор — MADOS test, новые сцены ({m['n_scenes']} сцен, мусор в {m['n_scenes_with_debris']})",
                  "", "| Метод | P | R | F1 (95% ДИ по сценам) | IoU |", "|---|---:|---:|---:|---:|",
                  f"| XGBoost + фильтры (как в сервисе) | {v['precision']:.3f} | {v['recall']:.3f} | {v['f1']:.3f} "
                  f"({v['ci95']['f1'][0]:.2f}–{v['ci95']['f1'][1]:.2f}) | {v['iou']:.3f} |", "",
                  "Доля пикселей фона, принятых за мусор: " + ", ".join(
                      f"{k} {100 * c['fp_rate']:.2f}%" for k, c in m["fp_by_class"].items() if c["fp_rate"] > 0) + ".",
                  ""]
    for pid in ("A", "B"):
        p = ev / "concentration" / f"{pid}_metrics.json"
        if not p.exists():
            continue
        r = json.loads(p.read_text(encoding="utf-8"))
        lines += [f"## Концентрация, профиль {pid} (признаки: {', '.join(r['features'])})", "",
                  "| Модель | CV MAE | CV RMSE | Отложенная MAE | Отложенная RMSE |", "|---|---:|---:|---:|---:|"]
        for m in r["cv"]:
            star = " ★" if m == r["serve_model"] else ""
            lines.append(f"| {m}{star} | {r['cv'][m]['mae']:.1f} | {r['cv'][m]['rmse']:.1f} | "
                         f"{r['holdout'][m]['mae']:.1f} | {r['holdout'][m]['rmse']:.1f} |")
        lines += ["", f"Обучение {r['n_dev']} событий, отложено {r['n_holdout']}, групп {r['n_groups']}; "
                      f"покрытие 80%-интервала на отложенной {r['holdout_interval_coverage']['0.8']:.0%}."]
        if r.get("poisson_floor_mae_dev"):
            lines.append(f"Нижняя граница MAE из-за счётного шума: {r['poisson_floor_mae_dev']:.1f} шт./км².")
        if "transfer_check" in r:
            t = r["transfer_check"]
            lines.append(f"Перенос S4 → S3 ({t['n_events']} событий): MAE " + ", ".join(
                f"{m} {v['mae']:.0f}" for m, v in t.items() if isinstance(v, dict)) +
                f"; медианы {t['target_median_train']:.0f} → {t['target_median_check']:.0f} шт./км².")
        lines.append("")
    reg = DATA / "registry" / "summary.json"
    if reg.exists():
        s = json.loads(reg.read_text(encoding="utf-8"))
        lines += ["## Реестр пар", "", f"Событий {s['events']}, кандидатов-сцен {s['candidates']}.",
                  "Итог по событиям: " + ", ".join(f"{k} {v}" for k, v in s["events_by_outcome"].items()) + ".",
                  "Причины: " + ", ".join(f"{k} {v}" for k, v in s["rows_by_reason"].items()) + ".", ""]
    return "\n".join(lines)


def write_summary() -> str:
    text = summary()
    SUMMARY.write_text(text, encoding="utf-8")
    return text


def main() -> None:
    if "--summary" in sys.argv:
        print(write_summary())
        return
    offline = "--offline" in sys.argv
    from . import concentration, field, pairs, splits

    step("Полевой реестр: отбор по профилям", field.prepare)
    step("Реестр пар «событие ↔ снимок»", lambda: pairs.build(offline=offline))
    step("Разбиение с группами", lambda: [splits.build(p) for p in ("A", "B")])
    step("Концентрация: CV, отложенная выборка, перенос", lambda: [concentration.evaluate(p) for p in ("A", "B")])
    if "--skip-marida" not in sys.argv:
        if (RAW / "marida" / "splits").exists():
            from . import eval_detector, mados

            step("Детектор: MARIDA test, базовые и основной", eval_detector.main)
            if mados.available():
                from . import eval_detector_mados

                step("Детектор: новые сцены MADOS test", eval_detector_mados.main)
            else:
                print(f"MADOS не найден ({mados.ZIP}) — проверка на MADOS пропущена")
        else:
            print("MARIDA не найдена в data/raw/marida — проверка детектора пропущена")
    if "--with-web" in sys.argv:
        from . import aggregate
        from .config import AOIS, PROCESSED

        for a in AOIS:
            if (PROCESSED / a / "water.tif").exists():
                step(f"Карта акватории {a}", lambda a=a: aggregate.run(a))
    print("\n" + write_summary())


if __name__ == "__main__":
    main()
