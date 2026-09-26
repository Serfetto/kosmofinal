"""Сведение источников: модель концентрации + полевые измерения с учётом расстояния и давности.

python -m pipeline.fusion fit [A B]                      # вариограмма остатков, реестр измерений, проверка
python -m pipeline.fusion explain B 41.60 41.62 2024-06-05T08:30

Метод — регрессионный кригинг остатков модели в пространстве-времени (простой кригинг, residual kriging).
В лог-шкале z = ln(C + 1):

  r_i = z_i − m(x_i)                     остаток модели в месте и в момент полевого измерения i
  ẑ(x0) = m(x0) + Σ w_i r_i,  w = K⁻¹k   сведённая оценка
  K_ij = s·exp(−h_ij/ρ) + δ_ij (τ² + σ_i²),  k_i = s·exp(−h_i0/ρ)
  h = √(d² + (v·Δt)²)                    пространственно-временное расстояние: сутки давности = v км
  σ²(x0) = s + τ² + σ̄² − kᵀK⁻¹k          дисперсия; без измерений рядом она равна дисперсии остатков модели

s, τ² и ρ подбираются по эмпирической вариограмме внефолдовых остатков модели на обучающей части (групповая CV,
отложенная выборка не участвует). v — скорость дрейфа, как в дрейфовом буфере реестра пар: 0,3 м/с течения +
2% ветра ERA5. σ_i² — счётный шум измерения: ≈ N / (N + A)² в лог-шкале, если известны число предметов N и
площадь A; иначе он входит в наггет τ².

Так измерение получает тем больший вес, чем оно ближе, свежее и точнее; соседние измерения одного разреза
не считаются независимыми (K учитывает их корреляцию). Детекции спутника в число шт./км² не входят: связь площади
маски с полевой плотностью не подтверждена (data/eval/transfer.json), снимок отвечает на вопрос «где скопление».
"""
from __future__ import annotations

import json
import sys
from datetime import timezone

import numpy as np
import pandas as pd

from . import covariates
from .concentration import EPS, EVAL as CONC_EVAL, apply_interval, event_features, from_json, load_model
from .config import DATA, MODELS
from .field import load_events
from .measure import mae, rmse
from .provenance import load_yaml, run_meta, write_json
from .splits import haversine_km

EVAL = DATA / "eval" / "fusion"
Z80, Z95 = 1.2816, 1.9600
MAX_EVIDENCE = 30        # измерений в системе кригинга, ближайших по h
MIN_WEIGHT_SHOWN = 0.005
_models: dict = {}


# ---------- вариограмма ----------

def drift_speed_km_day(wind_ms: float) -> float:
    """Скорость переноса пятна, км/сут: течение + парусность × ветер (параметры дрейфового буфера реестра пар)."""
    d = load_yaml("pairs.yaml")["drift"]
    return (d["current_ms"] + d["windage"] * wind_ms) * 86.4


