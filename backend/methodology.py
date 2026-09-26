"""Методика в интерфейсе: целевая величина, методы и формулы с текущими коэффициентами моделей, различение
объектов на снимке с измеренными ошибками, правила сведения источников.

GET /api/methodology — панель «Методика» и страница «Методика и формулы» PDF-отчёта строятся из этого ответа,
поэтому числа в формулах всегда совпадают с моделями сервиса (data/models) и проверками (data/eval).
"""
from __future__ import annotations

import json
from functools import lru_cache

from fastapi import APIRouter

from backend.schemas import MethodologyOut, Tag
from pipeline.config import DATA, MODELS
from pipeline.provenance import load_yaml

router = APIRouter()
EVAL = DATA / "eval"


def _json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _n(v, d=3) -> str:
    return f"{v:.{d}f}".replace(".", ",").replace("-", "−")


def _pct(v) -> str:
    return "—" if v is None else f"{100 * v:.1f}%".replace(".", ",")


def _fp(metrics: dict | None, *classes: str) -> float | None:
    """Доля пикселей классов фона, принятых за мусор (сумма по классам)."""
    if not metrics:
        return None
    fp = metrics.get("fp_by_class") or metrics.get("methods", {}).get("xgb_filters", {}).get("fp_by_class", {})
    rows = [fp[c] for c in classes if c in fp]
    total = sum(r["class_px"] for r in rows)
    return sum(r["fp_px"] for r in rows) / total if total else None


def target() -> list[dict]:
    """Целевая величина по профилям: что, в каких единицах, какого размера, каким методом измерено."""
    out = []
    for pid, p in load_yaml("profiles.yaml")["profiles"].items():
        if not p.get("show_in_ui", True):
            continue
        out.append({
            "profile": pid, "symbol": "C", "quantity": p["label"], "unit": p["unit"],
            "definition": f"число плавающих предметов ({p['material']}) крупнее порога размера на 1 км² "
                          "морской поверхности, осреднённое по обследованной полосе",
            "size_class": p["size_class"], "method": p["method"], "primary": bool(p.get("primary")),
        })
    return out


