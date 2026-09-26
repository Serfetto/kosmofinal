"""Концентрация плавающего мусора, шт./км², по полевым данным: базовые алгоритмы, основная модель,
интервалы, область применимости.

python -m pipeline.concentration evaluate [A B]   # CV, отложенная выборка, перенос → data/eval/concentration/
python -m pipeline.concentration explain B 39.72 43.57 2025-07-18T08:10

Спутниковое значение концентрации — модельная оценка. Площадь маски и спектральные индексы
в концентрацию не переводятся (см. docs/report.md).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime

import numpy as np
import pandas as pd

from . import covariates
from .config import DATA, MODELS
from .field import load_events
from .measure import group_bootstrap_mae, mae, mae_log, medae, rmse
from .provenance import load_yaml, run_meta, write_json
from .splits import haversine_km
from .splits import load as load_split

CONFIG = "concentration.yaml"
EVAL = DATA / "eval" / "concentration"
EPS = 1.0  # шт./км², сдвиг для логарифма


# ---------- признаки ----------

def event_features(ev: pd.DataFrame) -> pd.DataFrame:
    t = [covariates.event_reference_time(a, b, k) for a, b, k in zip(ev["t_start_utc"], ev["t_end_utc"], ev["time_known"])]
    f = covariates.build(ev["lon"], ev["lat"], t)
    out = ev.copy()
    for k, v in f.items():
        out[k] = v
    out["t_ref"] = [x.isoformat() for x in t]
    out["month"] = [x.month for x in t]
    return out


def check_features(features: list[str]) -> None:
    bad = set(features) & set(load_yaml(CONFIG)["forbidden_predictors"])
    if bad:
        raise ValueError(f"запрещённые предикторы (производные от ответа или недоступные при применении): {sorted(bad)}")


# ---------- модели ----------

class Median:
    name = "median"

    def fit(self, d: pd.DataFrame):
        self.c = float(np.median(d["conc_items_km2"]))
        return self

    def predict(self, d: pd.DataFrame) -> np.ndarray:
        return np.full(len(d), self.c)

    def to_json(self):
        return {"type": self.name, "value": self.c}


class IDW:
    """Простой пространственный прогноз: взвешенное по обратному расстоянию среднее k ближайших событий."""
    name = "idw"

    def __init__(self, power: float = 2, k: int = 8):
        self.power, self.k = power, k

    def fit(self, d: pd.DataFrame):
        self.lon, self.lat = d["lon"].to_numpy(float), d["lat"].to_numpy(float)
        self.c = d["conc_items_km2"].to_numpy(float)
        return self

    def predict(self, d: pd.DataFrame) -> np.ndarray:
        dist = haversine_km(d["lon"].to_numpy(float)[:, None], d["lat"].to_numpy(float)[:, None],
                            self.lon[None], self.lat[None])
        dist = np.maximum(dist, 1.0)
        k = min(self.k, dist.shape[1])
        idx = np.argsort(dist, 1)[:, :k]
        w = 1 / np.take_along_axis(dist, idx, 1) ** self.power
        return (w * self.c[idx]).sum(1) / w.sum(1)

    def to_json(self):
        return {"type": self.name, "power": self.power, "k": self.k,
                "points": np.c_[self.lon, self.lat, self.c].round(5).tolist()}


class _Scaled:
    def _x(self, d: pd.DataFrame, fit: bool = False) -> np.ndarray:
        x = d[self.features].to_numpy(float)
        if fit:
            self.mu, self.sd = x.mean(0), x.std(0)
            self.sd[self.sd == 0] = 1.0
        return np.c_[np.ones(len(x)), (x - self.mu) / self.sd]


class NBGLM(_Scaled):
    """Отрицательно-биномиальная регрессия числа предметов со смещением log(площади): C = exp(Xβ)."""
    name = "nb_glm"

    def __init__(self, features: list[str]):
        check_features(features)
        self.features = features

    def fit(self, d: pd.DataFrame):
        import statsmodels.api as sm

        X = self._x(d, fit=True)
        m = sm.NegativeBinomial(d["n_items"].to_numpy(float), X, offset=np.log(d["area_km2"].to_numpy(float)))
        r = m.fit(disp=0, maxiter=500)
        self.beta = np.asarray(r.params[:-1])
        self.alpha = float(r.params[-1])
        self.bse = np.asarray(r.bse[:-1])
        return self

    def predict(self, d: pd.DataFrame) -> np.ndarray:
        return np.exp(self._x(d) @ self.beta)

    def to_json(self):
        return {"type": self.name, "features": self.features, "mu": self.mu.tolist(), "sd": self.sd.tolist(),
                "beta": self.beta.tolist(), "beta_se": self.bse.tolist(), "alpha": self.alpha,
                "coef_names": ["intercept"] + self.features}


class LogLinearRidge(_Scaled):
    """Регрессия log(C + 1) с L2-регуляризацией (когда числитель и площадь неизвестны)."""
    name = "loglinear_ridge"

    def __init__(self, features: list[str], alpha: float = 1.0):
        check_features(features)
        self.features, self.alpha = features, alpha

    def fit(self, d: pd.DataFrame):
        X = self._x(d, fit=True)
        y = np.log(d["conc_items_km2"].to_numpy(float) + EPS)
        reg = self.alpha * np.eye(X.shape[1])
        reg[0, 0] = 0
        self.beta = np.linalg.solve(X.T @ X + reg, X.T @ y)
        return self

    def predict(self, d: pd.DataFrame) -> np.ndarray:
        return np.maximum(np.exp(self._x(d) @ self.beta) - EPS, 0)

    def to_json(self):
        return {"type": self.name, "features": self.features, "mu": self.mu.tolist(), "sd": self.sd.tolist(),
                "beta": self.beta.tolist(), "ridge_alpha": self.alpha, "coef_names": ["intercept"] + self.features}


def make(name: str, pcfg: dict, cfg: dict):
    if name == "median":
        return Median()
    if name == "idw":
        return IDW(**cfg["idw"])
    if name == "nb_glm":
        return NBGLM(pcfg["features"])
    if name == "loglinear_ridge":
        return LogLinearRidge(pcfg["features"], pcfg.get("ridge_alpha", 1.0))
    raise ValueError(name)


def from_json(j: dict):
    t = j["type"]
    if t == "median":
        m = Median()
        m.c = j["value"]
    elif t == "idw":
        m = IDW(j["power"], j["k"])
        pts = np.asarray(j["points"], float)
        m.lon, m.lat, m.c = pts[:, 0], pts[:, 1], pts[:, 2]
    else:
        m = NBGLM(j["features"]) if t == "nb_glm" else LogLinearRidge(j["features"], j.get("ridge_alpha", 1.0))
        m.mu, m.sd, m.beta = np.asarray(j["mu"]), np.asarray(j["sd"]), np.asarray(j["beta"])
    return m


# ---------- оценка ----------

def metrics(y, p) -> dict:
    return {"mae": mae(y, p), "rmse": rmse(y, p), "medae": medae(y, p), "mae_log": mae_log(y, p, EPS),
            "n": int(len(y)), "bias_median_ratio": float(np.median(p) / max(np.median(y), 1e-9))}


def conformal(y, p, levels) -> dict:
    """Сплит-конформные границы в лог-шкале по внефолдовым остаткам."""
    r = np.log(np.asarray(y) + EPS) - np.log(np.asarray(p) + EPS)
    n = len(r)
    out = {}
    for L in levels:
        a = 1 - L
        lo = np.quantile(r, max(a / 2 * (n + 1) / n - 1 / n, 0), method="lower")
        hi = np.quantile(r, min((1 - a / 2) * (n + 1) / n, 1), method="higher")
        out[str(L)] = [float(lo), float(hi)]
    return out


def apply_interval(p: np.ndarray, q: list[float]) -> tuple[np.ndarray, np.ndarray]:
    return np.maximum((p + EPS) * np.exp(q[0]) - EPS, 0), (p + EPS) * np.exp(q[1]) - EPS


def poisson_floor_mae(d: pd.DataFrame, n_sim: int = 4000) -> float | None:
    """MAE идеальной модели, знающей истинную интенсивность: остаётся только счётный шум."""
    if d["n_items"].isna().any():
        return None
    lam, a = d["n_items"].to_numpy(float), d["area_km2"].to_numpy(float)
    sim = np.random.default_rng(0).poisson(lam[None].repeat(n_sim, 0))
    return float(np.mean(np.abs(sim - lam) / a))


def evaluate(profile: str) -> dict:
    cfg = load_yaml(CONFIG)
    pcfg = cfg["profiles"][profile]
    ev = event_features(load_events(profile, "train"))
    sp = load_split(profile)[["event_id", "group_id", "fold", "is_holdout"]]
    d = ev.merge(sp, on="event_id", how="inner")
    if len(d) != len(ev):
        raise ValueError("разбиение не совпадает с событиями профиля — пересоберите splits")
    main = pcfg["main"]
    names = [main] + pcfg["baselines"]
    dev, hold = d[~d["is_holdout"]].reset_index(drop=True), d[d["is_holdout"]].reset_index(drop=True)
    y_dev, y_hold = dev["conc_items_km2"].to_numpy(float), hold["conc_items_km2"].to_numpy(float)

    def cv_predict(name: str, pc: dict, frame: pd.DataFrame = dev) -> np.ndarray:
        oof = np.full(len(frame), np.nan)
        for f in sorted(frame["fold"].unique()):
            tr, te = frame["fold"] != f, frame["fold"] == f
            oof[te.to_numpy()] = make(name, pc, cfg).fit(frame[tr]).predict(frame[te])
        return oof

    def select(frame: pd.DataFrame) -> list[dict]:
        return [{"features": feats, "cv_mae": mae(frame["conc_items_km2"],
                                                  cv_predict(main, {**pcfg, "features": feats}, frame))}
                for feats in pcfg["candidate_features"]]

    # Выбор признаков основной модели — только по групповой CV
    selection = select(dev)
    best = min(selection, key=lambda s: s["cv_mae"])
    pcfg = {**pcfg, "features": best["features"]}

    # Внефолдовые предсказания на обучающей части (выбор признаков, конформные остатки)
    oof = {m: cv_predict(m, pcfg) for m in names}
    cv = {m: metrics(y_dev, oof[m]) for m in names}

    # Вложенная CV: признаки выбираются заново внутри каждого внешнего фолда, без его событий. CV, по которой
    # выбирали признаки, оптимистична; вложенная оценивает всю процедуру. У базовых выбора нет — их CV уже честная.
    oof_nested, nested_selection = np.full(len(dev), np.nan), []
    for f in sorted(dev["fold"].unique()):
        te = (dev["fold"] == f).to_numpy()
        fs = min(select(dev[~te].reset_index(drop=True)), key=lambda s: s["cv_mae"])["features"]
        oof_nested[te] = make(main, {**pcfg, "features": fs}, cfg).fit(dev[~te]).predict(dev[te])
        nested_selection.append({"fold": int(f), "features": fs})
    nested = {main: oof_nested, **{m: oof[m] for m in pcfg["baselines"]}}
    cv_nested = {m: metrics(y_dev, p) for m, p in nested.items()}
    boot = {"n_boot": 2000, "seed": int(cfg["seed"])}
    uncertainty = {"cv_nested": group_bootstrap_mae(y_dev, nested, dev["group_id"], main, **boot)}

    # Модель сервиса — основная, пока базовый алгоритм не точнее её значимо на вложенной CV (95% ДИ разности
    # MAE выше нуля). Разница в пределах шума — не повод менять модель с признаками на константу или IDW.
    better = [m for m, v in uncertainty["cv_nested"]["delta"].items() if v["ci"][0] > 0]
    serve = min(better, key=lambda m: cv_nested[m]["mae"]) if better else main

    # Модели на всей обучающей части → отложенная выборка (итоговые числа)
    fitted = {m: make(m, pcfg, cfg).fit(dev) for m in names}
    hp = {m: fitted[m].predict(hold) for m in names}
    ho = {m: metrics(y_hold, hp[m]) for m in names}
    uncertainty["holdout"] = group_bootstrap_mae(y_hold, hp, hold["group_id"], main, **boot)
    q = conformal(dev["conc_items_km2"], oof[serve], cfg["interval_levels"])
    cover = {}
    for L, qq in q.items():
        lo, hi = apply_interval(hp[serve], qq)
        y = hold["conc_items_km2"].to_numpy()
        cover[L] = float(np.mean((y >= lo) & (y <= hi)))

    res = {"profile": profile, "main": main, "baselines": pcfg["baselines"], "features": pcfg["features"],
           "serve_model": serve, "selected_by": "признаки — минимум MAE на групповой CV (без отложенной выборки)",
           "serve_rule": "основная модель, если ни один базовый алгоритм не точнее её значимо на вложенной CV "
                         "(95% ДИ разности MAE по групповому бутстрепу выше нуля)",
           "feature_selection": selection, "nested_selection": nested_selection,
           "n_dev": len(dev), "n_holdout": len(hold), "n_groups": int(d["group_id"].nunique()),
           "n_groups_dev": int(dev["group_id"].nunique()), "n_groups_holdout": int(hold["group_id"].nunique()),
           "cv": cv, "cv_nested": cv_nested, "holdout": ho, "uncertainty": uncertainty,
           "interval_log_quantiles": q, "holdout_interval_coverage": cover,
           "poisson_floor_mae_dev": poisson_floor_mae(dev), "poisson_floor_mae_holdout": poisson_floor_mae(hold)}

    # Предсказания и эталоны
    EVAL.mkdir(parents=True, exist_ok=True)

    # Перенос на другой регион / порог размера (профиль B: обучение S4 → проверка S3)
    tc = load_events(profile, "transfer_check")
    if len(tc):
        tc = event_features(tc)
        y_tc, tp = tc["conc_items_km2"].to_numpy(float), {m: fitted[m].predict(tc) for m in names}
        tg = tc["source_id"] + ":" + tc["date_utc"]  # группа — день рейса, как в разбиении
        res["transfer_check"] = {m: metrics(y_tc, tp[m]) for m in names}
        res["transfer_check"]["n_events"] = len(tc)
        res["transfer_check"]["target_median_train"] = float(dev["conc_items_km2"].median())
        res["transfer_check"]["target_median_check"] = float(tc["conc_items_km2"].median())
        uncertainty["transfer_check"] = group_bootstrap_mae(y_tc, tp, tg, main, **boot)
        pd.DataFrame({"event_id": tc["event_id"], "group_id": tg, "part": "transfer_check", "y_true": y_tc,
                      "y_true_lo95": tc["conc_lo95"], "y_true_hi95": tc["conc_hi95"],
                      **{f"pred_{m}": np.round(tp[m], 3) for m in names},
                      **{k: tc[k] for k in pcfg["features"]}}).to_csv(
            EVAL / f"{profile}_transfer_predictions.csv", index=False, encoding="utf-8")

    rows = []
    for part, frame, preds in (("cv", dev, oof), ("holdout", hold, hp)):
        lo80, hi80 = apply_interval(preds[serve], q["0.8"])
        lo95, hi95 = apply_interval(preds[serve], q["0.95"])
        for i, r in frame.iterrows():
            rows.append({"event_id": r["event_id"], "group_id": r["group_id"], "fold": r["fold"], "part": part,
                         "y_true": r["conc_items_km2"], "y_true_lo95": r["conc_lo95"], "y_true_hi95": r["conc_hi95"],
                         **{f"pred_{m}": round(float(preds[m][i]), 3) for m in names},
                         f"pred_{main}_nested": round(float(oof_nested[i]), 3) if part == "cv" else np.nan,
                         "serve_lo80": lo80[i], "serve_hi80": hi80[i], "serve_lo95": lo95[i], "serve_hi95": hi95[i],
                         **{k: r[k] for k in pcfg["features"]}})
    pd.DataFrame(rows).to_csv(EVAL / f"{profile}_predictions.csv", index=False, encoding="utf-8")

    # Модель сервиса: обучена на обучающей части — та же, что дала числа на отложенной выборке
    train = dev
    model = {
        "profile": profile, "version": f"conc-{profile}-{run_meta(CONFIG)['configs'][CONFIG]}",
        "serve": fitted[serve].to_json(), "models": {m: fitted[m].to_json() for m in names},
        "interval_log_quantiles": q, "eps": EPS,
        "applicability": {
            "basins": load_yaml("profiles.yaml")["profiles"][profile]["domain"]["basins"],
            "train_points": train[["lon", "lat"]].round(5).to_numpy().tolist(),
            "train_months": sorted({int(m) for m in train["month"]}),
            "feature_ranges": {k: [float(train[k].min()), float(train[k].max())]
                               for k in pcfg["features"] if k not in ("lon", "lat")},
            **cfg["applicability"],
        },
        "trained_on": sorted(train["event_id"]), "holdout": sorted(hold["event_id"]),
        "meta": run_meta(CONFIG, "profiles.yaml"),
    }
    write_json(MODELS / f"conc_{profile}.json", model)
    res["meta"] = model["meta"]
    write_json(EVAL / f"{profile}_metrics.json", res)
    return res


# ---------- применение ----------

_models: dict = {}


def load_model(profile: str) -> dict:
    if profile not in _models:
        _models[profile] = json.loads((MODELS / f"conc_{profile}.json").read_text(encoding="utf-8"))
    return _models[profile]


def predict(profile: str, lon, lat, t_ref, features: dict | None = None) -> dict:
    """Оценка концентрации в точках на моменты t_ref: значение, интервалы, статус и причины."""
    j = load_model(profile)
    basins = load_yaml("profiles.yaml")["basins"]
    lon = np.atleast_1d(np.asarray(lon, float))
    lat = np.atleast_1d(np.asarray(lat, float))
    ap = j["applicability"]
    in_basin = np.zeros(len(lon), bool)
    for b in ap["basins"]:
        x0, y0, x1, y1 = basins[b]
        in_basin |= (lon >= x0) & (lon <= x1) & (lat >= y0) & (lat <= y1)
    tp = np.asarray(ap["train_points"], float)
    dmin = haversine_km(lon[:, None], lat[:, None], tp[None, :, 0], tp[None, :, 1]).min(1)
    months = np.array([_month(t) for t in np.broadcast_to(np.asarray(t_ref, dtype=object), lon.shape)])

    # Вне бассейна профиль заведомо неприменим. Не запрашиваем для таких точек
    # сетевые ковариаты (ERA5/Open-Meteo): ответ должен быть 404, а не 503 из-за сети.
    if not in_basin.any():
        nan = np.full(len(lon), np.nan)
        return {"value": nan.copy(), "lo80": nan.copy(), "hi80": nan.copy(),
                "lo95": nan.copy(), "hi95": nan.copy(),
                "status": ["unavailable"] * len(lon),
                "reasons": [["вне бассейна профиля: " + ", ".join(ap["basins"])] for _ in lon],
                "nearest_field_km": dmin, "model_version": j["version"],
                "model_type": j["serve"]["type"], "features": {}}

    f = features or covariates.build(lon, lat, t_ref)
    d = pd.DataFrame({k: np.broadcast_to(v, lon.shape) for k, v in f.items()})
    m = from_json(j["serve"])
    val = m.predict(d)
    lo80, hi80 = apply_interval(val, j["interval_log_quantiles"]["0.8"])
    lo95, hi95 = apply_interval(val, j["interval_log_quantiles"]["0.95"])
    status, reasons = [], []
    for i in range(len(lon)):
        why = []
        if not in_basin[i]:
            status.append("unavailable")
            reasons.append(["вне бассейна профиля: " + ", ".join(ap["basins"])])
            continue
        if dmin[i] > ap["validated_radius_km"]:
            why.append(f"ближайшее полевое измерение в {dmin[i]:.0f} км (> {ap['validated_radius_km']} км)")
        if months[i] not in ap["train_months"]:
            why.append(f"месяц {months[i]} вне месяцев обучения {ap['train_months']}")
        for k, (a, b) in ap["feature_ranges"].items():
            span = (b - a) * ap["feature_margin"]
            v = float(d[k].iloc[i])
            if not (a - span <= v <= b + span):
                why.append(f"{k} = {v:.1f} вне диапазона обучения [{a:.1f}; {b:.1f}]")
        status.append("research_estimate" if why else "model_estimate")
        reasons.append(why)
    na = np.array([s == "unavailable" for s in status])
    for arr in (val, lo80, hi80, lo95, hi95):
        arr[na] = np.nan
    return {"value": val, "lo80": lo80, "hi80": hi80, "lo95": lo95, "hi95": hi95, "status": status,
            "reasons": reasons, "nearest_field_km": dmin, "model_version": j["version"],
            "model_type": j["serve"]["type"], "features": f}


def calc_steps(profile: str, features: dict, i: int = 0) -> list[dict]:
    """Расчёт оценки в точке по шагам: формула модели сервиса с подставленными числами — для карточки гекса.

    features — признаки из predict (массивы), i — индекс точки. Каждый шаг: {label, formula}.
    """
    j = load_model(profile)
    s = j["serve"]
    if s["type"] not in ("nb_glm", "loglinear_ridge"):
        return []
    f = {k: float(np.atleast_1d(v)[i]) for k, v in features.items()}
    fmt = lambda v, d=3: f"{v:.{d}f}".replace(".", ",").replace("-", "−")  # noqa: E731
    out, eta, terms = [], s["beta"][0], [fmt(s["beta"][0])]
    if "dist_coast_km" in f and "log_dist_coast_km" in s["features"]:
        out.append({"label": "Расстояние до берега", "formula": f"d = {fmt(f['dist_coast_km'], 1)} км"})
    for k, b, mu, sd in zip(s["features"], s["beta"][1:], s["mu"], s["sd"]):
        z = (f[k] - mu) / sd
        src = f"ln(1 + {fmt(f['dist_coast_km'], 1)})" if k == "log_dist_coast_km" else fmt(f[k], 4)
        out.append({"label": f"Признак {k}", "formula": f"z = ({src} − {fmt(mu)}) / {fmt(sd)} = {fmt(z)}"})
        eta += b * z
        terms.append(f"{'−' if b < 0 else '+'} {fmt(abs(b))}·{fmt(z) if z >= 0 else '(' + fmt(z) + ')'}")
    if s["type"] == "nb_glm":
        c = float(np.exp(eta))
        out.append({"label": "Линейный предиктор", "formula": f"ln Ĉ = {' '.join(terms)} = {fmt(eta)}"})
        out.append({"label": "Оценка", "formula": f"Ĉ = e^{fmt(eta)} = {fmt(c, 1)} шт./км²"})
    else:
        c = max(float(np.exp(eta)) - EPS, 0.0)
        out.append({"label": "Линейный предиктор", "formula": f"ln(Ĉ + 1) = {' '.join(terms)} = {fmt(eta)}"})
        out.append({"label": "Оценка", "formula": f"Ĉ = e^{fmt(eta)} − 1 = {fmt(c, 1)} шт./км²"})
    q = j["interval_log_quantiles"]["0.8"]
    lo, hi = apply_interval(np.array([c]), q)
    out.append({"label": "80%-интервал", "formula": f"[(Ĉ + 1)·e^({fmt(q[0], 2)}) − 1 ; (Ĉ + 1)·e^{fmt(q[1], 2)} − 1] "
                                                   f"= [{fmt(float(lo[0]), 0)} ; {fmt(float(hi[0]), 0)}] шт./км²"})
    return out


def _month(t) -> int:
    return covariates._parse(t).month if not isinstance(t, (int, np.integer)) else int(t)


def explain(profile: str, lon: float, lat: float, t: str) -> None:
    r = predict(profile, [lon], [lat], [t])
    p = load_yaml("profiles.yaml")["profiles"][profile]
    print(f"Профиль {profile}: {p['label']} ({p['size_class']}, {p['method']})")
    print(f"Точка {lon}, {lat}; момент {t}")
    for k, v in r["features"].items():
        print(f"  признак {k} = {float(np.atleast_1d(v)[0]):.3f}")
    print(f"Модель: {r['model_type']} ({r['model_version']})")
    print(f"  C = {r['value'][0]:.1f} шт./км²; 80%: {r['lo80'][0]:.1f}…{r['hi80'][0]:.1f}; 95%: {r['lo95'][0]:.1f}…{r['hi95'][0]:.1f}")
    print(f"  статус: {r['status'][0]}; ближайшее полевое измерение: {r['nearest_field_km'][0]:.0f} км")
    for w in r["reasons"][0]:
        print(f"  — {w}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "evaluate"
    if cmd == "evaluate":
        for p in sys.argv[2:] or ["A", "B"]:
            r = evaluate(p)
            print(f"\n=== Профиль {p}: обучение {r['n_dev']} ({r['n_groups_dev']} групп), "
                  f"отложено {r['n_holdout']} ({r['n_groups_holdout']} групп)")
            for part in ("cv", "cv_nested", "holdout"):
                ci = r["uncertainty"].get(part, {}).get("mae_ci", {})
                for m, v in r[part].items():
                    lo_hi = f" [{ci[m][0]:6.1f}; {ci[m][1]:6.1f}]" if m in ci else " " * 17
                    print(f"  {part:9s} {m:16s} MAE={v['mae']:7.1f}{lo_hi} RMSE={v['rmse']:7.1f} "
                          f"MedAE={v['medae']:7.1f} ln-ошибка={v['mae_log']:.2f}")
            for part in ("cv_nested", "holdout"):
                for m, v in r["uncertainty"][part]["delta"].items():
                    print(f"  {part:9s} ΔMAE {r['main']} − {m}: {v['value']:+.1f} [{v['ci'][0]:+.1f}; {v['ci'][1]:+.1f}]")
            print(f"  модель сервиса: {r['serve_model']}; покрытие интервалов на отложенной: {r['holdout_interval_coverage']}")
            print(f"  нижняя граница MAE из-за счётного шума: {r['poisson_floor_mae_dev']}")
            if "transfer_check" in r:
                print("  перенос:", {m: round(v['mae'], 1) for m, v in r["transfer_check"].items() if isinstance(v, dict)},
                      "медианы", r["transfer_check"]["target_median_train"], "→", r["transfer_check"]["target_median_check"])
    elif cmd == "explain":
        explain(sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), sys.argv[5])
    else:
        raise SystemExit(f"неизвестная команда {cmd}")
