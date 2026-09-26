"""Сборка PDF технической документации.

python docs/techdoc/build.py            # обе версии
python docs/techdoc/build.py brief      # только краткая

techdoc.html → docs/AquaFlow_TechDoc.pdf (полная), brief.html → docs/AquaFlow_TechDoc_Brief.pdf (краткая, 5 стр.).
Общий стиль — style.css; схема архитектуры берётся из полной версии и подставляется в краткую вместо <!--ARCH-->.

Нужны playwright и pymupdf (pip install playwright pymupdf) и установленный Google Chrome
(или `playwright install chromium` и BROWSER_CHANNEL= пустой). Если в документе есть оглавление (#toc), сборка
идёт в два прохода: первый узнаёт, на каких страницах стоят разделы, второй вписывает номера страниц.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pymupdf
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
DOCS = {
    "full": ("techdoc.html", "AquaFlow_TechDoc.pdf", "AquaFlow · техническая документация"),
    "brief": ("brief.html", "AquaFlow_TechDoc_Brief.pdf", "AquaFlow · краткая техническая документация"),
}
CHANNEL = os.environ.get("BROWSER_CHANNEL", "chrome") or None


def pdf_options(title: str) -> dict:
    footer = f"""<div style="width:100%; font: 7.5pt 'Segoe UI', Arial, sans-serif; color:#7a8a85; padding:0 15mm;
 display:flex; justify-content:space-between;"><span>{title}</span>
 <span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>"""
    return dict(format="A4", print_background=True, display_header_footer=True, header_template="<span></span>",
                footer_template=footer, margin=dict(top="14mm", bottom="16mm", left="15mm", right="15mm"))


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def marker_pages(pdf: Path, markers: dict[str, str], skip: int) -> dict[str, int]:
    """Страница, на которой стоит метка раздела (номера с 1)."""
    doc = pymupdf.open(pdf)
    texts = [norm(p.get_text()) for p in doc]
    found = {}
    for ref, mark in markers.items():
        key = norm(mark)
        for i in range(skip, len(texts)):
            if key in texts[i]:
                found[ref] = i + 1
                break
    return found


def source(name: str) -> Path:
    """HTML для печати; в краткую версию подставляется схема архитектуры из полной."""
    src = HERE / name
    html = src.read_text(encoding="utf-8")
    if "<!--ARCH-->" not in html:
        return src
    full = (HERE / DOCS["full"][0]).read_text(encoding="utf-8")
    a = full.index('<svg viewBox="0 0 736 440"')
    b = full.index("</svg>", a) + len("</svg>")
    tmp = HERE / f"_{name}"
    tmp.write_text(html.replace("<!--ARCH-->", full[a:b]), encoding="utf-8")
    return tmp


def build(page, key: str) -> None:
    name, out_name, title = DOCS[key]
    out = HERE.parent / out_name
    opts = pdf_options(title)
    src = source(name)
    page.goto(src.as_uri())
    page.wait_for_load_state("networkidle")
    page.emulate_media(media="print")
    if page.locator("#toc").count():
        tmp = HERE / "_pass1.pdf"
        # Невидимые метки у разделов из оглавления: по ним первый проход находит страницы
        refs = page.evaluate("""() => [...document.querySelectorAll('#toc a[data-ref]')].map(a => {
            const m = document.createElement('span');
            m.className = 'toc-mark'; m.textContent = 'TOCMARK' + a.dataset.ref.replace(/-/g, 'x') + 'END';
            m.style.cssText = 'font-size:1px;color:#fff;position:absolute';
            document.getElementById(a.dataset.ref).prepend(m);
            return a.dataset.ref;
        })""")
        page.pdf(path=str(tmp), **opts)
        pages = marker_pages(tmp, {r: "TOCMARK" + r.replace("-", "x") + "END" for r in refs}, skip=2)
        tmp.unlink(missing_ok=True)
        missing = sorted(set(refs) - set(pages))
        if missing:
            print("не найдены на страницах:", missing, file=sys.stderr)
        page.evaluate("() => document.querySelectorAll('.toc-mark').forEach(m => m.remove())")
        page.evaluate("""(pages) => { for (const a of document.querySelectorAll('#toc a[data-ref]')) {
            const n = pages[a.dataset.ref]; if (n) a.querySelector('.p').textContent = n; } }""", pages)
    page.pdf(path=str(out), **opts)
    if src.name.startswith("_"):
        src.unlink(missing_ok=True)
    doc = pymupdf.open(out)
    doc.set_metadata({"title": title.replace("AquaFlow · ", "AquaFlow — "), "author": "Команда AquaFlow",
                      "subject": "КосмоХакатон 2026: детектирование и оценка концентрации макропластика"})
    doc.saveIncr()
    print(f"{out} — {len(doc)} стр., {out.stat().st_size / 1e6:.1f} МБ")


def main() -> None:
    keys = [a for a in sys.argv[1:] if a in DOCS] or list(DOCS)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=CHANNEL, headless=True)
        page = browser.new_page()
        for key in keys:
            build(page, key)
        browser.close()


if __name__ == "__main__":
    main()