def steps() -> list[dict]:
    """Методы и формулы по шагам — с коэффициентами моделей сервиса."""
    a = _json(MODELS / "conc_A.json")
    b = _json(MODELS / "conc_B.json")
    det = load_yaml("detector.yaml")
    pairs = load_yaml("pairs.yaml")["drift"]
    out = [
        {"id": "field", "title": "Полевая концентрация — эталон",
         "method": "метод ленточного трансекта (strip transect): подсчёт предметов в полосе фиксированной ширины",
         "formula": ["C = N / A,   A = L · W",
                     "95% ДИ (Гарвуд, точный пуассоновский): [ χ²(0,025; 2N) / 2 ;  χ²(0,975; 2N + 2) / 2 ] / A"],
         "legend": "N — число предметов в полосе, L — длина полосы, км, W — ширина полосы, км (10 м = 0,01 км). "
                   "Пример постановки: 12 предметов на 0,20 км² → 60 шт./км²",
         "code": "pipeline/measure.py"},
        {"id": "background", "title": "Нормализация фона снимка",
         "method": "вычитание локального спектра воды (медиана по блокам ~320 м, два прохода)",
         "formula": ["ρ̃_b = ρ_b − медиана_вода,320 м(ρ_b) + ρ_b^эталон"],
         "legend": "ρ_b — отражение в канале b Sentinel-2; эталон — медианный спектр чистой воды MARIDA. "
                   "Снимает блик, дымку и разницу атмосферной коррекции",
         "code": "pipeline/features.py"},
        {"id": "indices", "title": "Спектральные индексы",
         "method": "индекс плавающего мусора FDI (Biermann et al., 2020), FAI (Hu, 2009), NDVI, PI, NDWI, NDMI",
         "formula": ["FDI = R₈ − [R₆ + (R₁₁ − R₆) · (λ₈ − λ₄) / (λ₁₁ − λ₄) · 10]",
                     "NDVI = (R₈ − R₄) / (R₈ + R₄),   PI = R₈ / (R₈ + R₄)"],
         "legend": "R — отражение в каналах B04, B06, B08, B11; λ — центральные длины волн, нм",
         "code": "pipeline/features.py"},
        {"id": "classifier", "title": "Детектор: вероятность мусора в пикселе 10 м",
         "method": "градиентный бустинг XGBoost, 6 классов (мусор, органика, судно, облако, вода, пена) на MARIDA",
         "formula": ["P(мусор | x) = softmax( Σ_t f_t(x) )_мусор,   x — 20 признаков пикселя",
                     f"детекция: P ≥ {_n(det['p_det'], 2)}  ∧  Δρ₈ > Δρ₂ (не пена)  ∧  ≥2 соседа с P > 0,3  ∧  "
                     "Δρ₈ / σ_шум ≥ 5 (CFAR)"],
         "legend": "Δρ — аномалия относительно воды; σ_шум — локальный шум NIR в окне 510 м. Порог P задан до "
                   "проверки на test",
         "code": "pipeline/detect.py, pipeline/aggregate.py"},
        {"id": "cover", "title": "Покрытие мусором (вспомогательно, м²)",
         "method": "линейное спектральное смешение «вода + плотное скопление»",
         "formula": ["f = clip( ⟨Δρ, Δe⟩ / ⟨Δe, Δe⟩ , 0, 1 )  по каналам B05–B12",
                     "S_зоны = Σ f · 100 м²,   покрытие гекса = S / площадь видимой воды (м²/км²)"],
         "legend": "Δe — спектр плотного скопления MARIDA минус вода. В шт./км² не переводится: связь с полевой "
                   "плотностью не подтверждена",
         "code": "pipeline/detect.py"},
    ]
    if a:
        s = a["serve"]
        (b0, b1, b2), (mlat, mlon), (slat, slon) = s["beta"], s["mu"], s["sd"]
        out.append({
            "id": "model_A", "title": "Модель концентрации, профиль A",
            "method": "отрицательно-биномиальная регрессия (GLM) числа предметов со смещением ln A",
            "formula": ["N ~ NegBin(μ, α),   ln μ = ln A + β₀ + β₁·z_lat + β₂·z_lon",
                        f"Ĉ = exp({_n(b0)} + {_n(b1)}·z_lat + {_n(b2)}·z_lon),   α = {_n(s['alpha'])}",
                        f"z_lat = (lat − {_n(mlat, 2)}) / {_n(slat, 2)},   z_lon = (lon − ({_n(mlon, 2)})) / {_n(slon, 2)}"],
            "legend": f"обучение {len(a['trained_on'])} событий S2, отложено {len(a['holdout'])}; признаки выбраны "
                      "по групповой CV",
            "code": "pipeline/concentration.py", "model_version": a["version"]})
    if b:
        s = b["serve"]
        (b0, b1), (mu,), (sd,) = s["beta"], s["mu"], s["sd"]
        out.append({
            "id": "model_B", "title": "Модель концентрации, профиль B",
            "method": f"гребневая (ridge, α = {_n(s['ridge_alpha'], 1)}) регрессия ln(C + 1) по расстоянию до берега",
            "formula": [f"ln(Ĉ + 1) = {_n(b0)} {'−' if b1 < 0 else '+'} {_n(abs(b1))} · z",
                        f"z = (ln(1 + d) − {_n(mu)}) / {_n(sd)},   d — расстояние до берега, км"],
            "legend": f"обучение {len(b['trained_on'])} событий S4, отложено {len(b['holdout'])}; концентрация "
                      "падает с удалением от берега",
            "code": "pipeline/concentration.py", "model_version": b["version"]})
    q = (b or a or {}).get("interval_log_quantiles", {}).get("0.8")
    out += [
        {"id": "interval", "title": "Интервалы неопределённости",
         "method": "сплит-конформное предсказание в лог-шкале по внефолдовым остаткам групповой CV",
         "formula": ["[ (Ĉ + 1)·e^q_lo − 1 ;  (Ĉ + 1)·e^q_hi − 1 ],   q — квантили остатков ln(C + 1) − ln(Ĉ + 1)"]
                    + ([f"профиль B, 80%: q_lo = {_n(q[0], 2)}, q_hi = {_n(q[1], 2)} — то есть ×{_n(2.718281828 ** q[0], 2)}…"
                        f"×{_n(2.718281828 ** q[1], 2)} от оценки"] if b and q else []),
         "legend": "покрытие интервалов проверено на отложенной выборке (вкладка «Проверка»)",
         "code": "pipeline/concentration.py"},
        {"id": "status", "title": "Статус оценки (область применимости)",
         "method": "формальные правила по обучающим данным модели",
         "formula": ["«модельная оценка»: в бассейне профиля ∧ ≤ 60 км до обучающего события ∧ тот же месяц ∧ "
                     "признаки в диапазоне обучения ± 10%",
                     "«исследовательская оценка»: в бассейне, но хотя бы одно условие не выполнено",
                     "«концентрация недоступна»: другой бассейн или пресные воды"],
         "legend": "у детекции свои статусы: «не обнаружено» — только при ≥ 50% видимой воды, без блика и в "
                   "спокойное море, иначе «недостаточно данных»",
         "code": "pipeline/concentration.py, pipeline/aggregate.py"},
    ]
    f = _json(MODELS / "fusion_B.json") or _json(MODELS / "fusion_A.json")
    if f:
        vg = f["variogram"]
        out.append({
            "id": "fusion", "title": "Сведение модели и полевых измерений",
            "method": "регрессионный кригинг остатков модели в пространстве-времени (простой кригинг)",
            "formula": ["ẑ(x₀) = m(x₀) + Σ wᵢ·rᵢ,   z = ln(C + 1),   rᵢ = zᵢ − m(xᵢ) — остаток модели у измерения i",
                        "w = K⁻¹k,   K_ij = s·e^(−h_ij/ρ) + δ_ij(τ² + σᵢ²),   k_i = s·e^(−h_i0/ρ)",
                        "h = √(d² + (v·Δt)²),   σ²(x₀) = s + τ² − kᵀK⁻¹k",
                        f"профиль {f['profile']}: s = {_n(vg['psill'], 2)}, τ² = {_n(vg['nugget'], 2)}, "
                        f"ρ = {vg['range_km']:.0f} км, v = {f['v_km_day']:.0f} км/сут"],
            "legend": "d — расстояние, Δt — давность измерения; сутки давности весят как v км расстояния "
                      f"(v = {pairs['current_ms']} м/с течения + {pairs['windage']:.0%} ветра — дрейфовый буфер "
                      "реестра пар). σᵢ² — счётный шум измерения ≈ N / (N + A)². Параметры подобраны "
                      "кросс-валидацией на обучающей части",
            "code": "pipeline/fusion.py"})
    out += [
        {"id": "drift", "title": "Прогноз дрейфа на 72 ч",
         "method": "лагранжев ансамбль частиц, интегрирование Рунге — Кутты 2-го порядка, шаг 30 мин",
         "formula": ["dx/dt = u_течение(x, t) + α·u_ветер10м(x, t) + √(2K)·ξ(t)",
                     "α ~ U(1%, 3%) на частицу (парусность),   K = 5 м²/с,   ξ — белый шум"],
         "legend": "течения SMOC (с приливом и стоксовым дрейфом), ветер ERA5 / прогноз Open-Meteo; частица, "
                   "коснувшаяся суши, выброшена на берег. Без снимка — реанализы Copernicus Marine",
         "code": "pipeline/drift.py"},
        {"id": "metrics", "title": "Метрики качества",
         "method": "пиксельные метрики детектора и ошибки концентрации",
         "formula": ["P = TP / (TP + FP),   R = TP / (TP + FN),   F1 = 2PR / (P + R),   IoU = TP / (TP + FP + FN)",
                     "MAE = (1/n)·Σ |Cᵢ − Ĉᵢ|,   RMSE = √((1/n)·Σ (Cᵢ − Ĉᵢ)²)   (шт./км²)",
                     "ошибка ln = (1/n)·Σ |ln(Ĉᵢ + 1) − ln(Cᵢ + 1)|   (e^ошибка — типичная ошибка «во сколько раз»)"],
         "legend": "95% ДИ метрик детектора — бутстреп по сценам; ДИ MAE и разности MAE с базовыми на той же "
                   "выборке — бутстреп по группам событий. Основная и базовые модели считаются на одних и тех же "
                   "событиях; пересчёт из сохранённых предсказаний — python -m pipeline.verify",
         "code": "pipeline/eval_detector.py, pipeline/measure.py"},
    ]
    return out


