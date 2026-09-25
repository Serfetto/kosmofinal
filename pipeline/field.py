"""Полевой реестр: проверка схемы, время, геометрия событий, отбор по профилям концентрации.

python -m pipeline.field prepare              # → data/field/{selection.csv, events.csv, events.geojson, objects.geojson}
python -m pipeline.field explain S2:MSM41_litter-T4

Правила отбора — configs/profiles.yaml. Пустое поле CSV — «неизвестно», а не ноль: числа читаются
как NaN и нигде не заменяются нулями.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
from shapely.geometry import LineString, MultiLineString, Point, mapping

from .config import DATA, ROOT
from .measure import field_concentration, poisson_ci
from .provenance import load_yaml, run_meta, write_json

FIELD = DATA / "field"
CONFIG = "profiles.yaml"
NA_TOLERANCE = 0.01  # допустимое расхождение опубликованной плотности и N/A (округление источника S3)

NUMERIC = [
    "latitude", "longitude", "lat_start", "lon_start", "lat_end", "lon_end", "items_count",
    "concentration_value_orig", "concentration_items_km2", "concentration_g_km2", "transect_length_km",
    "transect_width_m", "sampled_area_km2", "sea_state_beaufort", "wind_speed_kn",
    "reported_concentration_items_km2", "reported_concentration_g_km2", "parent_concentration_items_km2",
    "density_numerator_items", "source_reported_total_items", "source_object_filtered_items",
]


def config() -> dict:
    return load_yaml(CONFIG)


def load_raw(cfg: dict | None = None) -> pd.DataFrame:
    """CSV как есть + проверка контракта из README (строки, поля, события, уникальный sample_id)."""
    cfg = cfg or config()
    df = pd.read_csv(ROOT / cfg["source_csv"], dtype=str, keep_default_na=False, encoding="utf-8")
    exp = cfg["expected"]
    problems = []
    if len(df) != exp["rows"]:
        problems.append(f"строк {len(df)}, ожидалось {exp['rows']}")
    if df.shape[1] != exp["columns"]:
        problems.append(f"полей {df.shape[1]}, ожидалось {exp['columns']}")
    if df["sample_id"].duplicated().any():
        problems.append("sample_id не уникален")
    if df["event_id"].nunique() != exp["events"]:
        problems.append(f"событий {df['event_id'].nunique()}, ожидалось {exp['events']}")
    if problems:
        raise ValueError("CSV не соответствует описанию: " + "; ".join(problems))
    for c in NUMERIC:
        df[c] = pd.to_numeric(df[c].replace("", np.nan), errors="raise")
    return df


def event_interval(date: str, t0: str, t1: str) -> tuple[datetime, datetime, bool]:
    """Интервал наблюдения, UTC. Конец раньше начала — переход через полночь.

    Дата без времени — это не 00:00: интервал растягивается на сутки, time_known=False.
    """
    d = datetime.fromisoformat(date)
    if t0 and t1:
        s = datetime.fromisoformat(f"{date}T{t0}")
        e = datetime.fromisoformat(f"{date}T{t1}")
        if e < s:
            e += timedelta(days=1)
        return s, e, True
    return d, d + timedelta(days=1), False


def s2_segments(cfg: dict) -> dict[str, list[dict]]:
    """Сегменты прерванных трансект S2 из метаданных PANGAEA 931834 (скачиваются при отсутствии)."""
    path = ROOT / cfg["s2_transects_xlsx"]
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(cfg["s2_transects_url"], timeout=60)
        r.raise_for_status()
        path.write_bytes(r.content)
    x = pd.read_excel(path)
    x["no"] = pd.to_numeric(x["Transect no."], errors="coerce")
    seg = x[x["Start Lat (N)"].notna()]
    out = {}
    dates = {}
    for _, r in seg.iterrows():
        if isinstance(r["Date"], (pd.Timestamp, datetime)):
            dates[int(r["no"])] = pd.Timestamp(r["Date"]).strftime("%Y-%m-%d")
    for no, g in seg.groupby("no"):
        if len(g) < 2:
            continue
        date = dates[int(no)]
        out[f"S2:MSM41_litter-T{int(no)}"] = [
            {"start": (float(r["Start Long (W)"]), float(r["Start Lat (N)"])),
             "end": (float(r["End Long (W)"]), float(r["End Lat (N)"])),
             "t0": f"{date}T{r['Start Time']}", "t1": f"{date}T{r['End time']}",
             "length_km": float(r["Distance (km)"])} for _, r in g.iterrows()]
    return out


def s2_seaweed(cfg: dict) -> dict[str, float]:
    """Плотность комков саргассума на трансекте S2 — только контекст сложного фона, не предиктор."""
    x = pd.read_excel(ROOT / cfg["s2_transects_xlsx"])
    x["no"] = pd.to_numeric(x["Transect no."], errors="coerce")
    col = "Density of seaweeds clumps (clumps km-2)"
    one = x[x["Start Lat (N)"].notna()].groupby("no").filter(lambda g: len(g) == 1)
    return {f"S2:MSM41_litter-T{int(r['no'])}": float(r[col]) for _, r in one.iterrows() if pd.notna(r[col])}


def _match(row: pd.Series, sel: dict) -> bool:
    return all(row[k] in v for k, v in sel.items())


def select(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Решение по каждой строке: профиль и роль либо причина исключения."""
    bad_flags = set(cfg.get("exclude_flags_always", []))
    out = []
    for _, r in df.iterrows():
        flags = {f for f in r["quality_flags"].split(";") if f}
        rec = dict(sample_id=r["sample_id"], event_id=r["event_id"], source_id=r["source_id"],
                   record_type=r["record_type"], target_scope=r["target_scope"],
                   measurement_profile=r["measurement_profile"], decision="excluded", profile="", role="",
                   reason_code="", reason_text="")
        for pid, prof in cfg["profiles"].items():
            for g in prof["groups"]:
                if _match(r, g["select"]):
                    hit = flags & bad_flags
                    if hit:
                        rec.update(reason_code="excluded_flag", reason_text="флаг качества: " + ";".join(sorted(hit)))
                    else:
                        rec.update(decision="included", profile=pid, role=g["role"])
                    break
            if rec["profile"] or rec["reason_code"]:
                break
        if rec["decision"] == "excluded" and not rec["reason_code"]:
            for rule in cfg["exclusion_rules"]:
                if _match(r, rule["select"]):
                    rec.update(reason_code=rule["code"], reason_text=rule["text"])
                    break
            else:
                rec.update(reason_code="not_target_profile", reason_text="не соответствует ни одному профилю")
        out.append(rec)
    return pd.DataFrame(out)


