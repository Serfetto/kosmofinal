"""Статусы результата — единый словарь для алгоритмов, API, интерфейса и выгрузки."""

# Статус детекции (гекс, зона, дата)
DETECTED, NOT_DETECTED, INSUFFICIENT = "detected", "not_detected", "insufficient_data"
DETECTION = {
    DETECTED: "обнаружено",
    NOT_DETECTED: "не обнаружено",
    INSUFFICIENT: "недостаточно данных",
}
DETECTION_CODE = {NOT_DETECTED: 0, DETECTED: 1, INSUFFICIENT: 2}

# Статус концентрации (профиль)
MODEL, RESEARCH, UNAVAILABLE = "model_estimate", "research_estimate", "unavailable"
CONCENTRATION = {
    MODEL: "модельная оценка",
    RESEARCH: "исследовательская оценка",
    UNAVAILABLE: "концентрация недоступна",
}
CONCENTRATION_CODE = {UNAVAILABLE: 0, MODEL: 1, RESEARCH: 2}

# Тип значения
VALUE_TYPE = {
    "measurement": "измерение",
    "model_estimate": "модельная оценка",
    "research_estimate": "исследовательская оценка",
    "unavailable": "нет оценки",
}

# Коды маски качества (quality.png) — пригодность пикселя, отдельно от принадлежности классу «мусор»
QUALITY = {
    0: ("ok", "пригоден", (0, 0, 0, 0)),
    1: ("land", "суша / вне маски воды", (0, 0, 0, 0)),
    2: ("nodata", "нет данных", (90, 90, 90, 170)),
    3: ("cloud", "облако", (235, 235, 245, 190)),
    4: ("shadow", "тень облака", (40, 40, 60, 170)),
    5: ("cirrus", "перистые облака", (190, 200, 255, 150)),
    6: ("glint", "сильный блик", (255, 214, 0, 150)),
    7: ("ice", "лёд / шуга", (120, 230, 255, 170)),
    8: ("ship", "судно / кильватер", (255, 140, 0, 170)),
    9: ("static", "постоянный объект", (180, 90, 255, 170)),
}
