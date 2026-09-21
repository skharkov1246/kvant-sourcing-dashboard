#!/usr/bin/env python3
"""Вопросы заказчику отдельным документом — тем, что прикладывают к письму.

ЗАЧЕМ ОТДЕЛЬНЫЙ ДОКУМЕНТ. Письмо заказчику в отчёте ссылается на приложение
«Вопросы заказчику», а приложения не было: вопросы жили таблицей внутри справки
на защиту, вперемешку с нашей внутренней работой. Заказчику такое не
отправляют: там и наши замеры, и наши сомнения в собственных данных.

ЧТО ЗДЕСЬ ЕСТЬ. Каждый вопрос четырьмя частями: по какой позиции, что именно
не сходится, что мы просим подтвердить, и что нам УЖЕ известно — последнее
важнее всего. Вопрос без «вот что мы выяснили сами» читается как перекладывание
работы; вопрос, к которому приложен разбор, читается как работа, упёршаяся в
данные, которых у нас физически нет.

ПОРЯДОК — ПО ДЕНЬГАМ. Сначала то, где цена ошибки больше. Заказчик отвечает
сверху вниз и закрывает крупное раньше мелкого.

ЧЕГО ЗДЕСЬ НЕТ. Наших цен, наших поставщиков и наших расчётов прибыли. Только
то, что относится к предмету поставки.

    python gt/tools/questions_doc.py
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "gt/data/ship_questions.json"
OUT = ROOT / "gt/docs"
CHROME = ["/opt/pw-browsers/chromium/chrome-linux/chrome",
          "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
          "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]

CSS = """
@page { size: A4 portrait; margin: 14mm 12mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 9pt; color: #111; margin: 0; }
h1 { font-size: 15pt; margin: 0 0 2mm; }
h2 { font-size: 10.5pt; margin: 6mm 0 2mm; border-bottom: 1.2pt solid #111;
     padding-bottom: 0.8mm; page-break-after: avoid; }
p { margin: 0 0 2.5mm; line-height: 1.5; }
.dim { color: #555; }
.q { border: 0.8pt solid #111; padding: 3mm; margin-bottom: 3.5mm;
     page-break-inside: avoid; }
.q .head { font-weight: bold; font-size: 9.6pt; margin-bottom: 1.5mm; }
.q .kind { color: #555; font-size: 8.4pt; margin-bottom: 2mm; }
.q .lbl { font-weight: bold; }
.q .ask { margin-bottom: 2mm; }
.q .known { color: #333; font-size: 8.6pt; }
.q .cost { color: #333; font-size: 8.6pt; margin-top: 2mm; border-top: 0.3pt solid #bbb;
           padding-top: 1.5mm; }
"""


def E(x) -> str:
    return html.escape(str(x if x is not None else ""))


def ru(n) -> str:
    try:
        v = float(n)
    except (TypeError, ValueError):
        return E(n)
    s = f"{v:,.0f}" if abs(v - round(v)) < 0.005 else f"{v:,.2f}"
    return s.replace(",", " ").replace(".", ",")


def money_in(text: str) -> float:
    """Цена вопроса — наибольшее число долларов, названное в его же пояснении.

    Порядок вопросов должен считаться, а не назначаться. Числа берутся из
    текста поля «цена вопроса», который пишется руками: это единственное место,
    где стоимость ошибки по строке уже оценена инженером.
    """
    best = 0.0
    for m in re.finditer(r"(\d[\d\s ]*(?:[.,]\d+)?)\s*USD", str(text or "")):
        try:
            v = float(m.group(1).replace(" ", "").replace(" ", "").replace(",", "."))
        except ValueError:
            continue
        best = max(best, v)
    return best


def build() -> str:
    qs = json.loads(SRC.read_text(encoding="utf-8"))["questions"]
    qs = sorted(qs, key=lambda q: -money_in(q.get("cost")))
    out: list[str] = []
    a = out.append
    a("<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
      "<title>Вопросы заказчику по заявке</title><style>" + CSS + "</style></head><body>")
    a("<h1>Вопросы заказчику по заявке на запасные части</h1>")
    a(f"<p class='dim'>Приложение к письму об уточнении позиций заявки. Вопросов "
      f"{ru(len(qs))}. Порядок — по цене вопроса: сначала то, где ошибка дороже. По каждой "
      f"позиции указано, что именно не сходится, что мы просим подтвердить и что нам уже "
      f"удалось установить самостоятельно.</p>")
    a("<p class='dim'>Просим ответить по форме: обозначение позиции — уточнение. Если по "
      "какой-то позиции ответ требует времени, достаточно сказать об этом: мы продолжим "
      "работу по остальным и не будем ставить всю заявку в ожидание.</p>")
    for i, q in enumerate(qs, 1):
        a("<div class='q'>")
        a(f"<div class='head'>{ru(i)}. {E(q.get('pn'))}"
          f"{' — ' + ru(q.get('qty')) + ' ' + E(q.get('unit')) if q.get('qty') else ''}</div>")
        a(f"<div class='kind'>Что не сходится: {E(q.get('kind'))}</div>")
        a(f"<p class='ask'><span class='lbl'>Просим подтвердить.</span> {E(q.get('ask'))}</p>")
        if q.get("known"):
            a(f"<p class='known'><span class='lbl'>Что мы установили сами.</span> "
              f"{E(q.get('known'))}</p>")
        if q.get("cost"):
            a(f"<p class='cost'><span class='lbl'>Почему это важно.</span> "
              f"{E(q.get('cost'))}</p>")
        a("</div>")
    a("</body></html>")
    return "\n".join(out)


def main() -> int:
    doc = build()
    if doc.count("<div class='q'>") < 5:
        print("вопросов в наборе почти нет — документ не собран", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    hp = OUT / "ВОПРОСЫ-ЗАКАЗЧИКУ-ЛУКОЙЛ.html"
    pp = OUT / "ВОПРОСЫ-ЗАКАЗЧИКУ-ЛУКОЙЛ.pdf"
    hp.write_text(doc, encoding="utf-8")
    exe = next((c for c in CHROME if Path(c).exists()), None)
    if not exe:
        print("Chromium не найден — документ не собран", file=sys.stderr)
        return 1
    subprocess.run(
        [exe, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
         "--run-all-compositor-stages-before-draw", "--virtual-time-budget=180000",
         f"--print-to-pdf={pp}", hp.as_uri()], check=True, capture_output=True)
    print(f"{pp.name}: вопросов {doc.count(chr(60) + 'div class=' + chr(39) + 'q' + chr(39))}, "
          f"{pp.stat().st_size / 1e6:.1f} МБ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