def build_events(df: pd.DataFrame, sel: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, list]:
    """Одна строка на включённое событие профиля: геометрия, время, N, A, C и интервал измерения."""
    segs = s2_segments(cfg)
    weed = s2_seaweed(cfg)
    inc = sel[sel["decision"] == "included"].merge(df, on="sample_id", suffixes=("", "_raw"))
    dup = inc.duplicated(["profile", "event_id"])
    if dup.any():
        raise ValueError(f"в профиле несколько строк на событие: {inc.loc[dup, 'event_id'].tolist()[:5]}")
    rows, feats = [], []
    for _, r in inc.iterrows():
        t0, t1, known = event_interval(r["date_utc"], r["time_start_utc"], r["time_end_utc"])
        eid = r["event_id"]
        radius_km = 0.0
        if eid in segs:
            geom = MultiLineString([[s["start"], s["end"]] for s in segs[eid]])
            gtype = "segments"
        elif r["position_role"] == "mosaic_center":
            geom = Point(r["longitude"], r["latitude"])
            gtype = "mosaic"
            radius_km = float(np.sqrt(r["sampled_area_km2"] / np.pi)) if pd.notna(r["sampled_area_km2"]) else 2.0
        elif pd.notna(r["lat_start"]) and pd.notna(r["lat_end"]):
            geom = LineString([(r["lon_start"], r["lat_start"]), (r["lon_end"], r["lat_end"])])
            gtype = "strip"
        else:
            geom = Point(r["longitude"], r["latitude"])
            gtype = "point"
            radius_km = float(cfg["geometry"]["point_radius_km"])

        n, a, c_pub = r["density_numerator_items"], r["sampled_area_km2"], r["concentration_items_km2"]
        if pd.notna(n) and pd.notna(a):
            c = field_concentration(n, a)
            lo, hi = poisson_ci(n, a)
            if pd.notna(c_pub) and c_pub > 0 and abs(c - c_pub) / c_pub > NA_TOLERANCE:
                raise ValueError(f"{eid}: N/A = {c:.3f} расходится с опубликованной {c_pub:.3f}")
            c_source = "n_over_a"
        else:
            c, lo, hi, c_source = c_pub, np.nan, np.nan, "published"
        if pd.isna(c):
            raise ValueError(f"{eid}: нет концентрации у включённой строки")
        rec = {
            "event_id": eid, "profile": r["profile"], "role": r["role"], "sample_id": r["sample_id"],
            "source_id": r["source_id"], "source_event_id": r["source_event_id"], "sea_area": r["sea_area"],
            "date_utc": r["date_utc"], "t_start_utc": t0.isoformat(), "t_end_utc": t1.isoformat(),
            "time_known": known, "lon": r["longitude"], "lat": r["latitude"], "position_role": r["position_role"],
            "geometry_type": gtype, "footprint_radius_km": radius_km, "geometry_wkt": geom.wkt,
            "length_km": r["transect_length_km"], "width_m": r["transect_width_m"], "area_km2": a,
            "n_items": n, "conc_items_km2": c, "conc_lo95": lo, "conc_hi95": hi, "conc_source": c_source,
            "conc_published": c_pub, "conc_g_km2": r["concentration_g_km2"],
            "target_scope": r["target_scope"], "measurement_profile": r["measurement_profile"],
            "material": r["material"], "size_class": r["size_class"], "sampling_method": r["sampling_method"],
            "litter_category": r["litter_category"], "quality_flags": r["quality_flags"],
            "calculation_method": r["calculation_method"], "source_doi": r["source_doi"],
            "source_license": r["source_license"], "provenance": r["provenance"],
            "ctx_seaweed_clumps_km2": weed.get(eid, np.nan),
            "value_type": "measurement",
        }
        rows.append(rec)
        props = {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in rec.items() if k != "geometry_wkt"}
        feats.append({"type": "Feature", "geometry": mapping(geom), "properties": props})
    ev = pd.DataFrame(rows).sort_values(["profile", "event_id"]).reset_index(drop=True)
    return ev, feats


