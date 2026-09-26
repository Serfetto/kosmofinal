"""PDF-отчёт по снимку: где на акватории больше всего мусора и где именно лежат крупнейшие зоны.

GET /api/report?aoi=&date=&profile= → report_<aoi>_<date>_<profile>.pdf

Страница 1 — обзорная карта сцены: гексы с детекциями по классам покрытия, зоны, районы скопления.
Страница 2 — фрагменты снимка по районам и таблица крупнейших зон с координатами и ориентирами (если зоны есть).
Страница 3 — графики: динамика по всем снимкам акватории, распределение концентрации по гексам с полевыми
измерениями, отбраковка похожих объектов (пена, блик, суда) и прогноз дрейфа на 72 ч.
Дальше — методика: целевая величина, методы и формулы с коэффициентами моделей, различение объектов, сведение
источников (из того же ответа, что панель «Методика», backend/methodology.py).
Строится из тех же файлов, что и карта (data/web, water.tif). Прогноз дрейфа — детерминированный ансамбль (seed);
без поля течений в кеше и без сети раздел дрейфа заменяется пояснением.
"""
from __future__ import annotations

import io
import textwrap
import threading
from datetime import datetime, timedelta
from functools import lru_cache

import matplotlib
import numpy as np
import rasterio
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from matplotlib import patheffects as pe
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.collections import PolyCollection
from matplotlib.colors import to_rgba
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, FancyBboxPatch, Rectangle
from PIL import Image
from pyproj import Transformer

from backend.case_api import _aoi_date, _profile, _read
from backend.methodology import methodology
from backend.schemas import AoiQuery, DateQuery, ProfileQuery, Tag, errors
from pipeline import status as ST
from pipeline.aggregate import WEB
from pipeline.config import AOIS, DATA, PROCESSED

router = APIRouter()

PAGE_W, PAGE_H, MARGIN = 8.27, 11.69, 0.55  # A4, дюймы
CONTENT_W = PAGE_W - 2 * MARGIN
INK, MUTED, TIDE, LINE, SOFT, WARN = "#10221f", "#5b6b67", "#007d72", "#d6dbd3", "#f1f3ee", "#b45309"
ZONE_COL, NODATA_COL = "#ff3b3b", "#8a94a3"
CLS_COL = ["#ffe38a", "#ffb24a", "#ff6a3d", "#d9214f"]  # как на карте: низкое … очень высокое
CLS_NAME = ["низкое", "умеренное", "высокое", "очень высокое"]
WIN_KM = (2.4, 1.6)  # окно района скопления, км
N_DISTRICTS, N_ROWS = 6, 12
COMPASS = ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"]
CONC_SHORT = {ST.MODEL: "мод.", ST.RESEARCH: "иссл.", ST.UNAVAILABLE: ""}
RC = {"pdf.fonttype": 42, "font.family": "DejaVu Sans", "axes.linewidth": 0.6}
KICKER = "AQUAFLOW · ОТЧЁТ О ПЛАВАЮЩЕМ МУСОРЕ"
DRIFT_COL = "#d61fa8"  # как частицы дрейфа на карте
_lock = threading.Lock()  # matplotlib не потокобезопасен, а FastAPI вызывает обработчики из пула потоков


def nf(v, d: int = 1) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v:,.{d}f}".replace(",", " ").replace(".", ",")


def zones_word(n: int) -> str:
    n = abs(int(n)) % 100
    if 11 <= n <= 19:
        return "зон"
    return {1: "зона", 2: "зоны", 3: "зоны", 4: "зоны"}.get(n % 10, "зон")


def ru_date(d: str) -> str:
    return ".".join(reversed(d.split("-")))


def coords(lat: float, lon: float) -> str:
    return f"{lat:.5f}, {lon:.5f}"


def _wrap(s: str, width_in: float, size: float) -> tuple[str, int]:
    lines = textwrap.wrap(s, max(8, int(width_in * 72 / (0.57 * size))))
    return "\n".join(lines), len(lines)


# ---------- геометрия ----------

class Grid:
    """Сетка water.tif (10 м): lon/lat ↔ пиксели. rgb.jpg — та же сетка через пиксель."""

    def __init__(self, aoi: str):
        with rasterio.open(PROCESSED / aoi / "water.tif") as s:
            self.water = s.read(1).astype(bool)
            self.t, crs = s.transform, s.crs
        self.h, self.w = self.water.shape
        self.m = abs(self.t.a)  # м на пиксель
        self._fwd = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        self._inv = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)

    def px(self, lon, lat) -> tuple[np.ndarray, np.ndarray]:
        x, y = self._fwd.transform(np.asarray(lon, float), np.asarray(lat, float))
        return (np.asarray(x) - self.t.c) / self.t.a, (np.asarray(y) - self.t.f) / self.t.e

    def lonlat(self, col, row) -> tuple[np.ndarray, np.ndarray]:
        x = self.t.c + np.asarray(col, float) * self.t.a
        y = self.t.f + np.asarray(row, float) * self.t.e
        lon, lat = self._inv.transform(x, y)
        return np.asarray(lon), np.asarray(lat)

    def coast_km(self, col: float, row: float, max_km: float = 5.0) -> float | None:
        """Расстояние до ближайшего пикселя не-воды по маске 10 м; дальше max_km — None."""
        r = int(max_km * 1000 / self.m)
        c0, r0 = max(int(col) - r, 0), max(int(row) - r, 0)
        rr, cc = np.nonzero(~self.water[r0:int(row) + r + 1, c0:int(col) + r + 1])
        if not len(rr):
            return None
        d = float(np.hypot(rr + r0 + 0.5 - row, cc + c0 + 0.5 - col).min()) * self.m / 1000
        return d if d <= max_km else None


@lru_cache(maxsize=2)
def _grid(aoi: str) -> Grid:
    return Grid(aoi)


def landmark(aoi: str, lon: float, lat: float) -> str:
    """Расстояние и румб от ближайшего ориентира: порта или устья реки."""
    a = AOIS[aoi]
    best = None
    for name, (lo, la) in [("порта", a["port"])] + [(f"устья р. {n}", ll) for n, ll in a["rivers"].items()]:
        dx = (lon - lo) * 111.32 * np.cos(np.radians((lat + la) / 2))
        dy = (lat - la) * 111.32
        if best is None or np.hypot(dx, dy) < best[0]:
            best = (float(np.hypot(dx, dy)), name, dx, dy)
    d, name, dx, dy = best
    if d < 0.3:
        return f"у {name}"
    return f"{nf(d)} км к {COMPASS[int((np.degrees(np.arctan2(dx, dy)) + 382.5) // 45) % 8]} от {name}"


def districts(col, row, cover, win_w, win_h, img_w, img_h, k: int = N_DISTRICTS) -> list[dict]:
    """Районы скопления: окна win_w × win_h пикселей с наибольшим суммарным покрытием зон.

    Жадно: окно с максимумом покрытия среди ещё не попавших в район зон, центр — средневзвешенный
    по покрытию (в пределах снимка), зоны окна исключаются; и так до k районов.
    """
    free = np.ones(len(cover), bool)
    out = []
    while free.any() and len(out) < k:
        idx = np.flatnonzero(free)
        x, y, w = col[idx], row[idx], cover[idx] + 1e-6
        inside = (np.abs(x[:, None] - x[None]) <= win_w / 2) & (np.abs(y[:, None] - y[None]) <= win_h / 2)
        m = inside[int(np.argmax(inside.astype(np.float32) @ w.astype(np.float32)))]
        cx = float(np.clip(np.average(x[m], weights=w[m]), win_w / 2, img_w - win_w / 2))
        cy = float(np.clip(np.average(y[m], weights=w[m]), win_h / 2, img_h - win_h / 2))
        m2 = (np.abs(x - cx) <= win_w / 2) & (np.abs(y - cy) <= win_h / 2)
        members = idx[m2 if m2.any() else m]
        free[members] = False
        out.append({"cx": cx, "cy": cy, "members": members, "cover": float(cover[members].sum())})
    return sorted(out, key=lambda d: -d["cover"])


