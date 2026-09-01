#!/usr/bin/env python3
"""Черновики Пояснительных записок БИ по лотам (Word) из ove/data/bi_lot{1..4}.json.

Каждый лот → ove/public/docs/bi/ove75-lot{N}-pz-draft.docx: разделы по составу ТЗ,
параметры с источниками, привязка к нормативам, открытые позиции «определяется на
стадии БИ». Это рабочие черновики для согласования с Заказчиком, не выпуск.
Переиспользует OOXML-хелперы build_docx.py. Вызывается из ove/build.py.
"""
import json
import zipfile
from datetime import date
from pathlib import Path

import build_docx as bd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUTDIR = ROOT / "public" / "docs" / "bi"


def section_xml(s) -> str:
    parts = [bd.p([bd.run(s["title"], bold=True, size=26)], style="Heading2")]
    for para in (s.get("body") or "").split("\n\n"):
        para = para.strip()
        if para:
            parts.append(bd.p([bd.run(para, size=20)]))
    items = s.get("items") or []
    if items:
        widths = [3600, 5000, 3038]
        head = ["Параметр", "Значение", "Источник"]
        rows = [[bd.cell([bd.p([bd.run(h, bold=True, size=19)])], w, shade="EFEFEF")
                 for h, w in zip(head, widths)]]
        for i in items:
            rows.append([
                bd.cell([bd.p([bd.run(str(i.get("label", "")), size=19)])], widths[0]),
                bd.cell([bd.p([bd.run(str(i.get("value", "")), size=19)])], widths[1]),
                bd.cell([bd.p([bd.run(str(i.get("src", "")), size=18, color="666666")])], widths[2]),
            ])
        parts.append(bd.table(rows, widths))
    gost = s.get("gost") or []
    if gost:
        refs = "; ".join(f"{g.get('doc', '')} — {g.get('what', '')}" if isinstance(g, dict) else str(g)
                         for g in gost)
        parts.append(bd.p([bd.run("Нормативная база: ", bold=True, size=19),
                           bd.run(refs, size=19, color="444444")]))
    for o in (s.get("open") or []):
        parts.append(bd.p([bd.run("— определяется на стадии БИ: ", bold=True, size=19, color="8A6200"),
                           bd.run(str(o), size=19)]))
    return "".join(parts)


def build_lot(n: int) -> Path | None:
    path = DATA / f"bi_lot{n}.json"
    if not path.exists():
        return None
    d = json.loads(path.read_text(encoding="utf-8"))
    today = date.today().strftime("%d.%m.%Y")
    body = [
        bd.p([bd.run(f"ОВЭ-75 · Лот №{d['lot']} · {d['name']}", bold=True, size=36)]),
        bd.p([bd.run("Пояснительная записка Базового инжиниринга — ЧЕРНОВИК v0.1 для согласования",
                     bold=True, size=26, color="8A6200")]),
        bd.p([bd.run("АО «Кольская ГМК» · комплекс «обжиг – выщелачивание – электроэкстракция» "
                     "производительностью 75 000 т катодной меди в год · шифр КГМК.ОВЭ-75", size=20)]),
        bd.p([bd.run(f"Рабочий материал КВАНТ, сформирован {today} до старта работ: данные — из ИД "
                     f"ч.1/ч.2 и ТЗ v4.1; состав разделов — по требованиям ТЗ к содержанию БИ. Позиции "
                     f"«определяется на стадии БИ» закрываются после старта (ИД по варианту размещения, "
                     f"ТУ на подключение, ТКП). Не является выпущенной документацией.",
                     size=18, color="666666")]),
    ]
    for s in d.get("sections", []):
        body.append(section_xml(s))
    sect = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1418"'
            ' w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body>' + "".join(body) + sect + '</w:body></w:document>')
    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = OUTDIR / f"ove75-lot{n}-pz-draft.docx"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", bd.CONTENT_TYPES)
        z.writestr("_rels/.rels", bd.RELS)
        z.writestr("word/_rels/document.xml.rels", bd.DOC_RELS)
        z.writestr("word/styles.xml", bd.STYLES)
        z.writestr("word/document.xml", document)
    return out


def build() -> None:
    made = []
    for n in (1, 2, 3, 4):
        out = build_lot(n)
        if out:
            made.append(f"лот {n}: {out.stat().st_size // 1024} КБ")
    print("DOCX ПЗ лотов →", "; ".join(made))


if __name__ == "__main__":
    build()
