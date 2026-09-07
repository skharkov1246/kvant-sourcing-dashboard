#!/usr/bin/env python3
"""Пояснительные записки БИ по лотам (Word) из ove/data/bi_lot{1..4}.json.

Каждый лот → ove/public/docs/bi/ove75-lot{N}-pz-draft.docx: разделы по составу ТЗ,
параметры с источниками, привязка к нормативам, открытые позиции «определяется на
стадии БИ» (по разделам и сводным перечнем в конце записки).
Файл — содержание документа выпуска: титул, лист ревизий и колонтитулы добавляет
ove/tools/docframe.py (первые четыре абзаца шапки снимаются, drop_head = 4).
Переиспользует OOXML-хелперы build_docx.py. Вызывается из ove/build.py.
"""
import json
import zipfile
from pathlib import Path

import build_docx as bd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUTDIR = ROOT / "public" / "docs" / "bi"

# Ширина полосы набора в выпуске (docframe: A4, поля 1418 + 1134 twips) — таблицы
# шире этого значения выходят за правое поле и обрезаются по краю листа.
TEXT_W = 9354
COLS = [3100, 4300, TEXT_W - 3100 - 4300]


def with_head(tbl: str) -> str:
    """Шапка таблицы повторяется на каждой странице (w:tblHeader)."""
    return tbl.replace("<w:tr>", "<w:tr><w:trPr><w:tblHeader/></w:trPr>", 1)


def section_xml(s, num: int) -> str:
    parts = [bd.p([bd.run(f"{num}. {s['title']}", bold=True, size=26)], style="Heading2")]
    for para in (s.get("body") or "").split("\n\n"):
        para = para.strip()
        if para:
            parts.append(bd.p([bd.run(para, size=20)]))
    items = s.get("items") or []
    if items:
        widths = COLS
        head = ["Параметр", "Значение", "Источник"]
        rows = [[bd.cell([bd.p([bd.run(h, bold=True, size=19)])], w, shade="EFEFEF")
                 for h, w in zip(head, widths)]]
        for i in items:
            rows.append([
                bd.cell([bd.p([bd.run(str(i.get("label", "")), size=19)])], widths[0]),
                bd.cell([bd.p([bd.run(str(i.get("value", "")), size=19)])], widths[1]),
                bd.cell([bd.p([bd.run(str(i.get("src", "")), size=18, color="666666")])], widths[2]),
            ])
        parts.append(with_head(bd.table(rows, widths)))
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


def open_positions_xml(sections) -> str:
    """Сводный перечень позиций «определяется на стадии БИ» — в конце записки."""
    rows = [(i, s.get("title", ""), s.get("open") or [])
            for i, s in enumerate(sections, 1) if s.get("open")]
    if not rows:
        return ""
    total = sum(len(o) for _, _, o in rows)
    parts = [bd.p([bd.run("Открытые позиции", bold=True, size=28)], style="Heading1"),
             bd.p([bd.run(f"Позиции, определяемые на стадии Базового инжиниринга, — {total} "
                          f"по разделам записки. Закрываются по мере выдачи недостающих исходных "
                          f"данных Заказчика, технических условий на подключение и технических "
                          f"предложений изготовителей.", size=20)])]
    for num, title, opens in rows:
        parts.append(bd.p([bd.run(f"{num}. {title}", bold=True, size=20)]))
        for k, o in enumerate(opens, 1):
            parts.append(bd.p([bd.run(f"{num}.{k}  ", bold=True, size=19),
                               bd.run(str(o), size=19)]))
    return "".join(parts)


def build_lot(n: int) -> Path | None:
    path = DATA / f"bi_lot{n}.json"
    if not path.exists():
        return None
    d = json.loads(path.read_text(encoding="utf-8"))
    # Шапка: ровно четыре абзаца — в выпуске их снимает docframe (drop_head = 4),
    # их назначение — сделать читаемым сам файл содержания.
    body = [
        bd.p([bd.run(f"ОВЭ-75 · Лот №{d['lot']} · {d['name']}", bold=True, size=36)]),
        bd.p([bd.run("Пояснительная записка Базового инжиниринга", bold=True, size=26)]),
        bd.p([bd.run("АО «Кольская ГМК» · комплекс «обжиг – выщелачивание – электроэкстракция» "
                     "производительностью 75 000 т катодной меди в год · шифр КГМК.ОВЭ-75", size=20)]),
        bd.p([bd.run("Состав разделов — по требованиям Технического задания к содержанию Базового "
                     "инжиниринга (ред. 4.1 от 25.08.2026). Численные значения — из исходных данных "
                     "Заказчика (части 1 и 2), Технического задания и расчётной модели КВАНТ; "
                     "источник указан при каждом значении.", size=18, color="666666")]),
    ]
    sections = d.get("sections", [])
    for i, s in enumerate(sections, 1):
        body.append(section_xml(s, i))
    body.append(open_positions_xml(sections))
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