# ---------- данные отчёта ----------

def _context(aoi: str, date: str, profile: str) -> dict:
    prof = _profile(profile)
    s = _read(WEB / aoi / "series.json")
    di = s["dates"].index(date)
    sc = s["scenes"][di]
    g = _grid(aoi)
    zones = [f["properties"] for f in _read(WEB / aoi / date / "zones.geojson")["features"]]
    zc, zr = g.px([z["lon"] for z in zones], [z["lat"] for z in zones]) if zones else (np.zeros(0), np.zeros(0))
    cover = np.array([z["cover_m2"] for z in zones], float)
    total = float(cover.sum())

    dist = districts(zc, zr, cover, WIN_KM[0] * 1000 / g.m, WIN_KM[1] * 1000 / g.m, g.w, g.h)
    zone_district = {}
    for k, d in enumerate(dist, 1):
        lon, lat = g.lonlat(d["cx"], d["cy"])
        d.update(n=k, lon=float(lon), lat=float(lat), share=d["cover"] / total if total else 0.0,
                 where=landmark(aoi, float(lon), float(lat)), coast=g.coast_km(d["cx"], d["cy"]))
        zone_district.update({int(i): k for i in d["members"]})

    rows = []
    for i in sorted(range(len(zones)), key=lambda i: (-cover[i], -zones[i]["p_max"], zones[i]["zone_id"]))[:N_ROWS]:
        z = zones[i]
        rows.append({"i": i, "n": len(rows) + 1, "z": z, "district": zone_district.get(i), "col": zc[i], "row": zr[i],
                     "where": landmark(aoi, z["lon"], z["lat"]), "coast": g.coast_km(zc[i], zr[i])})

    try:
        conc = _read(WEB / aoi / f"conc_{profile}.json")
    except HTTPException:
        conc = None
    vals = [v for v in (conc["value"][di] if conc and conc.get("available") else []) if v is not None]
    codes = s["status_codes"]
    status = [codes[str(v)] for v in s["status"][di]]
    t = datetime.fromisoformat(sc["datetime"]) + timedelta(hours=AOIS[aoi]["tz"])
    sep = WEB / aoi / "separation.json"
    sep = _read(sep)["by_date"].get(date) if sep.exists() else None
    return {"aoi": aoi, "a": AOIS[aoi], "date": date, "profile": profile, "prof": prof, "s": s, "di": di, "sc": sc,
            "conc": conc, "sep": sep, "field": _field_in(aoi, profile),
            "grid": g, "zones": zones, "zc": zc, "zr": zr, "total": total, "districts": dist, "rows": rows,
            "status": status, "conc_median": float(np.median(vals)) if vals else None,
            "conc_model": conc.get("model_version") if conc else None, "local_time": t.strftime("%H:%M"),
            "hexes": _read(WEB / aoi / "hexes.geojson")["features"]}


# ---------- вёрстка ----------

class Page:
    """Страница A4; координаты в дюймах от левого верхнего угла."""

    def __init__(self):
        self.fig = Figure(figsize=(PAGE_W, PAGE_H), facecolor="white")

    def text(self, x, y, s, size=8.0, color=INK, weight="normal", ha="left", va="top", **kw):
        return self.fig.text(x / PAGE_W, 1 - y / PAGE_H, s, fontsize=size, color=color, fontweight=weight,
                             ha=ha, va=va, **kw)

    def para(self, x, y, s, width, size=7.5, color=MUTED, **kw) -> float:
        """Абзац с переносом; возвращает y под ним."""
        txt, n = _wrap(s, width, size)
        self.text(x, y, txt, size, color, linespacing=1.35, **kw)
        return y + n * size * 1.35 / 72 + 0.04

    def axes(self, x, y, w, h):
        return self.fig.add_axes((x / PAGE_W, 1 - (y + h) / PAGE_H, w / PAGE_W, h / PAGE_H))

    def canvas(self, x, y, w, h):
        """Оси без рамки в дюймах, (0, 0) — левый верхний угол: для плашек, легенды и таблиц."""
        ax = self.axes(x, y, w, h)
        ax.set_xlim(0, w)
        ax.set_ylim(h, 0)
        ax.axis("off")
        return ax


def _halo(w=2.2, color="white"):
    return [pe.withStroke(linewidth=w, foreground=color)]


def _title_parts(name: str) -> tuple[str, str]:
    if name.endswith(")") and " (" in name:
        head, sub = name[:-1].split(" (", 1)
        return head, sub
    return name, ""


def _scalebar(ax, x0, y0, m_per_px, km, size=6.5):
    """Масштабная линейка в пикселях сетки: левый край (x0, y0)."""
    L = km * 1000 / m_per_px
    ax.plot([x0, x0 + L], [y0, y0], color="white", lw=2.2, solid_capstyle="butt", zorder=8,
            path_effects=_halo(3.6, INK))
    label = f"{nf(km, 0)} км" if km >= 1 else f"{nf(km * 1000, 0)} м"
    ax.annotate(label, (x0 + L / 2, y0), xytext=(0, 3), textcoords="offset points", fontsize=size, color="white",
                ha="center", va="bottom", zorder=8, path_effects=_halo(1.8, INK), fontweight="bold")


def _quiet_spot(pts: np.ndarray, x0, y0, w, h, L, spots) -> tuple[float, float]:
    """Левый край линейки длиной L: из углов spots (доли окна) — тот, где рядом меньше всего точек pts."""
    def crowd(s):
        bx, by = x0 + s[0] * w, y0 + s[1] * h
        return int(((np.abs(pts[:, 0] - bx - L / 2) < L / 2 + 0.04 * w) & (np.abs(pts[:, 1] - by) < 0.08 * h)).sum())
    s = min(spots, key=crowd)
    return x0 + s[0] * w, y0 + s[1] * h


def _nice_step(span: float) -> float:
    for st in (0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0):
        if span / st <= 5:
            return st
    return 5.0


def _geo_ticks(ax, g: Grid):
    """Подписи долготы по нижнему краю и широты по левому — снимок в UTM, поэтому интерполяция по краю."""
    cols = np.linspace(0, g.w, 60)
    lon, _ = g.lonlat(cols, np.full_like(cols, g.h))
    rows = np.linspace(0, g.h, 60)
    _, lat = g.lonlat(np.zeros_like(rows), rows)
    for vals_ax, span, set_ticks, suffix in (
            (lon, cols, ax.set_xticks, ("в.д.", "з.д.")), (lat[::-1], rows[::-1], ax.set_yticks, ("с.ш.", "ю.ш."))):
        st = _nice_step(vals_ax[-1] - vals_ax[0])
        ticks = np.arange(np.ceil(vals_ax[0] / st) * st, vals_ax[-1], st)
        nd = len(f"{st:g}".partition(".")[2])
        set_ticks(np.interp(ticks, vals_ax, span), labels=[f"{abs(v):.{nd}f}° {suffix[int(v < 0)]}" for v in ticks])
    ax.tick_params(labelsize=6, colors=MUTED, length=2.5, width=0.5, pad=2)
    for tl in ax.get_yticklabels():
        tl.set_rotation(90)
        tl.set_va("center")
    for sp in ax.spines.values():
        sp.set_color(LINE)


