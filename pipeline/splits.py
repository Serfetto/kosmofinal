"""Разбиение полевых событий с учётом зависимостей. Фиксируется до экспериментов.

python -m pipeline.splits build [A B]      # → data/splits/<profile>.csv

Группа — связная компонента графа, где события соединены, если:
- это один день одного рейса (соседние полосы, общий ветер и волнение);
- они ближе link_km и не дальше link_days по времени (пространственно-временное соседство);
- им сопоставлена одна и та же спутниковая сцена (по реестру пар, если он уже построен).
Строки одного события уже сведены в одну запись профиля (pipeline/field.py).
Отложенная выборка — доля групп, выбранная с фиксированным seed и стратификацией по уровню концентрации.
Внутри остальных групп — GroupKFold для выбора модели.
"""
from __future__ import annotations

import sys
from datetime import date

import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from .config import DATA
from .field import load_events
from .provenance import load_yaml, run_meta, write_json

SPLITS = DATA / "splits"
REGISTRY = DATA / "registry" / "pairs.csv"
CONFIG = "concentration.yaml"


def haversine_km(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def groups(ev: pd.DataFrame, link_km: float, link_days: float) -> tuple[np.ndarray, list[tuple[str, str, str]]]:
    n = len(ev)
    lon, lat = ev["lon"].to_numpy(), ev["lat"].to_numpy()
    day = np.array([date.fromisoformat(d).toordinal() for d in ev["date_utc"]])
    src = ev["source_id"].to_numpy()
    d = haversine_km(lon[:, None], lat[:, None], lon[None], lat[None])
    same_day = (day[:, None] == day[None]) & (src[:, None] == src[None])
    near = (d < link_km) & (np.abs(day[:, None] - day[None]) <= link_days)
    adj = same_day | near
    reasons = []
    ii, jj = np.nonzero(np.triu(adj, 1))
    for i, j in zip(ii, jj):
        reasons.append((ev["event_id"][i], ev["event_id"][j], "same_day" if same_day[i, j] else "near"))
    if REGISTRY.exists():
        reg = pd.read_csv(REGISTRY)
        reg = reg[reg["decision"].isin(["accepted", "context"]) & reg["scene_id"].notna()]
        idx = {e: k for k, e in enumerate(ev["event_id"])}
        for sid, g in reg.groupby("scene_id"):
            ids = sorted({idx[e] for e in g["event_id"] if e in idx})
            for a, b in zip(ids[:-1], ids[1:]):
                adj[a, b] = adj[b, a] = True
                reasons.append((ev["event_id"][a], ev["event_id"][b], f"scene:{sid}"))
    r, c = np.nonzero(adj)
    _, lab = connected_components(coo_matrix((np.ones(len(r)), (r, c)), shape=(n, n)), directed=False)
    return lab, reasons


def build(profile: str) -> pd.DataFrame:
    cfg = load_yaml(CONFIG)
    sp = cfg["split"]
    ev = load_events(profile, "train")
    lab, reasons = groups(ev, sp["link_km"], sp["link_days"])
    # Детерминированная нумерация групп: по дате и id первого события
    order = (ev.assign(g=lab).sort_values(["date_utc", "event_id"]).drop_duplicates("g")["g"].tolist())
    gid = {g: k for k, g in enumerate(order)}
    ev["group_id"] = [f"{profile}-g{gid[g]:02d}" for g in lab]

    # Отложенная выборка: группы, стратифицированные по медианной концентрации группы
    rng = np.random.default_rng(cfg["seed"])
    gstat = ev.groupby("group_id").agg(n=("event_id", "size"), c=("conc_items_km2", "median")).reset_index()
    n_hold = max(1, int(round(sp["holdout_frac"] * len(ev))))
    gstat["q"] = pd.qcut(gstat["c"].rank(method="first"), q=min(3, len(gstat)), labels=False)
    hold, taken = set(), 0
    cand = gstat.sample(frac=1.0, random_state=int(rng.integers(1 << 31)))
    # По очереди из каждого страта, пока не наберётся нужная доля событий; самые крупные группы не берём,
    # чтобы в обучении осталось разнообразие
    big = gstat["n"].max()
    while taken < n_hold:
        added = False
        for q in sorted(cand["q"].unique()):
            pool = cand[(cand["q"] == q) & ~cand["group_id"].isin(hold) & (cand["n"] < max(big, 2))]
            if pool.empty or taken >= n_hold:
                continue
            g = pool.iloc[0]
            hold.add(g["group_id"])
            taken += int(g["n"])
            added = True
        if not added:
            break
    ev["is_holdout"] = ev["group_id"].isin(hold)

    # GroupKFold на обучающей части: группы по очереди в фолды, сбалансировано по числу событий
    train_g = gstat[~gstat["group_id"].isin(hold)].sort_values(["n", "group_id"], ascending=[False, True])
    k = min(sp["cv_folds"], len(train_g))
    load = np.zeros(k)
    fold_of = {}
    for _, g in train_g.iterrows():
        f = int(np.argmin(load))
        fold_of[g["group_id"]] = f
        load[f] += g["n"]
    ev["fold"] = [(-1 if h else fold_of[g]) for g, h in zip(ev["group_id"], ev["is_holdout"])]

    SPLITS.mkdir(parents=True, exist_ok=True)
    cols = ["event_id", "sample_id", "source_id", "date_utc", "lon", "lat", "group_id", "fold", "is_holdout",
            "conc_items_km2"]
    out = ev[cols].sort_values(["is_holdout", "fold", "event_id"])
    out.to_csv(SPLITS / f"{profile}.csv", index=False, encoding="utf-8")
    pd.DataFrame(reasons, columns=["event_a", "event_b", "link"]).to_csv(SPLITS / f"{profile}_links.csv", index=False)
    write_json(SPLITS / f"{profile}_meta.json", {
        "profile": profile, "n_events": len(ev), "n_groups": int(ev["group_id"].nunique()),
        "n_holdout_events": int(ev["is_holdout"].sum()), "holdout_groups": sorted(hold),
        "folds": {int(f): int((ev["fold"] == f).sum()) for f in sorted(set(ev["fold"])) if f >= 0},
        "used_pairs_registry": REGISTRY.exists(), "meta": run_meta(CONFIG, "profiles.yaml"),
    })
    return out


def load(profile: str) -> pd.DataFrame:
    return pd.read_csv(SPLITS / f"{profile}.csv")


if __name__ == "__main__":
    args = sys.argv[2:] if len(sys.argv) > 1 and sys.argv[1] == "build" else sys.argv[1:]
    for p in args or ["A", "B"]:
        s = build(p)
        print(p, "событий", len(s), "групп", s["group_id"].nunique(), "в отложенной", int(s["is_holdout"].sum()),
              "фолды", s[s["fold"] >= 0].groupby("fold").size().to_dict())