def objects() -> list[dict]:
    """Какие объекты снимок может спутать с мусором и как сервис их отделяет — с ошибкой на размеченных данных."""
    m = _json(EVAL / "detector" / "metrics.json")
    md = _json(EVAL / "detector" / "mados.json")
    row = lambda obj, how, marida, mados, status, note="": {  # noqa: E731
        "object": obj, "how": how, "marida_fp": marida, "mados_fp": mados, "status": status, "note": note}
    return [
        row("Пена и барашки", "класс «пена, волны» в модели; тест: пена ярче в видимом, чем в NIR; при ветре ≥ 8 м/с "
            "сцена ненадёжна", _fp(m, "Foam"), None, "различаем"),
        row("Волны, рябь, кильватер", "класс модели; CFAR: аномалия NIR ≥ 5σ локального шума; буфер 400 м от судов",
            _fp(m, "Waves", "Wakes"), _fp(md, "Waves & Wakes"), "различаем"),
        row("Солнечный блик", "нормализация фона; CFAR поднимает порог; >30% воды под бликом — сцена ненадёжна, "
            "только скопления от 4 пикселей", None, None, "различаем",
            "проверяется маской качества (код «сильный блик»)"),
        row("Суда", "класс «судно» в модели; постоянные объекты по ряду снимков", _fp(m, "Ship"), _fp(md, "Ship"),
            "различаем"),
        row("Водоросли и саргассум", "класс «органика» в модели; признаки NDVI и FAI",
            _fp(m, "Dense Sargassum", "Sparse Sargassum"), _fp(md, "Dense Sargassum", "Sparse Floating Algae"),
            "различаем"),
        row("Плавник и природная органика", "класс «органика» — спектрально близок к мусору",
            _fp(m, "Natural Organic Material"), _fp(md, "Natural Organic Material"), "частично",
            "13 из 49 пикселей MARIDA test приняты за мусор — главный источник ошибок"),
        row("Облака, тени, дымка", "маска SCL + буфер 100 м; класс «облако»", _fp(m, "Clouds", "Cloud Shadows"), None,
            "различаем"),
        row("Мутная вода, взвесь", "нормализация фона; класс «вода»", _fp(m, "Sediment-Laden Water", "Turbid Water"),
            _fp(md, "Sediment-Laden Water", "Turbid Water"), "различаем"),
        row("Нефтяные плёнки", "класса нефти в обучении нет; плёнка тёмная в NIR, а мусору нужна положительная "
            "аномалия NIR", None, _fp(md, "Oil Spill"), "не выделяем",
            "допущение: нефть сервис не ищет и не отличает от воды"),
        row("Нефтяные эмульсии, мазут", "отдельно не выделяются; плавающие комки мазута могут дать срабатывание", None,
            None, "не выделяем", "ограничение: пример — Керченский пролив, 2025; подтверждение судном"),
        row("Нефтяные платформы, причалы, буи", "маска постоянных объектов: срабатывание на ≥3 снимках ряда", None,
            _fp(md, "Oil Platform"), "различаем"),
        row("Морская слизь, медузы", "класса в обучении нет", None, _fp(md, "Sea snot", "Jellyfish"),
            "не различаем", "ограничение: 45% пикселей слизи и 38% медуз на MADOS — нужна проверка судном или дроном"),
    ]