def _header_full(p: Page, c: dict) -> float:
    a, sc = c["a"], c["sc"]
    head, sub = _title_parts(a["name"])
    y = MARGIN
    p.text(MARGIN, y, KICKER, 7.5, TIDE, "bold")
    p.text(PAGE_W - MARGIN, y, f"Профиль {c['profile']}: {c['prof']['label']}", 7, MUTED, ha="right")
    y += 0.24
    p.text(MARGIN, y, head, 18, INK, "bold")
    y += 0.34
    if sub:
        p.text(MARGIN, y, sub, 9, MUTED)
        y += 0.2
    p.text(MARGIN, y, f"Снимок {sc.get('platform') or 'Sentinel-2'} · {ru_date(c['date'])}, пролёт "
                      f"{c['local_time']} (UTC+{a['tz']}) · облачность тайла {nf(sc.get('tile_cloud_pct'), 0)}%",
           8.5, INK)
    y += 0.19
    p.text(MARGIN, y, f"Сцена {sc.get('scene_id') or '—'}", 6.5, MUTED)
    return y + 0.26


def _kpis(p: Page, c: dict, y: float) -> float:
    sc, st = c["sc"], c["status"]
    det = st.count(ST.DETECTED)
    wind = f", {nf(sc['wind'])} м/с" if sc.get("wind") is not None else ""
    med = c["conc_median"]
    items = [
        (nf(len(c["zones"]), 0), "зон детекции на снимке"),
        (f"{nf(c['total'], 0)} м²", "покрытие мусором (эквивалентная площадь)"),
        (f"{nf(det, 0)} из {nf(len(st), 0)}", "гексов H3 (~0,7 км²) с мусором"),
        (f"{nf(sc['valid_frac'] * 100, 0)}%", f"видно воды · море {sc.get('sea', '—')}{wind}"),
        (nf(med, 0) if med is not None else "—",
         f"шт./км², медиана по гексам, профиль {c['profile']}" if med is not None else
         f"концентрация профиля {c['profile']} здесь недоступна"),
    ]
    h, gap = 0.7, 0.1
    w = (CONTENT_W - gap * (len(items) - 1)) / len(items)
    ax = p.canvas(MARGIN, y, CONTENT_W, h)
    for k, (val, label) in enumerate(items):
        x = k * (w + gap)
        ax.add_patch(FancyBboxPatch((x, 0), w, h, boxstyle="round,pad=0,rounding_size=0.07", fc=SOFT, ec="none"))
        ax.text(x + 0.1, 0.1, val, fontsize=12.5, color=INK, fontweight="bold", va="top")
        ax.text(x + 0.1, 0.36, _wrap(label, w - 0.16, 6.3)[0], fontsize=6.3, color=MUTED, va="top", linespacing=1.3)
    return y + h + 0.26


def _summary(c: dict) -> str:
    ds, n = c["districts"], len(c["zones"])
    if not n:
        ins = c["status"].count(ST.INSUFFICIENT)
        tail = (f" Для {nf(ins, 0)} из {nf(len(c['status']), 0)} гексов данных недостаточно (облака, блик, шторм): "
                "там отсутствие мусора не подтверждено.") if ins else ""
        return "Зон детекции на этом снимке нет." + tail
    d = ds[0]
    rest = n - sum(len(x["members"]) for x in ds)
    both = sum(x["share"] for x in ds)
    s = (f"Больше всего мусора — в районе 1: {coords(d['lat'], d['lon'])}, {d['where']}. "
         f"Здесь {nf(d['share'] * 100, 0)}% всего обнаруженного покрытия.")
    if len(ds) > 1:
        s += f" Районы 1–{len(ds)} вместе дают {nf(both * 100, 0)}% покрытия"
        s += f"; остальное — {nf(rest, 0)} {zones_word(rest)} вне районов." if rest else "."
    return s


def _overview_map(p: Page, c: dict, y: float, max_h: float) -> float:
    g, s, di = c["grid"], c["s"], c["di"]
    aspect = g.w / g.h
    left = 0.42  # место под подписи широты
    w = min(CONTENT_W - left, max_h * aspect)
    h = w / aspect
    ax = p.axes(MARGIN + left + (CONTENT_W - left - w) / 2, y, w, h)

    rgb = Image.open(WEB / c["aoi"] / c["date"] / "rgb.jpg").convert("RGB")
    ext = (0, rgb.width * 2, rgb.height * 2, 0)  # rgb.jpg — каждый второй пиксель сетки
    tw = int(w * 190)
    if tw < rgb.width:
        rgb = rgb.resize((tw, round(rgb.height * tw / rgb.width)), Image.LANCZOS)
    ax.imshow(np.asarray(rgb), extent=ext, interpolation="none", zorder=0)

    # Гексы: «обнаружено» — класс покрытия, «недостаточно данных» — серым
    edges = s["cover_class_edges"]
    polys, fcs = [], []
    for f, st in zip(c["hexes"], c["status"]):
        i = f["properties"]["i"]
        if st == ST.DETECTED:
            k = sum(s["cover"][di][i] > e for e in edges)
            col = CLS_COL[max(k, 1) - 1]
        elif st == ST.INSUFFICIENT:
            col = NODATA_COL
        else:
            continue
        ring = np.asarray(f["geometry"]["coordinates"][0])
        polys.append(np.column_stack(g.px(ring[:, 0], ring[:, 1])))
        fcs.append(col)
    if polys:
        alpha = [0.45 if col == NODATA_COL else 0.78 for col in fcs]
        ax.add_collection(PolyCollection(polys, facecolors=[to_rgba(col, a) for col, a in zip(fcs, alpha)],
                                         edgecolors="none", zorder=2))
    if len(c["zc"]):
        ax.scatter(c["zc"], c["zr"], s=5, c=ZONE_COL, edgecolors="white", linewidths=0.25, zorder=4)

    ww, wh = WIN_KM[0] * 1000 / g.m, WIN_KM[1] * 1000 / g.m
    px_in = g.w / w  # пикселей сетки на дюйм карты
    placed: list[tuple[float, float]] = []
    for d in c["districts"]:
        x0, y0 = d["cx"] - ww / 2, d["cy"] - wh / 2
        ax.add_patch(Rectangle((x0, y0), ww, wh, fill=False, ec="white", lw=1.1, zorder=5,
                               path_effects=_halo(2.6, INK)))
        # Номер — в свободный угол рамки: соседние районы часто стоят вплотную
        corners = [(x0, y0), (x0 + ww, y0), (x0, y0 + wh), (x0 + ww, y0 + wh)]
        lx, ly = next((q for q in corners if all(np.hypot(q[0] - u, q[1] - v) > 0.13 * px_in for u, v in placed)),
                      corners[0])
        placed.append((lx, ly))
        ax.text(lx, ly, str(d["n"]), fontsize=7, color="white", fontweight="bold", ha="center", va="center",
                zorder=6, bbox={"boxstyle": "round,pad=0.25", "fc": TIDE, "ec": "white", "lw": 0.6})

    a = c["a"]
    marks: list[list] = []
    for name, (lo, la), mk in [("порт", a["port"], "o")] + [(f"устье {n}", ll, "v") for n, ll in a["rivers"].items()]:
        mc, mr = (float(v) for v in g.px(lo, la))
        near = next((m for m in marks if np.hypot(m[1] - mc, m[2] - mr) < 0.12 * px_in), None)
        if near:  # порт у самого устья — одна подпись
            near[0] += f" · {name}"
        elif 0 <= mc <= g.w and 0 <= mr <= g.h:
            marks.append([name, mc, mr, mk])
    for name, mc, mr, mk in marks:
        ax.plot(mc, mr, marker=mk, ms=5, mfc="white", mec=INK, mew=0.8, zorder=7)
        right = mc > g.w * 0.8  # у правого края подпись слева от значка
        ax.text(mc + (-1 if right else 1) * 0.06 * px_in, mr, name, fontsize=6.5, color="white", va="center",
                ha="right" if right else "left", zorder=7, path_effects=_halo(1.8, INK))

    width_km = g.w * g.m / 1000
    km = max([v for v in (0.5, 1, 2, 5, 10, 20, 50) if v <= width_km / 5] or [0.5])
    L = km * 1000 / g.m
    pts = np.array([(x, y) for x, y in zip(c["zc"], c["zr"])] + [(d["cx"], d["cy"]) for d in c["districts"]]
                   + [(m[1], m[2]) for m in marks]).reshape(-1, 2)
    _scalebar(ax, *_quiet_spot(pts, 0, 0, g.w, g.h, L, [(0.03, 0.95), (0.97 - L / g.w, 0.95), (0.03, 0.08)]), g.m, km)
    ax.text(g.w * 0.975, g.h * 0.04, "↑\nС", fontsize=8, color="white", fontweight="bold", ha="center", va="top",
            linespacing=0.9, zorder=8, path_effects=_halo(2, INK))
    ax.set_xlim(0, g.w)
    ax.set_ylim(g.h, 0)
    _geo_ticks(ax, g)
    return y + h + 0.3


