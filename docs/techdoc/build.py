"""Сборка PDF технической документации из techdoc.html.

python docs/techdoc/build.py            # → docs/AquaFlow_TechDoc.pdf

Нужны playwright и pymupdf (pip install playwright pymupdf) и установленный Google Chrome
(или `playwright install chromium` и BROWSER_CHANNEL= пустой). Два прохода: первый узнаёт, на каких
страницах стоят разделы, второй вписывает номера страниц в оглавление.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pymupdf
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
SRC = HERE / "techdoc.html"
OUT = HERE.parent / "AquaFlow_TechDoc.pdf"
CHANNEL = os.environ.get("BROWSER_CHANNEL", "chrome") or None

FOOTER = """<div style="width:100%; font: 7.5pt 'Segoe UI', Arial, sans-serif; color:#7a8a85; padding:0 15mm;
 display:flex; justify-content:space-between;"><span>AquaFlow · техническая документация</span>
 <span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>"""
PDF = dict(format="A4", print_background=True, display_header_footer=True, header_template="<span></span>",
           footer_template=FOOTER, margin=dict(top="14mm", bottom="16mm", left="15mm", right="15mm"))


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def heading_pages(pdf: Path, headings: dict[str, str], skip: int) -> dict[str, int]:
    """Страница, на которой стоит метка раздела (номера с 1)."""
    doc = pymupdf.open(pdf)
    texts = [norm(p.get_text()) for p in doc]
    found = {}
    for ref, title in headings.items():
        key = norm(title)
        for i in range(skip, len(texts)):
            if key in texts[i]:
                found[ref] = i + 1
                break
    return found


def main() -> None:
    tmp = HERE / "_pass1.pdf"
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=CHANNEL, headless=True)
        page = browser.new_page()
        page.goto(SRC.as_uri())
        page.wait_for_load_state("networkidle")
        # Невидимые метки у разделов из оглавления: по ним первый проход находит страницы
        refs = page.evaluate("""() => [...document.querySelectorAll('#toc a[data-ref]')].map(a => {
            const m = document.createElement('span');
            m.className = 'toc-mark'; m.textContent = 'TOCMARK' + a.dataset.ref.replace(/-/g, 'x') + 'END';
            m.style.cssText = 'font-size:1px;color:#fff;position:absolute';
            document.getElementById(a.dataset.ref).prepend(m);
            return a.dataset.ref;
        })""")
        page.emulate_media(media="print")
        page.pdf(path=str(tmp), **PDF)
        headings = {r: "TOCMARK" + r.replace("-", "x") + "END" for r in refs}
        pages = heading_pages(tmp, headings, skip=2)
        missing = sorted(set(headings) - set(pages))
        if missing:
            print("не найдены на страницах:", missing, file=sys.stderr)
        page.evaluate("() => document.querySelectorAll('.toc-mark').forEach(m => m.remove())")
        page.evaluate("""(pages) => { for (const a of document.querySelectorAll('#toc a[data-ref]')) {
            const n = pages[a.dataset.ref]; if (n) a.querySelector('.p').textContent = n; } }""", pages)
        page.emulate_media(media="print")
        page.pdf(path=str(OUT), **PDF)
        browser.close()
    tmp.unlink(missing_ok=True)
    doc = pymupdf.open(OUT)
    doc.set_metadata({"title": "AquaFlow — техническая документация", "author": "Команда AquaFlow",
                      "subject": "КосмоХакатон 2026: детектирование и оценка концентрации макропластика"})
    doc.saveIncr()
    print(f"{OUT} — {len(doc)} стр., {OUT.stat().st_size / 1e6:.1f} МБ")


if __name__ == "__main__":
    main()