def build_objects(df: pd.DataFrame) -> list:
    """Объектные записи — контекстный слой «отдельные предметы» (не метки концентрации)."""
    obj = df[df["record_type"] == "item_observation"]
    return [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["longitude"], r["latitude"]]},
             "properties": {"sample_id": r["sample_id"], "event_id": r["event_id"], "source_id": r["source_id"],
                            "date_utc": r["date_utc"], "item_type": r["litter_item_type"],
                            "category": r["litter_category"], "material": r["material"],
                            "position_role": r["position_role"], "value_type": "object_context"}}
            for _, r in obj.iterrows()]


def prepare() -> dict:
    cfg = config()
    df = load_raw(cfg)
    sel = select(df, cfg)
    ev, feats = build_events(df, sel, cfg)
    FIELD.mkdir(parents=True, exist_ok=True)
    sel.to_csv(FIELD / "selection.csv", index=False, encoding="utf-8")
    ev.to_csv(FIELD / "events.csv", index=False, encoding="utf-8")
    (FIELD / "events.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False), encoding="utf-8")
    (FIELD / "objects.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": build_objects(df)}, ensure_ascii=False), encoding="utf-8")
    summary = {
        "rows": len(df), "events": int(df["event_id"].nunique()),
        "included_rows": int((sel["decision"] == "included").sum()),
        "by_profile": {f"{p}/{r}": int(n) for (p, r), n in
                       sel[sel["decision"] == "included"].groupby(["profile", "role"]).size().items()},
        "excluded_by_reason": {k: int(v) for k, v in sel[sel["decision"] == "excluded"]["reason_code"].value_counts().items()},
        "geometry_types": {k: int(v) for k, v in ev["geometry_type"].value_counts().items()},
        "time_known": {k: int(v) for k, v in ev.groupby("profile")["time_known"].sum().items()},
        "meta": run_meta(CONFIG),
    }
    write_json(FIELD / "summary.json", summary)
    return summary


def load_events(profile: str | None = None, role: str | None = None) -> pd.DataFrame:
    ev = pd.read_csv(FIELD / "events.csv", dtype={"source_event_id": str, "sample_id": str})
    if profile:
        ev = ev[ev["profile"] == profile]
    if role:
        ev = ev[ev["role"] == role]
    return ev.reset_index(drop=True)


def explain(event_id: str) -> None:
    """Расшифровка расчёта по событию — для перепроверки экспертом без изменения кода."""
    sel = pd.read_csv(FIELD / "selection.csv")
    ev = pd.read_csv(FIELD / "events.csv")
    rows = sel[sel["event_id"] == event_id]
    if rows.empty:
        print(f"событие {event_id} не найдено")
        return
    print(f"Событие {event_id}: строк в реестре {len(rows)}")
    for _, r in rows.iterrows():
        tag = f"профиль {r['profile']} ({r['role']})" if r["decision"] == "included" else f"исключена: {r['reason_code']}"
        print(f"  {r['sample_id']:9s} {r['target_scope']:26s} {r['measurement_profile']:18s} → {tag}")
    for _, e in ev[ev["event_id"] == event_id].iterrows():
        print(f"\nПрофиль {e['profile']}: {e['material']}, {e['size_class']}, {e['sampling_method']}")
        print(f"  интервал наблюдения, UTC: {e['t_start_utc']} … {e['t_end_utc']} (время известно: {e['time_known']})")
        print(f"  геометрия: {e['geometry_type']}; длина {e['length_km']} км, ширина {e['width_m']} м")
        if e["conc_source"] == "n_over_a":
            print(f"  C = N / A = {e['n_items']:g} / {e['area_km2']:g} км² = {e['conc_items_km2']:.2f} шт./км²")
            print(f"  95% ДИ (Гарвуд): {e['conc_lo95']:.1f} … {e['conc_hi95']:.1f} шт./км²")
            print(f"  опубликовано: {e['conc_published']:g} шт./км²")
        else:
            print(f"  C = {e['conc_items_km2']:g} шт./км² (опубликованная оценка; N/A проверить нельзя)")
        print(f"  флаги: {e['quality_flags'] or '—'}")
        print(f"  метод источника: {e['calculation_method']}")
        print(f"  источник: {e['source_doi']} ({e['source_license']}); {e['provenance']}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "prepare"
    if cmd == "prepare":
        s = prepare()
        print(json.dumps({k: v for k, v in s.items() if k != "meta"}, ensure_ascii=False, indent=1))
    elif cmd == "explain":
        explain(sys.argv[2])
    else:
        raise SystemExit(f"неизвестная команда {cmd}")