def _legend(p: Page, c: dict, y: float) -> float:
    e = c["s"]["cover_class_edges"]
    ranges = [f"до {nf(e[1], 0)}", f"{nf(e[1], 0)}–{nf(e[2], 0)}", f"{nf(e[2], 0)}–{nf(e[3], 0)}", f"> {nf(e[3], 0)}"]
    ax = p.canvas(MARGIN, y, CONTENT_W, 0.42)
    ax.text(0, 0.07, "Покрытие гекса, м² мусора на км²:", fontsize=6.8, color=INK, va="center")
    x = 1.85
    for col, name, rng in zip(CLS_COL, CLS_NAME, ranges):
        ax.add_patch(Rectangle((x, 0.01), 0.16, 0.12, fc=col, ec="none"))
        ax.text(x + 0.21, 0.07, f"{name} ({rng})", fontsize=6.5, color=MUTED, va="center")
        x += 0.3 + len(f"{name} ({rng})") * 0.052
    x = 0
    items = [
        (lambda x0: ax.add_patch(Rectangle((x0 + 0.02, 0.25), 0.14, 0.12, fc=NODATA_COL, alpha=0.5, ec="none")),
         "недостаточно данных (облака, блик)"),
        (lambda x0: ax.scatter([x0 + 0.09], [0.31], s=14, c=ZONE_COL, edgecolors="white", linewidths=0.4),
         "зона детекции"),
        (lambda x0: ax.add_patch(Rectangle((x0, 0.24), 0.18, 0.14, fill=False, ec=TIDE, lw=1.1)),
         f"район скопления {nf(WIN_KM[0])} × {nf(WIN_KM[1])} км (стр. 2)"),
        (lambda x0: ax.plot([x0 + 0.09], [0.31], marker="o", ms=4.5, mfc="white", mec=INK, mew=0.8),
         "порт / устье реки"),
    ]
    for draw, label in items:
        draw(x)
        ax.text(x + 0.24, 0.31, label, fontsize=6.5, color=MUTED, va="center")
        x += 0.45 + len(label) * 0.052
    return y + 0.5


def _page_overview(c: dict) -> Page:
    p = Page()
    y = _header_full(p, c)
    y = _kpis(p, c, y)
    p.text(MARGIN, y, "Где больше всего мусора", 12, INK, "bold")
    y = p.para(MARGIN, y + 0.24, _summary(c), CONTENT_W, 8.2, INK)
    sc = c["sc"]
    if sc.get("storm"):
        y = p.para(MARGIN, y, f"Ненадёжная сцена ({sc.get('reason') or 'шторм, лёд или блик'}): оставлены только "
                              "крупные скопления, отсутствие мусора не подтверждается.", CONTENT_W, 7.3, WARN)
    if c["a"].get("note"):
        y = p.para(MARGIN, y, c["a"]["note"], CONTENT_W, 6.8)
    ds = c["districts"]
    below = 0.3 + 0.5 + (0.3 + TABLE_HEAD + TABLE_ROW * len(ds) if ds else 0) + 0.3  # подписи, легенда, сводка
    y = _overview_map(p, c, y + 0.1, PAGE_H - MARGIN - below - (y + 0.1))
    y = _legend(p, c, y)
    if ds:
        p.text(MARGIN, y + 0.02, "Районы скопления", 10, INK, "bold")
        _table(p, y + 0.26, DISTRICT_COLS, [
            [str(d["n"]), coords(d["lat"], d["lon"]), d["where"], _coast(d["coast"]), nf(len(d["members"]), 0),
             nf(d["cover"]), f"{nf(d['share'] * 100, 0)}%"] for d in ds])
    return p


def _coast(km: float | None) -> str:
    return nf(km) if km is not None else "> 5"


