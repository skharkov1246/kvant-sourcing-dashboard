#!/usr/bin/env python3
"""Расчётная записка ОВЭ-75 (Word) из ove/data/calc.json.

Переиспользует низкоуровневые OOXML-хелперы build_docx.py (esc/run/p/cell/table).
Выход: ove/public/docs/ove75-raschet.docx. Вызывается из ove/build.py.
"""
import json
import zipfile
from datetime import date
from pathlib import Path

import build_docx as bd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "public" / "docs" / "ove75-raschet.docx"

V_RU = {"ok": "сходится", "warn": "в допуске", "question": "ВОПРОС"}
V_COLOR = {"ok": "1F7A1F", "warn": "B8860B", "question": "B22222"}
SEV_RU = {"info": "к сведению", "minor": "уточнить", "major": "ВАЖНО"}


def num(x):
    if x is None:
        return ""
    a = abs(x)
    nd = 0 if a >= 1000 else (1 if a >= 10 else (2 if a >= 1 else 3))
    return f"{x:,.{nd}f}".replace(",", " ").replace(".", ",")


def block_xml(b) -> str:
    parts = []
    lot = f"Лот №{b['lot']}" if b["lot"] else "Комплекс"
    parts.append(bd.p([bd.run(f"{b['id']} · {b['title']} ({lot})", bold=True, size=26)], style="Heading2"))
    parts.append(bd.p([bd.run("Входные данные: ", bold=True)] +
                      [bd.run(" · ".join(f"{i['n']}: {i['v']} [{i['src']}]" for i in b["inputs"]), size=19)]))
    for s in b["steps"]:
        parts.append(bd.p([bd.run(f"{s['d']}:  ", bold=True, size=20), bd.run(s["f"], size=20)]))
    widths = [4000, 1500, 1500, 1100, 1538]
    head = ["Проверка", "Наш расчёт", "ИД", "Δ, %", "Вердикт"]
    rows = [[bd.cell([bd.p([bd.run(h, bold=True, size=19)])], w, shade="EFEFEF")
             for h, w in zip(head, widths)]]
    for c in b["checks"]:
        what = c["what"] + (f" — {c['comment']}" if c.get("comment") else "")
        dev = f"{'+' if c['dev'] > 0 else ''}{str(c['dev']).replace('.', ',')}"
        vals = [
            bd.p([bd.run(what, size=19)]),
            bd.p([bd.run(num(c["ours"]), size=19)]),
            bd.p([bd.run(num(c["idv"]), size=19)]),
            bd.p([bd.run(dev, size=19)]),
            bd.p([bd.run(V_RU[c["verdict"]], bold=True, size=19, color=V_COLOR[c["verdict"]])]),
        ]
        rows.append([bd.cell([v], w) for v, w in zip(vals, widths)])
    parts.append(bd.table(rows, widths))
    parts.append(bd.p([bd.run("Вывод: ", bold=True), bd.run(b["concl"])]))
    parts.append(bd.p([]))
    return "".join(parts)


