#!/usr/bin/env python3
"""Сборка ove/public/docs/ove75-reestr.docx — сводный документ для команды.

«ОВЭ-75. Реестр расхождений конкурсной документации и вопросов Заказчику»:
93 расхождения (редакция А против редакции Б, дословные цитаты) + вопросы
Заказчику. Решения из общей базы (questions_answers.json) вшиваются в
документ; где решения нет — строка для заполнения на встрече.

Без внешних зависимостей: .docx — это zip с OOXML, собираем стандартной
библиотекой, чтобы не менять зависимости CI. Вызывается из ove/build.py.
"""
import json
import zipfile
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "public" / "docs" / "ove75-reestr.docx"

SEV_NAME = {"blocker": "Блокирующие", "major": "Существенные", "minor": "Формальные"}
SEV_INTRO = {
    "blocker": "Без ответа Заказчика на эти пункты нельзя ни проектировать, ни корректно оценить стоимость.",
    "major": "Влияют на проектные решения, состав поставки и стоимость.",
    "minor": "Формальности: опечатки и битые ссылки, которые всплывут на экспертизе.",
}
ST_NAME = {"sent": "задан Заказчику", "answered": "получен ответ", "closed": "снят"}

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/><w:sz w:val="22"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>
 <w:pPr><w:keepNext/><w:spacing w:before="360" w:after="160"/><w:outlineLvl w:val="0"/></w:pPr>
 <w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>
 <w:pPr><w:keepNext/><w:spacing w:before="280" w:after="120"/><w:outlineLvl w:val="1"/></w:pPr>
 <w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Muted"><w:name w:val="Muted"/><w:basedOn w:val="Normal"/>
 <w:rPr><w:color w:val="666666"/><w:sz w:val="20"/></w:rPr></w:style>
