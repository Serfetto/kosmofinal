"""Реестр данных: configs/datasets.yaml → docs/datasets.md.

python -m pipeline.datasets          # пересобрать docs/datasets.md

Один источник для документа, GET /api/datasets и вкладки «Данные» в панели «Методика».
"""
from __future__ import annotations

from .config import ROOT
from .provenance import load_yaml

DOC = ROOT / "docs" / "datasets.md"
ROLE = {"train": "обучение", "eval": "проверка", "feature": "признак модели", "input": "вход сервиса",
        "display": "отображение"}


def load() -> list[dict]:
    return load_yaml("datasets.yaml")["datasets"]


def _cell(s) -> str:
    return str(s or "—").replace("|", "\\|").replace("\n", " ")


def render() -> str:
    ds = load()
    out = ["# Реестр данных", "",
           "Все наборы данных, которые использует AquaFlow: откуда они, на каких условиях, какие поля мы берём и "
           "зачем. Документ собирается из [configs/datasets.yaml](../configs/datasets.yaml) командой "
           "`python -m pipeline.datasets`; тот же реестр отдаёт `GET /api/datasets` и показывает вкладка "
           "«Данные» в панели «Методика». Как данные преобразуются — [data.md](data.md).", "",
           "## Сводка", "",
           "| Набор | Роль | Лицензия | Доступ |", "|---|---|---|---|"]
    for d in ds:
        roles = ", ".join(ROLE[r] for r in d["role"])
        out.append(f"| [{_cell(d['name'])}](#{d['id']}) | {roles} | {_cell(d['license'])} | {_cell(d['access'])} |")
    out.append("")
    for d in ds:
        out += [f'<a id="{d["id"]}"></a>', "", f"## {d['name']}", ""]
        rows = [("Поставщик", d.get("provider")), ("Версия", d.get("version")), ("Доступ", d.get("access")),
                ("Где лежит", d.get("local")), ("Лицензия", d.get("license")), ("Условия", d.get("terms")),
                ("Роль", ", ".join(ROLE[r] for r in d["role"])), ("Зачем", d.get("used_for")),
                ("Код", d.get("code"))]
        out += ["| | |", "|---|---|"] + [f"| {k} | {_cell(v)} |" for k, v in rows if v]
        if d.get("fields"):
            out += ["", "| Поле | Тип / единица | Что это и как используем |", "|---|---|---|"]
            out += [f"| `{_cell(f['name'])}` | {_cell(f['type'])} | {_cell(f['desc'])} |" for f in d["fields"]]
        if d.get("notes"):
            out += ["", d["notes"]]
        out.append("")
    return "\n".join(out)


def main() -> None:
    DOC.write_text(render(), encoding="utf-8")
    print(f"{DOC.relative_to(ROOT)}: {len(load())} наборов")


if __name__ == "__main__":
    main()