def fusion_rules() -> list[dict]:
    """Как сервис разбирает расхождения источников и кому доверяет больше."""
    pairs = load_yaml("pairs.yaml")
    d = pairs["drift"]
    return [
        {"case": "Снимок и судовой журнал видят пятно со сдвигом по месту и времени",
         "rule": f"Одно пятно, если расстояние ≤ R = r_следа + |Δt|·v, v = {d['current_ms']} м/с + "
                 f"{d['windage']:.0%} ветра ERA5 (дрейфовый буфер). Пары со сдвигом > "
                 f"{pairs['sync']['tier_b_max_h']} ч — только контекст, в проверку не идут"},
        {"case": "Спутник видит скопление, в поле его не измеряли или не нашли",
         "rule": "Зона остаётся «обнаружено» — это ответ на вопрос «где искать». В шт./км² площадь маски не "
                 "переводится; проверка — маршрут судна с поправкой на дрейф"},
        {"case": "В поле мусор есть, спутник его не видит",
         "rule": "Не противоречие: рассеянный мусор (десятки–сотни предметов по ~0,01 м² на км²) занимает меньше 1% "
                 "пикселя 10 м, а детектор видит скопления от ~20–30% пикселя. «Не обнаружено» ≠ «мусора нет»"},
        {"case": "Число шт./км²: модель против свежего измерения",
         "rule": "Регрессионный кригинг: вес измерения тем больше, чем оно ближе, свежее и точнее (меньше счётный шум); "
                 "соседние измерения одного разреза не считаются независимыми. Без измерений рядом — модель"},
        {"case": "Два измерения рядом расходятся",
         "rule": "Оба входят с весами по ковариации; разброс между ними попадает в наггет τ² и расширяет интервал"},
        {"case": "Снимок ненадёжен (облака, блик, шторм, лёд)",
         "rule": "Гексы — «недостаточно данных», отсутствие мусора не подтверждается; сцена не входит в ряды "
                 "устойчивости и тренда. Свежий надёжный снимок важнее старого: статус берётся по дате снимка"},
    ]