</w:styles>"""


def esc(s) -> str:
    return escape(str(s or ""))


def run(text, *, bold=False, italic=False, color=None, size=None) -> str:
    props = ""
    if bold:
        props += "<w:b/>"
    if italic:
        props += "<w:i/>"
    if color:
        props += f'<w:color w:val="{color}"/>'
    if size:
        props += f'<w:sz w:val="{size}"/>'
    rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{esc(text)}</w:t></w:r>'


def p(runs, style=None) -> str:
    if isinstance(runs, str):
        runs = [run(runs)]
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}{''.join(runs)}</w:p>"


def cell(paras, width, *, shade=None, bold_first=False) -> str:
    sh = f'<w:shd w:val="clear" w:fill="{shade}"/>' if shade else ""
    if isinstance(paras, str):
        paras = [p([run(paras, bold=bold_first)])]
    return (f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{sh}</w:tcPr>{"".join(paras)}</w:tc>')


def table(rows, widths) -> str:
    borders = "".join(f'<w:{side} w:val="single" w:sz="4" w:color="C9C9C9"/>'
                      for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    body = "".join(f"<w:tr>{''.join(cells)}</w:tr>" for cells in rows)
    return (f'<w:tbl><w:tblPr><w:tblW w:w="{sum(widths)}" w:type="dxa"/>'
            f'<w:tblBorders>{borders}</w:tblBorders>'
            f'<w:tblLayout w:type="fixed"/></w:tblPr>'
            f'<w:tblGrid>{grid}</w:tblGrid>{body}</w:tbl>')


def flat_side(side) -> tuple[str, str]:
    if isinstance(side, dict):
        return str(side.get("q", "")), str(side.get("where", ""))
    return str(side or ""), ""


def item_block(it, answers) -> str:
    lot = it.get("lot", 0)
    lot_name = {0: "общий / стык", 1: "Лот №1 · Цех обжига", 2: "Лот №2 · Купорос",
                3: "Лот №3 · Электроэкстракция", 4: "Лот №4 · Склад"}.get(lot, str(lot))
    xml = [p([run(f"{it['id']}. ", bold=True), run(it["title"], bold=True)], style="Heading2"),
           p([run(f"{lot_name} · важность: {SEV_NAME.get(it['sev'], it['sev']).lower()[:-1]}е", color="666666", size="20")])]

    qa, wa = flat_side(it.get("a"))
    qb, wb = flat_side(it.get("b"))
    w_lab, w_txt = 1560, 8078
    rows = [
        [cell("Редакция А", w_lab, shade="F2F2F2", bold_first=True),
         cell([p([run(f"«{qa}»", italic=True)])] + ([p([run(wa, color="666666", size="18")])] if wa else []), w_txt)],
        [cell("Редакция Б", w_lab, shade="F2F2F2", bold_first=True),
         cell([p([run(f"«{qb}»", italic=True)])] + ([p([run(wb, color="666666", size="18")])] if wb else []), w_txt)],
    ]
    xml.append(table(rows, [w_lab, w_txt]))

    if it.get("essence"):
        xml.append(p([run("На что влияет: ", bold=True), run(it["essence"])]))
    v41 = it.get("v41")
    if v41 and v41.get("s") != "new":
        v41_color = {"off": "0CA30C", "part": "B07C00"}.get(v41["s"], "666666")
        xml.append(p([run("Статус по редакции 25.08.2026 (ТЗ v4.1 / ЦИМ ред.2): ", bold=True, color=v41_color),
                      run(v41.get("t", ""), color=v41_color)]))
    xml.append(p([run("Вопрос Заказчику: ", bold=True), run(it.get("ask", ""))]))

    ans = (answers.get("ans") or {}).get(it["id"], "").strip()
    eng = (answers.get("eng") or {}).get(it["id"], "").strip()
    st = (answers.get("st") or {}).get(it["id"], "")
    meta = (answers.get("meta") or {}).get(it["id"]) or {}
    if ans:
        who = f" ({meta.get('by', '')}, {meta.get('at', '')})" if meta.get("by") else ""
        xml.append(p([run("Решение Заказчика: ", bold=True), run(ans), run(who, color="666666", size="18")]))
    else:
        status = f" — {ST_NAME[st]}" if st in ST_NAME else ""
        xml.append(p([run("Решение Заказчика: ", bold=True),
                      run("_" * 60 + status, color="888888")]))
    if eng:
        xml.append(p([run("Комментарий инженера: ", bold=True), run(eng)]))
    return "".join(xml)


def build() -> Path:
    disc = json.loads((DATA / "discrepancies.json").read_text(encoding="utf-8"))
    tender = json.loads((DATA / "tender.json").read_text(encoding="utf-8"))
    try:
        answers = json.loads((DATA / "questions_answers.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        answers = {}

    items = disc["items"]
    n_ans = sum(1 for i in items if (answers.get("ans") or {}).get(i["id"], "").strip())
    counts = {k: sum(1 for i in items if i["sev"] == k) for k in ("blocker", "major", "minor")}
    today = date.today().strftime("%d.%m.%Y")

    body = [
        p([run("ОВЭ-75 · Реестр расхождений конкурсной документации", bold=True, size="40")]),
        p([run("и вопросов Заказчику", bold=True, size="40")]),
        p([run("АО «Кольская ГМК» · комплекс «обжиг – выщелачивание – электроэкстракция» "
               "производительностью 75 000 т катодной меди в год · Базовый инжиниринг", size="22")]),
        p([run(f"Основание: ТЗ на БИ v4.1 от 25.08.2026, ТЗ на ЦИМ ред.2 и приложения; реестр сверен с новой "
               f"редакцией (снятые позиции помечены, новые расхождения v4.1 — C-94…C-101; адреса цитат в "
               f"исторических позициях — по v3.6). Составлено {today}. "
               f"Каждая позиция — две дословные цитаты из документов Заказчика; все цитаты "
               f"проверены программно на дословное вхождение в исходники.", color="666666", size="20")]),
        p([run("Как работать с документом: пройти позиции с Заказчиком, зафиксировать по каждой, "
               "какая редакция верна (или иное решение), в строке «Решение Заказчика». Актуальная "
               "редакция реестра с теми же номерами ведётся Исполнителем и передаётся Заказчику "
               "по мере пополнения; решения, зафиксированные на рабочей встрече, попадают "
               "в следующую редакцию этого документа.", size="20")]),
        p([run("Сводка: ", bold=True),
           run(f"расхождений {len(items)} — блокирующих {counts['blocker']}, существенных {counts['major']}, "
               f"формальных {counts['minor']}; решено {n_ans}. "
               f"Плюс {len(tender.get('questions_to_customer', []))} общих вопросов без парных цитат.")]),
    ]

    sec_no = 0
    for sev in ("blocker", "major", "minor"):
        sec = [i for i in items if i["sev"] == sev]
        if not sec:
            continue
        sec_no += 1
        body.append(p(f"{sec_no}. {SEV_NAME[sev]} расхождения ({len(sec)})", style="Heading1"))
        body.append(p([run(SEV_INTRO[sev], color="666666", size="20")]))
        for it in sec:
            body.append(item_block(it, answers))

    qs = tender.get("questions_to_customer", [])
    if qs:
        sec_no += 1
        body.append(p(f"{sec_no}. Общие вопросы Заказчику ({len(qs)})", style="Heading1"))
        body.append(p([run("Вопросы без парных цитат — уточнения по составу работ и условиям.",
                           color="666666", size="20")]))
        for i, q in enumerate(qs, 1):
            qid = f"Q-{i:02d}"
            body.append(p([run(f"{qid}. ", bold=True), run(q)]))
            ans = (answers.get("ans") or {}).get(qid, "").strip()
            body.append(p([run("Ответ: ", bold=True),
                           run(ans if ans else "_" * 60, color=None if ans else "888888")]))

    sect = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"'
            ' w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f'<w:body>{"".join(body)}{sect}</w:body></w:document>')

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", DOC_RELS)
        z.writestr("word/styles.xml", STYLES)
    print(f"DOCX → {OUT} ({OUT.stat().st_size // 1024} КБ, позиций {len(items)}, решено {n_ans})")
    return OUT


if __name__ == "__main__":
    build()