def _crop(p: Page, c: dict, d: dict, x: float, y: float, w: float, h: float, rgb: Image.Image, numbered: dict):
    g = c["grid"]
    ww, wh = WIN_KM[0] * 1000 / g.m, WIN_KM[1] * 1000 / g.m
    c0, r0 = d["cx"] - ww / 2, d["cy"] - wh / 2
    box = (int(np.floor(c0 / 2)), int(np.floor(r0 / 2)), int(np.ceil((c0 + ww) / 2)), int(np.ceil((r0 + wh) / 2)))
    im = rgb.crop(box)
    im = im.resize((im.width * 4, im.height * 4), Image.BICUBIC)
    ax = p.axes(x, y, w, h)
    ax.imshow(np.asarray(im), extent=(box[0] * 2, box[2] * 2, box[3] * 2, box[1] * 2), interpolation="none")
    inside = (np.abs(c["zc"] - d["cx"]) <= ww / 2) & (np.abs(c["zr"] - d["cy"]) <= wh / 2)
    ax.scatter(c["zc"][inside], c["zr"][inside], s=7, c=ZONE_COL, edgecolors="white", linewidths=0.35, zorder=3)
    for i in np.flatnonzero(inside):
        if int(i) not in numbered:
            continue
        z = c["zones"][i]
        r = (40 + 20 * np.sqrt(z["n_pixels"])) / g.m
        ax.add_patch(Circle((c["zc"][i], c["zr"][i]), r, fill=False, ec="#ffd43b", lw=1.1, zorder=4,
                            path_effects=_halo(2.2, INK)))
        ax.text(c["zc"][i] + r * 0.8, c["zr"][i] - r * 0.8, str(numbered[int(i)]), fontsize=6.5, color="#ffd43b",
                fontweight="bold", zorder=5, path_effects=_halo(1.8, INK))
    ax.text(c0 + ww * 0.035, r0 + wh * 0.06, str(d["n"]), fontsize=8, color="white", fontweight="bold",
            ha="left", va="top", zorder=6, bbox={"boxstyle": "round,pad=0.3", "fc": TIDE, "ec": "white", "lw": 0.6})
    L = 500 / g.m
    pts = np.column_stack([c["zc"][inside], c["zr"][inside]])
    _scalebar(ax, *_quiet_spot(pts, c0, r0, ww, wh, L, [(0.05, 0.92), (0.95 - L / ww, 0.92), (0.95 - L / ww, 0.1)]),
              g.m, 0.5, 6)
    ax.set_xlim(c0, c0 + ww)
    ax.set_ylim(r0 + wh, r0)
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_color(LINE)

    ty = y + h + 0.07
    p.text(x, ty, f"Район {d['n']}", 8, INK, "bold")
    p.text(x + w, ty, f"{nf(d['share'] * 100, 0)}% покрытия", 7.5, TIDE, "bold", ha="right")
    p.text(x, ty + 0.16, coords(d["lat"], d["lon"]), 7, INK)
    p.text(x, ty + 0.3, d["where"], 6.6, MUTED)
    coast = f"до берега {_coast(d['coast'])} км"
    k = len(d["members"])
    p.text(x, ty + 0.43, f"{nf(k, 0)} {zones_word(k)} · {nf(d['cover'], 0)} м² · {coast}", 6.6, MUTED)


TABLE_HEAD, TABLE_ROW = 0.34, 0.205
DISTRICT_COLS = [  # заголовок, ширина (дюймы, в сумме CONTENT_W), выравнивание
    ("Район", 0.55, "center"), ("Центр района\n(шир., долг.)", 1.35, "left"), ("Ориентир", 2.1, "left"),
    ("До берега,\nкм", 0.75, "right"), ("Зон", 0.55, "right"), ("Покрытие,\nм²", 0.85, "right"),
    ("Доля всего\nпокрытия", 1.02, "right"),
]
ZONE_COLS = [
    ("№", 0.28, "center"), ("Район", 0.44, "center"), ("Координаты\n(шир., долг.)", 1.2, "left"),
    ("Ориентир", 1.86, "left"), ("До берега,\nкм", 0.62, "right"), ("Покрытие,\nм²", 0.66, "right"),
    ("P\nмакс.", 0.41, "right"), ("Концентрация {P}, шт./км²\n(80%-интервал)", 1.70, "left"),
]


def _table(p: Page, y: float, cols: list[tuple], rows: list[list[str]]) -> float:
    """Таблица с шапкой и зеброй; возвращает y под ней."""
    ax = p.canvas(MARGIN, y, CONTENT_W, TABLE_HEAD + TABLE_ROW * len(rows))
    ax.add_patch(Rectangle((0, 0), CONTENT_W, TABLE_HEAD, fc=SOFT, ec="none"))
    ax.plot([0, CONTENT_W], [TABLE_HEAD, TABLE_HEAD], color=INK, lw=0.6)

    def cells(yc, values, size, color, weight="normal"):
        x = 0.0
        for (_, w, al), v in zip(cols, values):
            tx = {"left": x + 0.06, "center": x + w / 2, "right": x + w - 0.06}[al]
            ax.text(tx, yc, v, fontsize=size, color=color, ha=al, va="center", fontweight=weight, linespacing=1.15)
            x += w

    cells(TABLE_HEAD / 2, [t for t, _, _ in cols], 6.3, MUTED, "bold")
    for k, row in enumerate(rows):
        top = TABLE_HEAD + k * TABLE_ROW
        if k % 2:
            ax.add_patch(Rectangle((0, top), CONTENT_W, TABLE_ROW, fc="#f8f9f6", ec="none"))
        cells(top + TABLE_ROW / 2, row, 6.7, INK)
    bottom = TABLE_HEAD + TABLE_ROW * len(rows)
    ax.plot([0, CONTENT_W], [bottom, bottom], color=LINE, lw=0.6)
    return y + bottom + 0.18


def _zone_rows(c: dict) -> list[list[str]]:
    P, out = c["profile"], []
    for r in c["rows"]:
        z = r["z"]
        v, st = z.get(f"conc_{P}_items_km2"), z.get(f"conc_{P}_status") or ST.UNAVAILABLE
        conc = (f"{nf(v, 0)} ({nf(z.get(f'conc_{P}_lo80'), 0)}–{nf(z.get(f'conc_{P}_hi80'), 0)}) {CONC_SHORT[st]}"
                if v is not None else "— недоступна")
        out.append([str(r["n"]), str(r["district"] or "—"), coords(z["lat"], z["lon"]), r["where"],
                    _coast(r["coast"]), nf(z["cover_m2"]), nf(z["p_max"], 2), conc])
    return out