def build() -> Path:
    calc = json.loads((DATA / "calc.json").read_text(encoding="utf-8"))
    s = calc["summary"]

    body = []
    body.append(bd.p([bd.run("ОВЭ-75 · Расчётная записка (независимый пересчёт КВАНТ)", bold=True, size=32)],
                     style="Heading1"))
    body.append(bd.p([bd.run(f"АО «Кольская ГМК», комплекс «обжиг–выщелачивание–электроэкстракция» 75 000 т/год. "
                             f"Сборка {date.today().isoformat()}. К ТЗ на БИ v3.6 и ИД ч.1/ч.2.", size=20)]))
    body.append(bd.p([bd.run(calc["note"], italic=True, size=19)]))
    body.append(bd.p([bd.run(f"Итого: блоков {s['blocks']}, сверок {s['checks']} "
                             f"(сходится {s['ok']}, в допуске {s['warn']}, вопросов {s['question']}), "
                             f"находок для Гипроникеля {s['flags']}.", bold=True, size=21)]))
    body.append(bd.p([]))

    body.append(bd.p([bd.run("1. Находки — вопросы к ИД", bold=True, size=28)], style="Heading1"))
    for i, f in enumerate(calc["flags"], 1):
        body.append(bd.p([bd.run(f"{i}. [{SEV_RU[f['sev']]}] {f['t']}", bold=True, size=21)]))
        body.append(bd.p([bd.run(f["d"], size=20)]))
    body.append(bd.p([]))

    if calc.get("guarantees"):
        body.append(bd.p([bd.run("2. Гарантийная рамка — консервативные значения для ТКП и договора",
                                 bold=True, size=28)], style="Heading1"))
        body.append(bd.p([bd.run("Правило: гарантируем границу с запасом к расчёту; условия — на сырьё и данные "
                                 "изготовителей; расчётные значения — справочные.", italic=True, size=19)]))
        gw = [2600, 2900, 2900, 1238]
        ghead = ["Параметр", "Расчёт / ИД", "Гарантия (рекомендация)", "Базис"]
        grows = [[bd.cell([bd.p([bd.run(h, bold=True, size=19)])], w, shade="EFEFEF")
                  for h, w in zip(ghead, gw)]]
        for g in calc["guarantees"]:
            vals = [bd.p([bd.run(g["param"], bold=True, size=19)]),
                    bd.p([bd.run(g["calc"], size=19)]),
                    bd.p([bd.run(g["guar"], bold=True, size=19, color="8B0000")]),
                    bd.p([bd.run(g["basis"], size=18)])]
            grows.append([bd.cell([v], w) for v, w in zip(vals, gw)])
        body.append(bd.table(grows, gw))
        body.append(bd.p([]))

    if calc.get("mc"):
        mc = calc["mc"]
        body.append(bd.p([bd.run(f"3. Неопределённость: Монте-Карло {mc['n']:,} прогонов".replace(",", " "),
                                 bold=True, size=28)], style="Heading1"))
        body.append(bd.p([bd.run(mc["note"], italic=True, size=19)]))
        mw = [2700, 1200, 1200, 1200, 1100, 2238]
        mhead = ["Показатель", "P10", "P50", "P90", "Размах", "Что уточнять"]
        mrows = [[bd.cell([bd.p([bd.run(h, bold=True, size=19)])], w, shade="EFEFEF")
                  for h, w in zip(mhead, mw)]]
        for r in mc["rows"]:
            vals = [bd.p([bd.run(r["name"], bold=True, size=19)]),
                    bd.p([bd.run(num(r["p10"]), size=19)]),
                    bd.p([bd.run(num(r["p50"]), bold=True, size=19)]),
                    bd.p([bd.run(num(r["p90"]), size=19)]),
                    bd.p([bd.run(f"{r['span_pct']} %".replace(".", ","), size=19)]),
                    bd.p([bd.run(r["fix"], size=18)])]
            mrows.append([bd.cell([v], w) for v, w in zip(vals, mw)])
        body.append(bd.table(mrows, mw))
        body.append(bd.p([]))

    body.append(bd.p([bd.run("4. Расчётные блоки и сверки", bold=True, size=28)], style="Heading1"))
    for b in calc["blocks"]:
        body.append(block_xml(b))

    sect = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"'
            ' w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>')
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{"".join(body)}{sect}</w:body></w:document>'
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", bd.CONTENT_TYPES)
        z.writestr("_rels/.rels", bd.RELS)
        z.writestr("word/_rels/document.xml.rels", bd.DOC_RELS)
        z.writestr("word/styles.xml", bd.STYLES)
        z.writestr("word/document.xml", document)
    n_par = document.count("<w:p>") + document.count("<w:p ")
    print(f"DOCX → {OUT} ({OUT.stat().st_size // 1024} КБ, блоков {len(calc['blocks'])}, параграфов {n_par})")
    return OUT


if __name__ == "__main__":
    build()