def st_distance(lon0, lat0, t0, lon1, lat1, t1, v: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Расстояние d (км), давность |Δt| (сут) и пространственно-временное h = √(d² + (v·Δt)²) (км)."""
    d = haversine_km(lon0, lat0, lon1, lat1)
    dt = np.abs(np.asarray(t0, float) - np.asarray(t1, float)) / 86400.0
    return d, dt, np.sqrt(d ** 2 + (v * dt) ** 2)


def empirical_variogram(h: np.ndarray, g: np.ndarray, edges) -> list[dict]:
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        m = (h >= a) & (h < b)
        if m.sum() >= 3:
            out.append({"h_lo": float(a), "h_hi": float(b), "h_mean": float(h[m].mean()),
                        "gamma": float(g[m].mean()), "n_pairs": int(m.sum())})
    return out


def select_by_loo(lon, lat, t, r, v: float, total: float, meas_var: np.ndarray, floor: float) -> dict:
    """Параметры вариограммы γ(h) = τ² + s·(1 − e^(−h/ρ)), τ² + s = дисперсия остатков: перебор доли наггета
    и радиуса ρ по кросс-валидации кригинга с исключением по одному.

    Ошибка и дисперсия без i-го измерения считаются сразу для всех i: e = K⁻¹r / diag(K⁻¹), σ² = 1 / diag(K⁻¹)
    (Dubrule, 1983). Критерий — средний отрицательный логарифм правдоподобия ½ln σ² + e²/(2σ²): он штрафует
    и промахи, и излишнюю уверенность, поэтому интервалы не схлопываются. Наггет не меньше floor — счётного шума
    измерений; радиус — до 500 км (масштаб вихрей и прибрежных течений).
    """
    _, _, H = st_distance(lon[:, None], lat[:, None], t[:, None], lon[None], lat[None], t[None], v)
    known = np.isfinite(meas_var)
    grid = []
    for f in np.linspace(0.05, 0.95, 19):
        nugget = max(total * f, floor)
        s = total - nugget
        if s <= 0:
            continue
        micro = max(nugget - (float(np.mean(meas_var[known])) if known.any() else 0.0), 0.0)
        diag = np.where(known, micro + np.nan_to_num(meas_var), nugget)
        for rho in np.geomspace(5, 500, 25):
            Ki = np.linalg.inv(s * np.exp(-H / rho) + np.diag(diag))
            var = 1 / np.diag(Ki)
            e = (Ki @ r) * var
            nll = float(np.mean(0.5 * np.log(var) + e ** 2 / (2 * var)))
            grid.append((nll, float(np.mean(e ** 2)), nugget, s, float(rho), micro))
    nll, mse, nugget, s, rho, micro = min(grid)
    return {"nugget": nugget, "psill": s, "range_km": rho, "micro_nugget": micro, "total": float(total),
            "model": "exponential", "loo_nll": nll, "loo_mse": mse,
            "loo_nll_no_neighbours": float(np.mean(0.5 * np.log(total) + r ** 2 / (2 * total))),
            "loo_mse_no_neighbours": float(np.mean(r ** 2)),
            "selected_by": "минимум отрицательного лог-правдоподобия кригинга с исключением по одному на внефолдовых "
                           "остатках обучающей части (отложенная выборка не участвует)"}


# ---------- модель сведения ----------

def _residuals(profile: str, ev: pd.DataFrame, serve: dict) -> np.ndarray:
    """z − m(x) для событий: z = ln(C + 1), m — модель сервиса в месте и в момент события."""
    m = from_json(serve)
    return np.log(ev["conc_items_km2"].to_numpy(float) + EPS) - np.log(m.predict(ev) + EPS)


def _meas_var(ev: pd.DataFrame) -> np.ndarray:
    """Счётный шум ln(C + 1): Var ≈ N / (N + A)² (Пуассон, дельта-метод); NaN — N или A неизвестны."""
    n, a = ev["n_items"].to_numpy(float), ev["area_km2"].to_numpy(float)
    n = np.maximum(n, 1.0)
    return n / (n + a) ** 2


def _in_basins(lon, lat, basins: list[str]) -> np.ndarray:
    box = load_yaml("profiles.yaml")["basins"]
    ok = np.zeros(len(lon), bool)
    for b in basins:
        x0, y0, x1, y1 = box[b]
        ok |= (lon >= x0) & (lon <= x1) & (lat >= y0) & (lat <= y1)
    return ok


def fit(profile: str) -> dict:
    """Вариограмма остатков по обучающей части, реестр измерений для сведения и проверка на отложенной выборке."""
    cm = load_model(profile)
    serve_name = cm["serve"]["type"]
    pred = pd.read_csv(CONC_EVAL / f"{profile}_predictions.csv").drop(columns=["lon", "lat"], errors="ignore")
    ev = event_features(load_events(profile))
    ev = ev[_in_basins(ev["lon"].to_numpy(), ev["lat"].to_numpy(), cm["applicability"]["basins"])]
    ev = ev.merge(pred[["event_id", "part", "group_id", f"pred_{serve_name}"]], on="event_id", how="left")
    ev["t"] = [_ts(t) for t in ev["t_ref"]]
    v = drift_speed_km_day(float(np.nanmedian(ev["wind24_ms"])))

    # Вариограмма — по внефолдовым остаткам обучающей части: честный разброс модели вне своих данных
    dev = ev[ev["part"] == "cv"].reset_index(drop=True)
    r_oof = np.log(dev["conc_items_km2"].to_numpy(float) + EPS) - np.log(dev[f"pred_{serve_name}"].to_numpy(float) + EPS)
    _, _, H = st_distance(dev["lon"].to_numpy()[:, None], dev["lat"].to_numpy()[:, None], dev["t"].to_numpy()[:, None],
                          dev["lon"].to_numpy()[None], dev["lat"].to_numpy()[None], dev["t"].to_numpy()[None], v)
    iu = np.triu_indices(len(dev), 1)
    h, g = H[iu], 0.5 * (r_oof[iu[0]] - r_oof[iu[1]]) ** 2
    bins = empirical_variogram(h, g, [0, 15, 30, 60, 120, 250, 500, 1000, 2500, 20000])
    mv = np.where(dev["n_items"].notna(), _meas_var(dev), np.nan)
    mean_meas = float(np.nanmean(mv)) if np.isfinite(mv).any() else 0.0
    vg = select_by_loo(dev["lon"].to_numpy(float), dev["lat"].to_numpy(float), dev["t"].to_numpy(float), r_oof, v,
                       float(np.var(r_oof, ddof=1)), mv, mean_meas)
    vg["meas_var_mean"] = mean_meas

    # Реестр измерений для сведения: остатки модели сервиса (той же, что считает оценку в точке)
    ev["resid"] = _residuals(profile, ev, cm["serve"])
    ev["meas_var"] = np.where(ev["n_items"].notna(), _meas_var(ev), np.nan)
    store = [{"event_id": r["event_id"], "lon": round(float(r["lon"]), 5), "lat": round(float(r["lat"]), 5),
              "t": float(r["t"]), "t_ref": r["t_ref"], "date_utc": r["date_utc"],
              "conc": round(float(r["conc_items_km2"]), 3), "resid": round(float(r["resid"]), 5),
              "meas_var": None if np.isnan(r["meas_var"]) else round(float(r["meas_var"]), 5),
              "n_items": None if pd.isna(r["n_items"]) else float(r["n_items"]),
              "footprint_km": round(_footprint_km(r), 2), "time_known": bool(r["time_known"]),
              "role": r["role"], "part": r["part"] if isinstance(r["part"], str) else "transfer_check"}
             for _, r in ev.iterrows()]
    doc = {"profile": profile, "version": f"fusion-{profile}-{cm['version'].split('-')[-1]}",
           "method": "регрессионный кригинг остатков модели в пространстве-времени (простой кригинг)",
           "conc_model": cm["version"], "variogram": vg, "empirical": bins, "v_km_day": round(v, 2),
           "v_note": "скорость переноса пятна: течение 0,3 м/с + 2% медианного ветра ERA5 событий "
                     "(как дрейфовый буфер реестра пар)",
           "evidence": store, "meta": run_meta("pairs.yaml", "concentration.yaml")}
    write_json(MODELS / f"fusion_{profile}.json", doc)
    _models.pop(profile, None)
    res = evaluate(profile, ev)
    doc["evaluation"] = res
    return doc


def _footprint_km(r) -> float:
    """Полуразмер следа события: половина полосы учёта или радиус неопределённости точки."""
    length = r.get("length_km")
    if pd.notna(length) and length > 0:
        return float(length) / 2
    radius = r.get("footprint_radius_km")
    return float(radius) if pd.notna(radius) else 0.0


def _ts(t) -> float:
    """Момент (ISO-строка или datetime) → секунды от эпохи, UTC."""
    return covariates._parse(t).replace(tzinfo=timezone.utc).timestamp()


def load(profile: str) -> dict:
    if profile not in _models:
        p = MODELS / f"fusion_{profile}.json"
        if not p.exists():
            raise FileNotFoundError(f"нет {p.name}: python -m pipeline.fusion fit {profile}")
        _models[profile] = json.loads(p.read_text(encoding="utf-8"))
    return _models[profile]


def krige(fm: dict, lon: float, lat: float, t: float, m0: float, exclude: set[str] | None = None) -> dict:
    """Сведённая оценка в лог-шкале в точке (lon, lat) на момент t (с от эпохи); m0 = ln(Ĉ + 1) модели."""
    vg, v = fm["variogram"], fm["v_km_day"]
    s, tau2 = vg["psill"], vg["micro_nugget"]
    total = vg["total"]
    ev = [e for e in fm["evidence"] if not exclude or e["event_id"] not in exclude]
    if not ev:
        return {"z": m0, "var": total, "var0": total, "weights": np.zeros(0), "ev": [], "d": np.zeros(0),
                "dt": np.zeros(0), "h": np.zeros(0)}
    elon = np.array([e["lon"] for e in ev])
    elat = np.array([e["lat"] for e in ev])
    et = np.array([e["t"] for e in ev])
    d, dt, h = st_distance(lon, lat, t, elon, elat, et, v)
    near = np.argsort(h)[:MAX_EVIDENCE]
    near = near[h[near] < 6 * vg["range_km"]]  # дальше ковариация < 0,25% — вклад ничтожен
    if not len(near):
        return {"z": m0, "var": total, "var0": total, "weights": np.zeros(0), "ev": [], "d": np.zeros(0),
                "dt": np.zeros(0), "h": np.zeros(0)}
    ev = [ev[i] for i in near]
    d, dt, h = d[near], dt[near], h[near]
    mv = np.array([e["meas_var"] if e["meas_var"] is not None else 0.0 for e in ev])
    extra = np.where([e["meas_var"] is not None for e in ev], tau2 + mv, vg["nugget"])
    _, _, Hij = st_distance(elon[near][:, None], elat[near][:, None], et[near][:, None],
                            elon[near][None], elat[near][None], et[near][None], v)
    K = s * np.exp(-Hij / vg["range_km"]) + np.diag(extra)
    k = s * np.exp(-h / vg["range_km"])
    w = np.linalg.solve(K, k)
    r = np.array([e["resid"] for e in ev])
    var = max(total - float(k @ w), 1e-6)
    return {"z": m0 + float(w @ r), "var": var, "var0": total, "weights": w, "ev": ev, "d": d, "dt": dt, "h": h}


def fuse(profile: str, lon: float, lat: float, t_iso: str, value: float | None, q80: list[float] | None = None,
         q95: list[float] | None = None, exclude: set[str] | None = None) -> dict | None:
    """Сведение для ответа API: вклад модели и каждого измерения, сведённое значение и интервалы.

    value — оценка модели в точке, шт./км² (None — концентрация недоступна: сводить не с чем).
    Интервалы: конформные квантили модели, сжатые в √(σ²/σ₀²) — без измерений рядом они совпадают с интервалами
    модели, рядом со свежим измерением сужаются.
    """
    if value is None or not np.isfinite(value):
        return None
    fm = load(profile)
    cm = load_model(profile)
    q80 = q80 or cm["interval_log_quantiles"]["0.8"]
    q95 = q95 or cm["interval_log_quantiles"]["0.95"]
    t = _ts(t_iso)
    m0 = float(np.log(value + EPS))
    k = krige(fm, lon, lat, t, m0, exclude)
    ratio = float(np.sqrt(k["var"] / k["var0"]))
    fused = max(float(np.exp(k["z"]) - EPS), 0.0)
    lo80, hi80 = (max(float(np.exp(k["z"] + q * ratio) - EPS), 0.0) for q in q80)
    lo95, hi95 = (max(float(np.exp(k["z"] + q * ratio) - EPS), 0.0) for q in q95)
    w = k["weights"]
    w_field = float(np.clip(w.sum(), 0, 1)) if len(w) else 0.0
    evidence = []
    for i in np.argsort(-w):
        if w[i] < MIN_WEIGHT_SHOWN:
            continue
        e = k["ev"][i]
        buffer_km = e["footprint_km"] + k["dt"][i] * fm["v_km_day"]
        evidence.append({
            "event_id": e["event_id"], "date_utc": e["date_utc"], "conc_items_km2": e["conc"],
            "distance_km": round(float(k["d"][i]), 2), "age_days": round(float(k["dt"][i]), 2),
            "st_distance_km": round(float(k["h"][i]), 1), "weight": round(float(w[i]), 3),
            "model_factor": round(float(np.exp(e["resid"])), 2),
            "model_at_event": round(float((e["conc"] + EPS) * np.exp(-e["resid"]) - EPS), 1),
            "counting_noise": e["meas_var"] is not None, "n_items": e["n_items"],
            "drift_buffer_km": round(float(buffer_km), 1), "same_patch": bool(k["d"][i] <= buffer_km),
        })
    return {
        "profile": profile, "method": fm["method"], "version": fm["version"],
        "model": {"value": round(float(value), 1), "weight": round(1 - w_field, 3)},
        "fused": {"value": round(fused, 1), "lo80": round(lo80, 1), "hi80": round(hi80, 1),
                  "lo95": round(lo95, 1), "hi95": round(hi95, 1)},
        "field_weight": round(w_field, 3),
        "variance_reduction": round(1 - k["var"] / k["var0"], 3),
        "evidence": evidence, "n_candidates": len(k["ev"]),
        "params": {"psill": round(fm["variogram"]["psill"], 3), "nugget": round(fm["variogram"]["nugget"], 3),
                   "range_km": round(fm["variogram"]["range_km"], 1), "v_km_day": fm["v_km_day"]},
        "unit": "шт./км²",
    }


# ---------- проверка ----------

def evaluate(profile: str, ev: pd.DataFrame | None = None) -> dict:
    """Отложенная выборка, по одному событию: модель против сведения с остальными измерениями.

    Два сценария: «судно работает в районе» — доступны все прочие измерения, включая соседей того же рейса;
    «рядом никого» — измерения той же группы (тот же день, ближе 30 км, общая сцена) исключены.
    """
    fm = load(profile)
    cm = load_model(profile)
    serve = cm["serve"]["type"]
    pred = pd.read_csv(CONC_EVAL / f"{profile}_predictions.csv").drop(columns=["lon", "lat"], errors="ignore")
    if ev is None:
        ev = event_features(load_events(profile, "train")).merge(
            pred[["event_id", "part", "group_id", f"pred_{serve}"]], on="event_id")
    hold = ev[ev["part"] == "holdout"].reset_index(drop=True)
    groups = dict(zip(ev["event_id"], ev["group_id"]))
    q = cm["interval_log_quantiles"]
    rows = []
    for _, r in hold.iterrows():
        y, p = float(r["conc_items_km2"]), float(r[f"pred_{serve}"])
        t = covariates._parse(r["t_ref"]).isoformat()
        row = {"event_id": r["event_id"], "group_id": r["group_id"], "y_true": y, "pred_model": p}
        lo, hi = apply_interval(np.array([p]), q["0.8"])
        row.update(model_lo80=float(lo[0]), model_hi80=float(hi[0]))
        same_group = {e for e, g in groups.items() if g == r["group_id"]}
        for name, excl in (("nearby", {r["event_id"]}), ("isolated", same_group)):
            f = fuse(profile, float(r["lon"]), float(r["lat"]), t, p, exclude=excl)
            row.update({f"pred_{name}": f["fused"]["value"], f"{name}_lo80": f["fused"]["lo80"],
                        f"{name}_hi80": f["fused"]["hi80"], f"{name}_field_weight": f["field_weight"]})
        rows.append(row)
    df = pd.DataFrame(rows)
    EVAL.mkdir(parents=True, exist_ok=True)
    df.to_csv(EVAL / f"{profile}_predictions.csv", index=False, encoding="utf-8")

    def cover(lo, hi):
        return float(np.mean((df["y_true"] >= df[lo]) & (df["y_true"] <= df[hi])))

    def width(lo, hi):
        return float(np.median(np.log(df[hi] + EPS) - np.log(df[lo] + EPS)))

    def mae_log(col):
        return float(np.mean(np.abs(np.log(df["y_true"] + EPS) - np.log(df[col] + EPS))))

    res = {"profile": profile, "n_holdout": len(df), "method": fm["method"], "variogram": fm["variogram"],
           "v_km_day": fm["v_km_day"],
           "model": {"mae": mae(df["y_true"], df["pred_model"]), "rmse": rmse(df["y_true"], df["pred_model"]),
                     "mae_log": mae_log("pred_model"),
                     "cover80": cover("model_lo80", "model_hi80"), "log_width80": width("model_lo80", "model_hi80")},
           "scenarios": {}}
    for name, text in (("nearby", "доступны все прочие измерения, включая соседей того же рейса"),
                       ("isolated", "измерения той же группы исключены — свежих соседей нет")):
        res["scenarios"][name] = {
            "description": text, "mae": mae(df["y_true"], df[f"pred_{name}"]),
            "rmse": rmse(df["y_true"], df[f"pred_{name}"]), "mae_log": mae_log(f"pred_{name}"),
            "cover80": cover(f"{name}_lo80", f"{name}_hi80"),
            "log_width80": width(f"{name}_lo80", f"{name}_hi80"),
            "mean_field_weight": float(df[f"{name}_field_weight"].mean())}
    res["meta"] = run_meta("pairs.yaml", "concentration.yaml")
    write_json(EVAL / f"{profile}_metrics.json", res)
    return res


def explain(profile: str, lon: float, lat: float, t: str) -> None:
    from . import concentration

    r = concentration.predict(profile, [lon], [lat], [t])
    v = float(r["value"][0])
    f = fuse(profile, lon, lat, t, None if np.isnan(v) else v)
    print(f"Профиль {profile}, точка {lon}, {lat}, момент {t}")
    if f is None:
        print("  концентрация недоступна — сводить не с чем")
        return
    print(f"  модель: {f['model']['value']} шт./км² (доверие {f['model']['weight']:.0%})")
    for e in f["evidence"]:
        print(f"  {e['event_id']}: {e['conc_items_km2']} шт./км², {e['distance_km']} км, {e['age_days']} сут. "
              f"→ вес {e['weight']:.2f}{' (то же пятно с учётом дрейфа)' if e['same_patch'] else ''}")
    x = f["fused"]
    print(f"  сведённая оценка: {x['value']} шт./км²; 80%: {x['lo80']}–{x['hi80']}; "
          f"дисперсия снижена на {f['variance_reduction']:.0%}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fit"
    if cmd == "fit":
        for p in sys.argv[2:] or ["A", "B"]:
            d = fit(p)
            vg, e = d["variogram"], d["evaluation"]
            print(f"\n=== Профиль {p}: s={vg['psill']:.3f}, τ²={vg['nugget']:.3f}, ρ={vg['range_km']:.0f} км, "
                  f"v={d['v_km_day']:.0f} км/сут; измерений {len(d['evidence'])}")
            print(f"  CV на обучающей части: ошибка ln(C+1)² {vg['loo_mse_no_neighbours']:.3f} → {vg['loo_mse']:.3f}")
            print(f"  отложенная ({e['n_holdout']}): модель MAE {e['model']['mae']:.1f} (лог {e['model']['mae_log']:.2f}), "
                  f"покрытие 80% {e['model']['cover80']:.0%}")
            for k, s in e["scenarios"].items():
                print(f"  {k:8s} MAE {s['mae']:.1f} (лог {s['mae_log']:.2f}), покрытие 80% {s['cover80']:.0%}, "
                      f"вес измерений {s['mean_field_weight']:.2f}")
    elif cmd == "explain":
        explain(sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), sys.argv[5])
    else:
        raise SystemExit(f"неизвестная команда {cmd}")