def _page_details(c: dict) -> Page:
    p = Page()
    head, _ = _title_parts(c["a"]["name"])
    y = MARGIN
    p.text(MARGIN, y, KICKER, 7.5, TIDE, "bold")
    p.text(PAGE_W - MARGIN, y, f"{head} · {ru_date(c['date'])} · профиль {c['profile']}", 7, MUTED, ha="right")
    y += 0.36
    p.text(MARGIN, y, "Районы скопления", 12, INK, "bold")
    y = p.para(MARGIN, y + 0.24,
               f"Фрагменты снимка {nf(WIN_KM[0])} × {nf(WIN_KM[1])} км там, где суммарное покрытие зон больше всего; "
               "номер района совпадает с рамкой на обзорной карте. Красные точки — зоны детекции, жёлтые кольца "
               "с номерами — зоны из таблицы ниже.", CONTENT_W, 7.2)

    numbered = {r["i"]: r["n"] for r in c["rows"]}
    rgb = Image.open(WEB / c["aoi"] / c["date"] / "rgb.jpg").convert("RGB")
    gap = 0.14
    w = (CONTENT_W - 2 * gap) / 3
    h = w * WIN_KM[1] / WIN_KM[0]
    y += 0.08
    for k, d in enumerate(c["districts"]):
        row, col = divmod(k, 3)
        _crop(p, c, d, MARGIN + col * (w + gap), y + row * (h + 0.78), w, h, rgb, numbered)
    y += -(-len(c["districts"]) // 3) * (h + 0.78) + 0.08

    p.text(MARGIN, y, "Где именно: крупнейшие зоны", 12, INK, "bold")
    n = len(c["zones"])
    k = len(c["rows"])
    y = p.para(MARGIN, y + 0.24, f"{k} {zones_word(k)} с наибольшим покрытием из {nf(n, 0)} на снимке. "
                                 "Координаты — центр зоны (WGS 84), их можно вставить в навигатор. «—» в столбце "
                                 "«Район» — одиночная зона вне районов скопления.", CONTENT_W, 7.2)
    cols = [(t.format(P=c["profile"]), w, al) for t, w, al in ZONE_COLS]
    y = _table(p, y + 0.04, cols, _zone_rows(c))

    notes = [
        f"Зона детекции — связная группа пикселей 10 м, где детектор Sentinel-2 видит плавающий мусор "
        f"(P ≥ {nf(c['s']['detector']['p_det'], 2)}). Покрытие — эквивалентная площадь мусора в зоне, м²: "
        "вспомогательный показатель детектора.",
        f"Концентрация — модель профиля {c['profile']} «{c['prof']['label']}» по полевым данным, в шт./км², "
        "с 80%-интервалом; из площади зоны она не выводится. «мод.» — модельная оценка, "
        "«иссл.» — исследовательская (перенос модели на это место не подтверждён).",
        "Ориентир — расстояние и румб от ближайшего из порта и устьев рек. До берега — по маске воды 10 м "
        "(дальше 5 км не считается).",
        "Детектор не различает материал: пластик, плавник, водоросли и пена выглядят похоже, скопления "
        "подтверждают судном или дроном. Полный список зон — выгрузка «Зоны · CSV» или GeoJSON.",
    ]
    for s in notes:
        y = p.para(MARGIN, y, s, CONTENT_W, 6.6)
    return p


def _field_in(aoi: str, profile: str) -> list[dict]:
    """Полевые измерения профиля внутри рамки акватории — для графика концентрации."""
    import pandas as pd

    path = DATA / "field" / "events.csv"
    if not path.exists():
        return []
    ev = pd.read_csv(path)
    x0, y0, x1, y1 = AOIS[aoi]["bbox"]
    ev = ev[(ev["profile"] == profile) & ev["lon"].between(x0, x1) & ev["lat"].between(y0, y1)]
    return ev[["event_id", "date_utc", "conc_items_km2"]].to_dict("records")


def _drift(c: dict, hours: int = 72, max_seeds: int = 300, n_ens: int = 4) -> dict | None:
    """Прогноз дрейфа всех детекций (как `GET /api/aois/{aoi}/{date}/drift`); None — детекций нет или нет полей."""
    import json

    from pipeline.drift import simulate

    pts = np.asarray(json.loads((WEB / c["aoi"] / c["date"] / "points.json").read_text(encoding="utf-8")),
                     float).reshape(-1, 4)
    if not len(pts):
        return None
    if len(pts) > max_seeds:
        pts = pts[np.argsort(-pts[:, 2] * pts[:, 3])[:max_seeds]]
    try:
        res = simulate(c["aoi"], c["date"], pts[:, 0], pts[:, 1], hours=hours, n_ens=n_ens, seed=1)
    except Exception as e:  # нет кеша полей и нет сети — отчёт всё равно собирается
        return {"error": str(e)[:160]}
    tr = res["track"].astype(float)
    lat0 = np.radians(tr[:, 0, 1])
    disp = {h: np.hypot((tr[:, h, 0] - tr[:, 0, 0]) * np.cos(lat0), tr[:, h, 1] - tr[:, 0, 1]) * 111.32
            for h in (24, 48, hours)}
    dx = float(np.mean((tr[:, -1, 0] - tr[:, 0, 0]) * np.cos(lat0)))
    dy = float(np.mean(tr[:, -1, 1] - tr[:, 0, 1]))
    return {"track": tr, "beached": res["beached"], "hours": hours, "disp": disp,
            "heading": COMPASS[int((np.degrees(np.arctan2(dx, dy)) + 382.5) // 45) % 8],
            "windage": res["windage"], "n": len(tr)}


def _style(ax, title: str):
    ax.set_title(title, fontsize=8, color=INK, loc="left", fontweight="bold", pad=6)
    ax.tick_params(labelsize=6, colors=MUTED, length=2, width=0.5)
    for k, sp in ax.spines.items():
        sp.set_color(LINE)
        sp.set_visible(k in ("left", "bottom"))
    ax.grid(axis="y", color=LINE, lw=0.4)
    ax.set_axisbelow(True)


def _chart_series(p: Page, c: dict, y: float, h: float) -> float:
    s, di = c["s"], c["di"]
    x = np.arange(len(s["dates"]))
    area = np.array([sc["area_m2"] for sc in s["scenes"]], float)
    zones = np.array([sc.get("n_zones") or 0 for sc in s["scenes"]], float)
    col = [TIDE if i == di else NODATA_COL if sc.get("storm") else "#9fcdc5" for i, sc in enumerate(s["scenes"])]
    ax = p.axes(MARGIN + 0.5, y, CONTENT_W - 1.05, h)
    ax.bar(x, area, color=col, width=0.72, zorder=2)
    _style(ax, "Покрытие мусором по снимкам, м² (столбцы) и число зон (линия)")
    ax2 = ax.twinx()
    ax2.plot(x, zones, color=ZONE_COL, lw=0.9, marker="o", ms=2.2, zorder=3)
    ax2.tick_params(labelsize=6, colors=ZONE_COL, length=2, width=0.5)
    for sp in ax2.spines.values():
        sp.set_visible(False)
    ax2.set_ylim(bottom=0)
    step = max(1, len(x) // 10)
    ticks = sorted(set(range(0, len(x), step)) | {di})
    ax.set_xticks(ticks, [ru_date(s["dates"][i])[:5] + "." + s["dates"][i][2:4] for i in ticks], rotation=0)
    for t in ax.get_xticklabels():
        if t.get_text().startswith(ru_date(s["dates"][di])[:5]):
            t.set_color(TIDE)
            t.set_fontweight("bold")
    ax.set_xlim(-0.6, len(x) - 0.4)
    return y + h + 0.42


def _chart_conc(p: Page, c: dict, x0: float, y: float, w: float, h: float):
    ax = p.axes(x0 + 0.42, y, w - 0.5, h)
    conc, di, P = c["conc"], c["di"], c["profile"]
    _style(ax, f"Концентрация по гексам, профиль {P}")
    if not conc or not conc.get("available"):
        ax.text(0.5, 0.5, f"концентрация профиля {P}\nв этой акватории недоступна", transform=ax.transAxes,
                ha="center", va="center", fontsize=7, color=MUTED)
        ax.set_xticks([])
        ax.set_yticks([])
        return
    v = np.array([x for x in conc["value"][di] if x is not None], float)
    lo = np.array([x for x in conc["lo80"][di] if x is not None], float)
    hi = np.array([x for x in conc["hi80"][di] if x is not None], float)
    edges = np.geomspace(max(1.0, min(v.min(), lo.min()) * 0.8), max(v.max(), hi.max()) * 1.2, 26)
    ax.hist(v, bins=edges, color="#2f7fb8", alpha=0.85, zorder=2)
    med = float(np.median(v))
    ax.axvline(med, color=INK, lw=0.9, zorder=3)
    ax.axvspan(float(np.median(lo)), float(np.median(hi)), color="#2f7fb8", alpha=0.12, zorder=1)
    ax.set_xscale("log")
    ax.set_xlabel("шт./км² (лог. шкала)", fontsize=6, color=MUTED)
    ax.set_ylabel("гексов", fontsize=6, color=MUTED)
    top = ax.get_ylim()[1]
    ax.text(med, top * 0.97, f" медиана {nf(med, 0)}", fontsize=6, color=INK, va="top",
            path_effects=_halo(1.6, "white"))
    for f in c["field"]:
        ax.plot(f["conc_items_km2"], top * 0.05, marker="v", ms=4.5, mfc="white", mec=ZONE_COL, mew=0.9, zorder=4)
    if c["field"]:
        ax.text(0.99, 0.62, f"▽ полевые измерения\nв акватории: {len(c['field'])}", transform=ax.transAxes,
                fontsize=5.8, color=ZONE_COL, ha="right", va="top")
    ax.text(0.02, 0.97, "полоса —\n80%-интервал\n(медиана\nпо гексам)", transform=ax.transAxes, fontsize=5.6,
            color=MUTED, ha="left", va="top")


SEP_LABELS = [("foam", "пена, барашки"), ("cfar", "блик, рябь (CFAR)"), ("isolated", "одиночный шум"),
              ("ship", "суда, кильватер"), ("static", "постоянные объекты"), ("cloud", "края облаков"),
              ("ice", "лёд"), ("small", "шторм/блик: мелкие")]


def _chart_sep(p: Page, c: dict, x0: float, y: float, w: float, h: float):
    ax = p.axes(x0 + 1.05, y, w - 1.1, h)
    _style(ax, "Отбраковка похожих объектов, пикс.")
    ax.grid(False)
    st = c["sep"]
    if not st:
        ax.text(0.5, 0.5, "разбор снимка недоступен", transform=ax.transAxes, ha="center", va="center",
                fontsize=7, color=MUTED)
        ax.set_xticks([])
        ax.set_yticks([])
        return
    rows = [(lab, st["rejected"].get(k, 0), LINE) for k, lab in SEP_LABELS if st["rejected"].get(k, 0)]
    rows.append(("осталось: мусор", st["kept"], ZONE_COL))
    yy = np.arange(len(rows))[::-1]
    vals = [r[1] for r in rows]
    ax.barh(yy, vals, color=[r[2] if r[2] != LINE else "#b9c4bf" for r in rows], height=0.62)
    ax.set_yticks(yy, [r[0] for r in rows])
    ax.set_xscale("symlog", linthresh=10)
    for yv, v in zip(yy, vals):
        ax.text(v, yv, f" {nf(v, 0)}", fontsize=6, color=INK, va="center")
    ax.set_xlim(0, max(vals) * 6 if max(vals) else 10)
    ax.set_xticks([])
    ax.spines["bottom"].set_visible(False)


def _chart_drift(p: Page, c: dict, y: float, h: float) -> float:
    d = _drift(c)
    p.text(MARGIN, y, "Прогноз дрейфа на 72 часа", 12, INK, "bold")
    y += 0.24
    if d is None:
        return p.para(MARGIN, y, "На этот снимок детекций нет — переносить нечего.", CONTENT_W, 7.2)
    if "error" in d:
        return p.para(MARGIN, y, "Прогноз не рассчитан: поля течений и ветра для этой даты не в кеше, а сервис "
                                 f"погоды недоступен ({d['error']}). В веб-карте прогноз — кнопка «Дрейф».",
                      CONTENT_W, 7.2, WARN)
    y = p.para(MARGIN, y, f"Лагранжев ансамбль: {d['n']} частиц (до 300 крупнейших детекций × 4), течения SMOC + "
                          f"{nf(d['windage'] * 50, 0)}–{nf(d['windage'] * 150, 0)}% ветра ERA5, шаг 30 мин. Прогноз, "
                          "не наблюдение: на парах соседних снимков у берега он пока не точнее «пятно на месте».",
               CONTENT_W, 7.2)
    g = c["grid"]
    tr = d["track"]
    w = CONTENT_W * 0.56
    ax = p.axes(MARGIN, y + 0.05, w, h)
    rgb = Image.open(WEB / c["aoi"] / c["date"] / "rgb.jpg").convert("RGB")
    tw = int(w * 150)
    if tw < rgb.width:
        rgb = rgb.resize((tw, round(rgb.height * tw / rgb.width)), Image.LANCZOS)
    ax.imshow(np.asarray(rgb), extent=(0, g.w, g.h, 0), interpolation="none", zorder=0, alpha=0.9)
    cols = {}
    for hh, a in ((0, 0.9), (24, 0.35), (48, 0.5), (d["hours"], 0.95)):
        px, py = g.px(tr[:, hh, 0], tr[:, hh, 1])
        cols[hh] = (px, py)
        ax.scatter(px, py, s=3 if hh else 4, c=ZONE_COL if hh == 0 else DRIFT_COL, alpha=a, linewidths=0, zorder=3)
    allx = np.concatenate([v[0] for v in cols.values()] + [np.array([0, g.w])])
    ally = np.concatenate([v[1] for v in cols.values()] + [np.array([0, g.h])])
    pad = 0.04 * max(np.ptp(allx), np.ptp(ally))
    ax.set_xlim(allx.min() - pad, allx.max() + pad)
    ax.set_ylim(ally.max() + pad, ally.min() - pad)
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_color(LINE)
    mk = lambda col, a, lab: Line2D([], [], ls="", marker="o", ms=3.5, mfc=col, mec="none", alpha=a, label=lab)  # noqa: E731
    ax.legend(handles=[mk(ZONE_COL, 0.9, "старт — детекции снимка"), mk(DRIFT_COL, 0.4, "+24 и +48 ч"),
                       mk(DRIFT_COL, 0.95, f"+{d['hours']} ч")], loc="lower left", fontsize=5.8, framealpha=0.85,
              borderpad=0.4, handletextpad=0.2, edgecolor=LINE)

    x2 = MARGIN + w + 0.25
    rows = [(f"+{hh} ч", f"{nf(float(np.median(d['disp'][hh])))} км", f"{nf(float(np.percentile(d['disp'][hh], 90)))} км")
            for hh in (24, 48, d["hours"])]
    ty = y + 0.1
    p.text(x2, ty, "Смещение частиц", 8, INK, "bold")
    ty += 0.22
    for k, (a_, b_, c_) in enumerate([("Горизонт", "медиана", "90-й перц.")] + rows):
        wgt = "bold" if k == 0 else "normal"
        col = MUTED if k == 0 else INK
        p.text(x2, ty, a_, 7, col, wgt)
        p.text(x2 + 1.05, ty, b_, 7, col, wgt)
        p.text(x2 + 1.9, ty, c_, 7, col, wgt)
        ty += 0.2
    ty += 0.1
    ty = p.para(x2, ty, f"Преобладающий перенос — на {d['heading']}. Выброшено на берег за {d['hours']} ч: "
                        f"{nf(100 * float(d['beached'].mean()), 0)}% частиц.", CONTENT_W - w - 0.25, 7.2, INK)
    p.para(x2, ty + 0.05, "Куда отправить судно с поправкой на дрейф к моменту прибытия — инструмент «Маршрут» "
                          "в веб-карте (выгрузка GPX).", CONTENT_W - w - 0.25, 6.8)
    return y + 0.05 + h + 0.2


def _page_charts(c: dict) -> Page:
    p = Page()
    head, _ = _title_parts(c["a"]["name"])
    y = MARGIN
    p.text(MARGIN, y, KICKER, 7.5, TIDE, "bold")
    p.text(PAGE_W - MARGIN, y, f"{head} · {ru_date(c['date'])} · профиль {c['profile']}", 7, MUTED, ha="right")
    y += 0.36
    p.text(MARGIN, y, "Динамика, концентрация и отличие от похожих объектов", 12, INK, "bold")
    y = p.para(MARGIN, y + 0.24, f"Все {len(c['s']['dates'])} снимков акватории: выбранная дата выделена, серые — "
                                 "ненадёжные сцены (шторм, лёд, сильный блик), они не входят в тренды.", CONTENT_W, 7.2)
    y = _chart_series(p, c, y + 0.12, 1.55)
    half = (CONTENT_W - 0.3) / 2
    _chart_conc(p, c, MARGIN, y, half, 1.9)
    _chart_sep(p, c, MARGIN + half + 0.3, y, half, 1.9)
    y += 1.9 + 0.35
    y = p.para(MARGIN, y, "Концентрация — модель профиля по полевым данным в центре каждого гекса на момент снимка "
                          "(не из площади маски). Справа — сколько кандидатов в мусор (P ≥ порога) снимок отбраковал "
                          "как пену, блик и рябь, суда и кильватер, постоянные объекты, края облаков; осталось — "
                          "итоговые детекции.", CONTENT_W, 6.8)
    _chart_drift(p, c, y + 0.15, 3.3)
    return p


def _page_method(c: dict) -> list[Page]:
    """Методика: те же целевая величина, формулы и правила, что в панели «Методика». Поток текста по страницам."""
    m = methodology()
    pages: list[Page] = []
    state = {"y": PAGE_H}
    bottom = PAGE_H - MARGIN - 0.35

    def new_page():
        p = Page()
        head, _ = _title_parts(c["a"]["name"])
        p.text(MARGIN, MARGIN, KICKER, 7.5, TIDE, "bold")
        p.text(PAGE_W - MARGIN, MARGIN, f"{head} · {ru_date(c['date'])} · методика", 7, MUTED, ha="right")
        pages.append(p)
        state["y"] = MARGIN + 0.36

    def need(h):
        if state["y"] + h > bottom:
            new_page()

    def title(t):
        state["y"] += 0.12
        need(0.5)
        pages[-1].text(MARGIN, state["y"], t, 12, INK, "bold")
        state["y"] += 0.3

    def para(t, size=7.2, color=MUTED, x=MARGIN, width=CONTENT_W - 0.2):
        txt, n = _wrap(t, width, size)
        need(n * size * 1.35 / 72 + 0.05)
        state["y"] = pages[-1].para(x, state["y"], t, width, size, color)

    def mono(t):
        txt, n = _wrap(t, CONTENT_W - 0.2, 6.8)
        need(n * 6.8 * 1.3 / 72 + 0.04)
        pages[-1].text(MARGIN + 0.15, state["y"], txt, 6.8, INK, family="DejaVu Sans Mono", linespacing=1.3)
        state["y"] += n * 6.8 * 1.3 / 72 + 0.04

    new_page()
    title("Методика и формулы")
    for t in m["target"]:
        para(f"Целевая величина, профиль {t['profile']}: C, {t['unit']} — {t['definition']}. Размер: {t['size_class']}. "
             f"Метод полевого эталона: {t['method']}.", 7.6, INK)
    para("Три величины не смешиваются: полевое измерение (C = N / A по полосе учёта), модельная оценка C в шт./км² и "
         "зона детекции со снимка (площадь в м², в шт./км² не переводится).")
    state["y"] += 0.08
    for k, st in enumerate(m["steps"], 1):
        need(0.55)
        para(f"{k}. {st['title']} — {st['method']}", 7.6, INK)
        for f in st["formula"]:
            mono(f)
        para(f"{st['legend']}. Код: {st['code']}.", 6.6)
        state["y"] += 0.05
    title("Мусор, а не пена, волны, суда, водоросли или блик")
    para("Доля пикселей класса, которые основной алгоритм принял за мусор, на MARIDA test / новых сценах MADOS test.")
    for o in m["objects"]:
        a, b = ("—" if v is None else f"{nf(100 * v, 1)}%" for v in (o["marida_fp"], o["mados_fp"]))
        note = f". {o['note'][:1].upper()}{o['note'][1:]}" if o["note"] else ""
        para(f"{o['object']} — {o['status']} (MARIDA {a}, MADOS {b}): {o['how']}{note}.", 6.9, INK)
    title("Противоречивые источники и неопределённость")
    for r in m["fusion_rules"]:
        para(f"{r['case']}. {r['rule']}.", 6.9, INK)
    return pages


def _footer(p: Page, c: dict, k: int, n: int):
    y = PAGE_H - MARGIN + 0.12
    ax = p.canvas(MARGIN, y - 0.1, CONTENT_W, 0.02)
    ax.plot([0, CONTENT_W], [0, 0], color=LINE, lw=0.6)
    model = f" · модель {c['conc_model']}" if c["conc_model"] else ""
    p.text(MARGIN, y, f"AquaFlow · Sentinel-2 L2A, детектор P ≥ {nf(c['s']['detector']['p_det'], 2)}{model} · "
                      f"{c['aoi']}/{c['date']}", 6, MUTED)
    p.text(PAGE_W - MARGIN, y, f"стр. {k} / {n}", 6, MUTED, ha="right")


def figures(aoi: str, date: str, profile: str) -> list[Figure]:
    c = _context(aoi, date, profile)
    pages = [_page_overview(c)] + ([_page_details(c)] if c["zones"] else []) + [_page_charts(c)] + _page_method(c)
    for k, p in enumerate(pages, 1):
        _footer(p, c, k, len(pages))
    return [p.fig for p in pages]


@lru_cache(maxsize=16)
def build_report(aoi: str, date: str, profile: str) -> bytes:
    with _lock, matplotlib.rc_context(RC):
        buf = io.BytesIO()
        with PdfPages(buf, metadata={"Title": f"AquaFlow: {AOIS[aoi]['name']}, {ru_date(date)}", "Creator": "AquaFlow",
                                     "CreationDate": None}) as pdf:
            for fig in figures(aoi, date, profile):
                pdf.savefig(fig, dpi=200)
        return buf.getvalue()


@router.get("/api/report", tags=[Tag.EXPORT], summary="PDF-отчёт по снимку", response_class=Response, responses={
    200: {"description": "PDF, A4, 4–6 страниц. Имя файла: `report_<aoi>_<date>_<profile>.pdf`",
          "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}},
    **errors(404, 422)})
def report(aoi: AoiQuery, date: DateQuery, profile: ProfileQuery = "B"):
    """Отчёт на выбранную дату для печати и рассылки.

    Страница 1 — шапка снимка и ключевые числа (зоны, покрытие, гексы с мусором, видимость воды, медианная
    концентрация профиля), вывод «где больше всего мусора», обзорная карта с гексами по классам покрытия,
    зонами и районами скопления, таблица районов. Страница 2 (если есть зоны) — фрагменты снимка по районам и
    таблица крупнейших зон: координаты, ориентир (румб и расстояние от порта или устья), расстояние до берега,
    покрытие, P и концентрация с интервалом. Страница графиков — динамика покрытия и числа зон по всем снимкам,
    распределение концентрации по гексам с полевыми измерениями акватории, отбраковка похожих объектов (пена,
    блик, суда, облака) и прогноз дрейфа на 72 ч с картой частиц и смещением на 24/48/72 ч. Последние страницы —
    методика: целевая величина, формулы с коэффициентами моделей, различение объектов, сведение источников.
    Сборка занимает несколько секунд; повторный запрос берётся из кеша.
    """
    _aoi_date(aoi, date)
    _profile(profile)
    return Response(build_report(aoi, date, profile), media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename=report_{aoi}_{date}_{profile}.pdf"})