@lru_cache(maxsize=4)
def _build(stamp: tuple) -> dict:
    fusion = {}
    for pid in ("A", "B"):
        e = _json(EVAL / "fusion" / f"{pid}_metrics.json")
        if e:
            fusion[pid] = {"model": e["model"], "scenarios": e["scenarios"], "n_holdout": e["n_holdout"],
                           "variogram": e["variogram"], "v_km_day": e["v_km_day"]}
    return {"target": target(), "steps": steps(), "objects": objects(), "fusion_rules": fusion_rules(),
            "fusion_eval": fusion}


def methodology() -> dict:
    files = [MODELS / f"{n}.json" for n in ("conc_A", "conc_B", "fusion_A", "fusion_B")] + [
        EVAL / "detector" / "metrics.json", EVAL / "detector" / "mados.json"]
    return _build(tuple(p.stat().st_mtime if p.exists() else 0 for p in files))


@router.get("/api/methodology", tags=[Tag.METRICS], summary="Методика: целевая величина, формулы, различение объектов",
            response_model=MethodologyOut)
def methodology_api():
    """Всё, что панель «Методика» показывает без чтения документов: целевая величина по профилям (что, единица,
    размер, метод), методы и формулы по шагам с текущими коэффициентами моделей, какие объекты снимок может
    спутать с мусором и как сервис их отделяет (с долей ошибок на MARIDA test и MADOS), правила сведения
    противоречивых источников и проверка сведения на отложенной выборке."""
    return methodology()
