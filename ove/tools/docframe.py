#!/usr/bin/env python3
"""Единое оформление выпуска документов Базового инжиниринга ОВЭ-75 (Ревизия 0).

Любой документ выпуска проходит через этот модуль и получает вид документа
проектной организации:
  текстовые (DOCX)  — титульный лист со штампом, лист регистрации ревизий,
                      колонтитулы с обозначением и номером листа, рамка страницы,
                      единый шрифт, зачистка служебных следов; затем PDF;
  графические (SVG) — лист формата A3 с рамкой и основной надписью
                      (по мотивам ГОСТ 21.1101, форма 3), штампом «Эскизная
                      проработка — не для строительства», нумерацией листов;
                      затем PDF.

Реестр выпуска — ove/data/bi_register.json: одна строка ТЗ = один документ =
одно обозначение = один PDF. Генераторы содержания лежат в ove/tools/bidocs/
(по модулю на документ, функция build(row) -> Path DOCX либо list[Path] SVG).

Запуск:  python3 ove/tools/docframe.py [код ...]      — собрать всё / выбранные
         python3 ove/tools/docframe.py --list          — показать реестр
"""
import html
import importlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PUBLIC = ROOT / "public"
OUT = PUBLIC / "docs" / "bi"
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
import doc_meta  # noqa: E402

ORG = "ООО «КВАНТ»"
CUSTOMER = "АО «Кольская ГМК»"
OBJECT = ("Комплекс «обжиг – выщелачивание – электроэкстракция» "
          "производительностью 75 000 т катодной меди в год")
CODE = "КГМК.ОВЭ-75"
STAGE = "Базовый инжиниринг — эскизная проработка"
REV = "0"
REV_NOTE = "Первая выдача. Черновик для согласования состава и решений с Заказчиком."
STAMP = "ЭСКИЗНАЯ ПРОРАБОТКА · НЕ ДЛЯ СТРОИТЕЛЬСТВА · РЕВИЗИЯ 0"
CITY = "Москва"
LOTS = {0: "Общее по комплексу", 1: "Цех обжига", 2: "Участок купороса",
        3: "Отделение электроэкстракции", 4: "Склад готовой продукции"}
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
FONT = "Arial"

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" ' \
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def today() -> str:
    return date.today().strftime("%d.%m.%Y")


def lot_title(lot) -> str:
    return "Общее по комплексу" if not lot else f"Лот №{lot} «{LOTS[lot]}»"


# ----------------------------------------------------------------- реестр

def register() -> list:
    d = json.loads((DATA / "bi_register.json").read_text(encoding="utf-8"))
    return d["rows"]


# --------------------------------------------------------- OOXML-кирпичи

def esc(s) -> str:
    return escape(str(s if s is not None else ""))


def run(text, *, bold=False, size=None, color=None, italic=False, caps=False) -> str:
    pr = f'<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/>'
    if bold:
        pr += "<w:b/>"
    if italic:
        pr += "<w:i/>"
    if caps:
        pr += "<w:caps/>"
    if color:
        pr += f'<w:color w:val="{color}"/>'
    if size:
        pr += f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
    return f'<w:r><w:rPr>{pr}</w:rPr><w:t xml:space="preserve">{esc(text)}</w:t></w:r>'


def para(runs, *, align=None, before=0, after=120, keep=False, brk=False) -> str:
    pp = f'<w:spacing w:before="{before}" w:after="{after}"/>'
    if align:
        pp += f'<w:jc w:val="{align}"/>'
    if keep:
        pp += "<w:keepNext/>"
    body = "".join(runs) if isinstance(runs, (list, tuple)) else runs
    if brk:
        body += '<w:r><w:br w:type="page"/></w:r>'
    return f"<w:p><w:pPr>{pp}</w:pPr>{body}</w:p>"


def cell(paras, width, *, shade=None, borders="all", vmerge=None, span=None, valign="center") -> str:
    tc = f'<w:tcW w:w="{width}" w:type="dxa"/>'
    if span:
        tc += f'<w:gridSpan w:val="{span}"/>'
    if vmerge:
        tc += "<w:vMerge/>" if vmerge == "cont" else '<w:vMerge w:val="restart"/>'
    if borders == "none":
        tc += ('<w:tcBorders><w:top w:val="nil"/><w:left w:val="nil"/>'
               '<w:bottom w:val="nil"/><w:right w:val="nil"/></w:tcBorders>')
    if shade:
        tc += f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>'
    tc += f'<w:vAlign w:val="{valign}"/>'
    content = "".join(paras) if isinstance(paras, (list, tuple)) else paras
    if not content:
        content = "<w:p/>"
    return f"<w:tc><w:tcPr>{tc}</w:tcPr>{content}</w:tc>"


def table(rows, widths, *, borders=True, indent=0) -> str:
    b = ('<w:tblBorders>' + "".join(
        f'<w:{s} w:val="single" w:sz="6" w:space="0" w:color="000000"/>'
        for s in ("top", "left", "bottom", "right", "insideH", "insideV")) + '</w:tblBorders>') if borders else \
        '<w:tblBorders>' + "".join(f'<w:{s} w:val="nil"/>' for s in
                                   ("top", "left", "bottom", "right", "insideH", "insideV")) + '</w:tblBorders>'
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    trs = "".join(f"<w:tr>{''.join(r)}</w:tr>" for r in rows)
    return (f'<w:tbl><w:tblPr><w:tblW w:w="{sum(widths)}" w:type="dxa"/>'
            f'<w:tblInd w:w="{indent}" w:type="dxa"/>{b}'
            f'<w:tblLayout w:type="fixed"/><w:tblCellMar><w:left w:w="80" w:type="dxa"/>'
            f'<w:right w:w="80" w:type="dxa"/></w:tblCellMar></w:tblPr>'
            f'<w:tblGrid>{grid}</w:tblGrid>{trs}</w:tbl>')


# ----------------------------------------------------- титул и ревизии

def cover_xml(row, landscape=False) -> str:
    """Титульный лист: организация, заказчик, объект, документ, обозначение, ревизия.

    На альбомном листе высота меньше — вертикальные отбивки сжимаются, иначе
    подписной блок уезжает на второй лист и ломает порядок «титул — ревизии».
    """
    lot = row.get("lot") or 0
    W = 14570 if landscape else 9636
    k = 0.35 if landscape else 1.0

    def sp(v):
        return int(v * k)
    top = [
        para([run(ORG, bold=True, size=28)], align="center", before=sp(240), after=40),
        para([run("инжиниринг · поставка · сопровождение проектов", size=18, color="555555")],
             align="center", after=60),
        para([run("", size=8)], after=0),
    ]
    # линия под шапкой — таблица из одной ячейки с нижней границей
    rule = table([[cell("<w:p/>", W, borders="none")]], [W], borders=False)
    rule = rule.replace('<w:tcBorders><w:top w:val="nil"/><w:left w:val="nil"/><w:bottom w:val="nil"/><w:right w:val="nil"/></w:tcBorders>',
                        '<w:tcBorders><w:top w:val="nil"/><w:left w:val="nil"/>'
                        '<w:bottom w:val="single" w:sz="12" w:space="0" w:color="000000"/><w:right w:val="nil"/></w:tcBorders>')
    mid = [
        para([run("", size=8)], after=sp(600)),
        para([run("Заказчик: ", size=20, color="555555"), run(CUSTOMER, size=20)], align="center", after=80),
        para([run(OBJECT, size=20)], align="center", after=80),
        para([run(f"Шифр объекта {CODE}", size=20)], align="center", after=sp(400)),
        para([run(STAGE.upper(), bold=True, size=22, caps=False)], align="center", after=80),
        para([run(lot_title(lot), size=22)], align="center", after=sp(520)),
        para([run(row["title"], bold=True, size=36)], align="center", after=160),
    ]
    if row.get("subtitle"):
        mid.append(para([run(row["subtitle"], size=22, color="333333")], align="center", after=160))
    mid += [
        para([run("Обозначение документа", size=18, color="555555")], align="center", after=20),
        para([run(row["code"], bold=True, size=28)], align="center", after=sp(400)),
    ]
    # штамп статуса
    stamp = table([[cell([
        para([run(STAMP, bold=True, size=20)], align="center", after=40),
        para([run("Документ выпущен для согласования состава и решений с Заказчиком. "
                  "Численные значения предварительные, подлежат уточнению по исходным данным "
                  "Заказчика и техническим предложениям изготовителей. Не подлежит применению "
                  "для закупки, изготовления и строительства.", size=17)], align="center", after=40),
    ], W)]], [W])
    # подписи
    sig_w = [int(W * 0.34), int(W * 0.35), W - int(W * 0.34) - int(W * 0.35)]
    sig_rows = [[cell([para([run(h, bold=True, size=17)], after=0)], w, shade="EFEFEF")
                 for h, w in zip(("Должность", "Подпись, дата", "Фамилия"), sig_w)]]
    for role in ("Генеральный директор", "Главный инженер проекта", "Разработал"):
        sig_rows.append([cell([para([run(role, size=18)], after=0)], sig_w[0]),
                         cell([para([run("", size=18)], after=0)], sig_w[1]),
                         cell([para([run("", size=18)], after=0)], sig_w[2])])
    sig = table(sig_rows, sig_w)
    bottom = [
        para([run("", size=8)], after=sp(400)),
        para([run(f"Ревизия {REV} · {today()}", size=20)], align="center", after=60),
        para([run(f"{CITY}, {date.today().year}", size=20)], align="center", after=0, brk=True),
    ]
    return "".join(top) + rule + "".join(mid) + stamp + para([run("", size=8)], after=sp(300)) + sig + "".join(bottom)


def revisions_xml(row, landscape=False) -> str:
    """Лист регистрации ревизий + состав документа (если задан)."""
    W = [1400, 2000, 6970, 2100, 2100] if landscape else [1000, 1500, 4436, 1350, 1350]
    head = [cell([para([run(h, bold=True, size=17)], after=0)], w, shade="EFEFEF")
            for h, w in zip(("Ревизия", "Дата", "Содержание изменений", "Разработал", "Проверил"), W)]
    r0 = [cell([para([run(REV, size=18)], align="center", after=0)], W[0]),
          cell([para([run(today(), size=18)], align="center", after=0)], W[1]),
          cell([para([run(REV_NOTE, size=18)], after=0)], W[2]),
          cell([para([run("", size=18)], after=0)], W[3]),
          cell([para([run("", size=18)], after=0)], W[4])]
    blank = [cell([para([run("", size=18)], after=0)], w) for w in W]
    parts = [para([run("Лист регистрации ревизий", bold=True, size=24)], after=160),
             table([head, r0, blank, blank], W),
             para([run("", size=8)], after=200),
             para([run("Основание разработки", bold=True, size=20)], after=80),
             para([run("Техническое задание на выполнение Базового инжиниринга (ред. 4.1 от 25.08.2026) "
                       "с приложениями; исходные данные Заказчика (части 1 и 2). Документ соответствует "
                       f"пункту состава Базового инжиниринга: {row.get('tz', '—')}.", size=18)], after=120)]
    if row.get("contents"):
        parts.append(para([run("Состав документа", bold=True, size=20)], before=200, after=80))
        for i, c in enumerate(row["contents"], 1):
            parts.append(para([run(f"{i}. {c}", size=18)], after=40))
    parts.append(para([run("", size=8)], after=0, brk=True))
    return "".join(parts)


# ------------------------------------------------------- колонтитулы

def header_xml(row) -> str:
    lot = row.get("lot") or 0
    W = [7200, 2436]
    left = cell([para([run(f"{row['code']} · {row['title']}", size=16, color="333333"),
                       run(f" · {lot_title(lot)}", size=16, color="777777")], after=0)], W[0], borders="none")
    right = cell([para([run(f"Ревизия {REV}", size=16, color="333333")], align="right", after=0)], W[1], borders="none")
    t = table([[left, right]], W, borders=False)
    t = t.replace('<w:tblBorders>' + "".join(f'<w:{s} w:val="nil"/>' for s in
                  ("top", "left", "bottom", "right", "insideH", "insideV")) + '</w:tblBorders>',
                  '<w:tblBorders><w:bottom w:val="single" w:sz="6" w:space="0" w:color="000000"/></w:tblBorders>')
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr {W_NS}>{t}<w:p><w:pPr><w:spacing w:after="0"/></w:pPr></w:p></w:hdr>'


def footer_xml() -> str:
    W = [3600, 3200, 2836]
    fld = ('<w:r><w:rPr><w:sz w:val="16"/></w:rPr><w:fldChar w:fldCharType="begin"/></w:r>'
           '<w:r><w:rPr><w:sz w:val="16"/></w:rPr><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
           '<w:r><w:rPr><w:sz w:val="16"/></w:rPr><w:fldChar w:fldCharType="separate"/></w:r>'
           '<w:r><w:rPr><w:sz w:val="16"/></w:rPr><w:t>1</w:t></w:r>'
           '<w:r><w:rPr><w:sz w:val="16"/></w:rPr><w:fldChar w:fldCharType="end"/></w:r>')
    npg = fld.replace(" PAGE ", " NUMPAGES ")
    left = cell([para([run(f"{ORG} · {CODE}", size=16, color="333333")], after=0)], W[0], borders="none")
    mid = cell([para([run("Эскизная проработка — не для строительства", size=16, color="333333")],
                     align="center", after=0)], W[1], borders="none")
    right = cell([f'<w:p><w:pPr><w:jc w:val="right"/><w:spacing w:after="0"/></w:pPr>'
                  f'{run("Лист ", size=16, color="333333")}{fld}{run(" из ", size=16, color="333333")}{npg}</w:p>'],
                 W[2], borders="none")
    t = table([[left, mid, right]], W, borders=False)
    t = t.replace('<w:tblBorders>' + "".join(f'<w:{s} w:val="nil"/>' for s in
                  ("top", "left", "bottom", "right", "insideH", "insideV")) + '</w:tblBorders>',
                  '<w:tblBorders><w:top w:val="single" w:sz="6" w:space="0" w:color="000000"/></w:tblBorders>')
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr {W_NS}><w:p><w:pPr><w:spacing w:after="0"/></w:pPr></w:p>{t}</w:ftr>'


def sect_xml(landscape=False) -> str:
    pg = ('<w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>' if landscape
          else '<w:pgSz w:w="11906" w:h="16838"/>')
    return ('<w:sectPr><w:headerReference w:type="default" r:id="rIdHdr"/>'
            '<w:footerReference w:type="default" r:id="rIdFtr"/>' + pg +
            '<w:pgMar w:top="1418" w:right="1134" w:bottom="1247" w:left="1418" w:header="567" w:footer="567" w:gutter="0"/>'
            '<w:pgBorders w:offsetFrom="page"><w:top w:val="single" w:sz="8" w:space="18" w:color="000000"/>'
            '<w:left w:val="single" w:sz="8" w:space="18" w:color="000000"/>'
            '<w:bottom w:val="single" w:sz="8" w:space="18" w:color="000000"/>'
            '<w:right w:val="single" w:sz="8" w:space="18" w:color="000000"/></w:pgBorders>'
            '<w:titlePg/></w:sectPr>')


# ------------------------------------------------ зачистка служебных следов

SCRUB = [
    (r"\s*\((?:[^()]*\b(?:equipment|bi_lot\w*|calc|questions_answers|suppliers|flowsheet|automation)\.json[^()]*)\)", ""),
    (r"\b(?:equipment|bi_lot\d?\*?|calc|questions_answers|suppliers|flowsheet|automation|zakladki)\.json\b", "база решений БИ"),
    (r"ЧЕРНОВИК\s*v?0\.\d", "Ревизия 0"),
    (r"черновик\s*v?0\.\d", "Ревизия 0"),
    (r"\bv0\.\d\b", "Ревизия 0"),
    (r"\[\s*Ф\.И\.О\.[^\]]*\]", "________________"),
    (r"\[\s*(?:заполняется|указать|уточнить)[^\]]*\]", "________________"),
    (r"жёлтые поля", "поля с пометкой"),
    (r"\*\*|`", ""),
    (r"Сборка\s+\d{4}-\d{2}-\d{2}\.?", ""),
    (r"\s*\((?:markdown|json|md)\)", ""),
]


def scrub_text(s: str) -> str:
    for pat, rep in SCRUB:
        s = re.sub(pat, rep, s)
    return s


def scrub_xml(xml: str) -> str:
    def fix(m):
        return m.group(1) + scrub_text(m.group(2)) + m.group(3)
    return re.sub(r"(<w:t(?:\s[^>]*)?>)([^<]*)(</w:t>)", fix, xml)


# ----------------------------------------------------- оформление DOCX

def restyle(styles: str) -> str:
    styles = re.sub(r'w:ascii="[^"]*"', f'w:ascii="{FONT}"', styles)
    styles = re.sub(r'w:hAnsi="[^"]*"', f'w:hAnsi="{FONT}"', styles)
    styles = re.sub(r'w:cs="[^"]*"', f'w:cs="{FONT}"', styles)
    return styles


def frame_docx(src: Path, row: dict, dst: Path) -> Path:
    """DOCX генератора → DOCX выпуска: титул, ревизии, колонтитулы, рамка, зачистка."""
    z = zipfile.ZipFile(src)
    names = z.namelist()
    doc = z.read("word/document.xml").decode("utf-8")
    styles = z.read("word/styles.xml").decode("utf-8") if "word/styles.xml" in names else ""
    # тело без старого sectPr
    m = re.search(r"<w:body>(.*)</w:body>", doc, re.S)
    body = m.group(1)
    body = re.sub(r"<w:sectPr>.*?</w:sectPr>", "", body, flags=re.S)
    landscape = 'w:orient="landscape"' in doc
    # снять шапку генератора: N первых абзацев (задаётся в реестре) либо до первого заголовка
    drop = row.get("drop_head")
    if drop:
        paras = re.findall(r"<w:p[ >].*?</w:p>|<w:p/>", body, re.S)
        cut = 0
        for i, p_ in enumerate(paras[:drop]):
            cut = body.find(p_, cut) + len(p_)
        body = body[cut:]
    body = scrub_xml(body)
    new_doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W_NS}><w:body>'
               + cover_xml(row, landscape) + revisions_xml(row, landscape) + body + sect_xml(landscape) + "</w:body></w:document>")
    if "w:r=" not in doc[:600] and 'xmlns:r=' not in doc[:600]:
        pass  # пространство имён r объявлено в новом корне
    rels = z.read("word/_rels/document.xml.rels").decode("utf-8") if "word/_rels/document.xml.rels" in names else \
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>'
    add = ('<Relationship Id="rIdHdr" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>'
           '<Relationship Id="rIdFtr" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>')
    if "rIdHdr" not in rels:
        rels = rels.replace("</Relationships>", add + "</Relationships>")
    if 'Target="styles.xml"' not in rels:
        rels = rels.replace("</Relationships>", '<Relationship Id="rIdSty" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
    ct = z.read("[Content_Types].xml").decode("utf-8")
    for part, kind in (("header1", "header"), ("footer1", "footer")):
        if f"/word/{part}.xml" not in ct:
            ct = ct.replace("</Types>",
                            f'<Override PartName="/word/{part}.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}+xml"/></Types>')
    if "/word/styles.xml" not in ct:
        ct = ct.replace("</Types>", '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
    dst.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out:
        for n in names:
            if n in ("word/document.xml", "word/styles.xml", "word/_rels/document.xml.rels",
                     "[Content_Types].xml", "word/header1.xml", "word/footer1.xml"):
                continue
            out.writestr(n, z.read(n))
        out.writestr("[Content_Types].xml", ct)
        out.writestr("word/_rels/document.xml.rels", rels)
        out.writestr("word/styles.xml", restyle(styles) if styles else restyle(_default_styles()))
        out.writestr("word/document.xml", new_doc)
        out.writestr("word/header1.xml", header_xml(row))
        out.writestr("word/footer1.xml", footer_xml())
    z.close()
    core_props(dst, f"{row['code']} · {row['title']}")
    return dst


def core_props(path: Path, title: str) -> None:
    """docProps: название документа и организация (поверх любого генератора)."""
    z = zipfile.ZipFile(path)
    names = z.namelist()
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for n in names:
            if n in ("docProps/core.xml", "docProps/app.xml"):
                continue
            data = z.read(n)
            if n == "[Content_Types].xml" and b"/docProps/core.xml" not in data:
                data = data.replace(b"</Types>",
                    b'<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                    b'<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>')
            if n == "_rels/.rels" and b"core-properties" not in data:
                data = data.replace(b"</Relationships>",
                    b'<Relationship Id="rIdCore" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                    b'<Relationship Id="rIdApp" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>')
            out.writestr(n, data)
        out.writestr("docProps/core.xml", doc_meta.CORE.format(title=esc(title), org=ORG, d=date.today().isoformat()))
        out.writestr("docProps/app.xml", doc_meta.APPXML.format(app=doc_meta.APP, org=ORG))
    z.close()
    shutil.move(str(tmp), path)


def stamp_pdf(path: Path, title: str) -> None:
    """Метаданные PDF: название, автор, без XMP и следов конвертера."""
    try:
        from pypdf import PdfWriter
        from pypdf.generic import NameObject
    except Exception:
        return
    w = PdfWriter(clone_from=str(path))
    root = w._root_object
    if "/Metadata" in root:
        del root[NameObject("/Metadata")]
    w.add_metadata({"/Title": title, "/Author": ORG, "/Creator": doc_meta.APP,
                    "/Producer": doc_meta.APP, "/Subject": f"{CODE} · {STAGE}"})
    tmp = path.with_suffix(".tmp.pdf")
    with open(tmp, "wb") as f:
        w.write(f)
    shutil.move(str(tmp), path)


def _default_styles() -> str:
    import build_docx as bd
    return bd.STYLES


def docx_to_pdf(src: Path, dst: Path, title: str = "") -> Path:
    with tempfile.TemporaryDirectory() as td:
        prof = Path(td) / "profile"
        subprocess.run(["soffice", f"-env:UserInstallation=file://{prof}", "--headless", "--norestore",
                        "--convert-to", "pdf", "--outdir", td, str(src)],
                       check=True, capture_output=True, timeout=900)
        pdf = Path(td) / (src.stem + ".pdf")
        if not pdf.exists():
            raise RuntimeError("LibreOffice не вернул PDF")
        shutil.move(str(pdf), dst)
    stamp_pdf(dst, title or dst.stem)
    return dst


# -------------------------------------------------- листы (SVG → A3 PDF)

SHEET_CSS = """
@page{size:A3 landscape;margin:0}
html,body{margin:0;padding:0}
body{font-family:Arial,'Liberation Sans',sans-serif;color:#000}
.sheet{position:relative;width:420mm;height:297mm;page-break-after:always;overflow:hidden;background:#fff}
.frame{position:absolute;left:20mm;top:5mm;width:395mm;height:287mm;border:0.7mm solid #000;box-sizing:border-box}
.draw{position:absolute;left:22mm;top:7mm;width:391mm;height:224mm;overflow:hidden;display:flex;align-items:center;justify-content:center;z-index:1}
.draw svg{max-width:100%;max-height:100%;width:auto;height:auto}
.stamp{position:absolute;left:24mm;bottom:9mm;white-space:nowrap;z-index:3;border:0.5mm solid #b3261e;color:#b3261e;padding:1.6mm 3mm;font-size:3.4mm;font-weight:700;letter-spacing:.06em;background:#fff}
.tb{position:absolute;right:5mm;bottom:5mm;width:185mm;border-collapse:collapse;font-size:2.6mm;background:#fff}
.tb td{border:0.35mm solid #000;padding:0.6mm 1mm;height:5mm;vertical-align:middle}
.tb td.b{font-weight:700}
.tb td.c{text-align:center}
.tb td.big{font-size:4.2mm;font-weight:700;text-align:center;height:12mm}
.tb td.obj{font-size:3mm;height:14mm;text-align:center}
.tb td.org{font-size:3.6mm;font-weight:700;text-align:center;height:13mm}
.tb td.lab{font-size:2.2mm;color:#333}
.side{position:absolute;left:8mm;top:120mm;transform:rotate(-90deg);transform-origin:left top;font-size:2.6mm;color:#333;white-space:nowrap}
"""


def sheet_html(svgs: list, row: dict) -> str:
    lot = row.get("lot") or 0
    n = len(svgs)
    pages = []
    for i, sp in enumerate(svgs, 1):
        svg = Path(sp).read_text(encoding="utf-8")
        svg = re.sub(r"<\?xml[^>]*\?>", "", svg)
        svg = re.sub(r'\sstyle="[^"]*width:1190px[^"]*"', "", svg)
        svg = re.sub(r'\swidth="\d+"(?=[^>]*viewBox)', "", svg, count=1)
        title = row.get("sheets", [{}])[i - 1].get("title") if row.get("sheets") and i - 1 < len(row["sheets"]) else None
        title = title or row["title"]
        tb = f"""
<table class="tb">
 <tr><td class="lab c" style="width:10mm">Изм.</td><td class="lab c" style="width:10mm">Кол.уч.</td><td class="lab c" style="width:10mm">Лист</td><td class="lab c" style="width:15mm">№ док.</td><td class="lab c" style="width:15mm">Подп.</td><td class="lab c" style="width:10mm">Дата</td>
     <td class="big" colspan="5" rowspan="2" style="width:115mm">{html.escape(row['code'])}</td></tr>
 <tr><td class="c">0</td><td class="c"></td><td class="c"></td><td class="c">Р0</td><td></td><td class="c">{today()}</td></tr>
 <tr><td class="lab">Разраб.</td><td colspan="2"></td><td></td><td></td><td></td><td class="obj" colspan="5" rowspan="2">{html.escape(OBJECT)}<br>{html.escape(CODE)} · {html.escape(lot_title(lot))}</td></tr>
 <tr><td class="lab">Пров.</td><td colspan="2"></td><td></td><td></td><td></td></tr>
 <tr><td class="lab">ГИП</td><td colspan="2"></td><td></td><td></td><td></td><td class="b c" colspan="2" rowspan="2" style="width:70mm">{html.escape(title)}</td><td class="lab c">Стадия</td><td class="lab c">Лист</td><td class="lab c">Листов</td></tr>
 <tr><td class="lab">Н.контр.</td><td colspan="2"></td><td></td><td></td><td></td><td class="c">БИ · Р0</td><td class="c">{i}</td><td class="c">{n}</td></tr>
 <tr><td colspan="6" class="lab" style="height:13mm;vertical-align:top">Заказчик: {html.escape(CUSTOMER)}</td><td class="org" colspan="5">{html.escape(ORG)}</td></tr>
</table>"""
        pages.append(f"""<div class="sheet"><div class="frame"></div>
<div class="side">{html.escape(row['code'])} · {html.escape(title)} · Ревизия 0 · {today()}</div>
<div class="stamp">{html.escape(STAMP)}</div>
<div class="draw">{svg}</div>{tb}</div>""")
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(row['code'])}</title><style>{SHEET_CSS}</style></head><body>{''.join(pages)}</body></html>"


def sheets_to_pdf(svgs: list, row: dict, dst: Path) -> Path:
    with tempfile.TemporaryDirectory() as td:
        h = Path(td) / "sheet.html"
        h.write_text(sheet_html(svgs, row), encoding="utf-8")
        subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
                        f"--print-to-pdf={dst}", str(h)], check=True, capture_output=True, timeout=300)
    stamp_pdf(dst, f"{row['code']} · {row['title']}")
    return dst


# ------------------------------------------------------------ сборка

def slug(code: str) -> str:
    """ОВЭ75-БИ-Л1-ПЗ → ove75-l1-pz-r0 (латиница для имён файлов и ссылок)."""
    tr = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
          "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
          "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
          "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"}
    s = "".join(tr.get(ch, ch) for ch in code.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = re.sub(r"^ove75-bi-", "", s)
    return f"ove75-{s}-r0"


def build_row(row: dict) -> dict:
    """Собрать один документ реестра: вернуть пути выпуска."""
    OUT.mkdir(parents=True, exist_ok=True)
    row = dict(row)
    if row.get("contents_from") and not row.get("contents"):
        src_json = DATA / f"{row['contents_from']}.json"
        if src_json.exists():
            secs = json.loads(src_json.read_text(encoding="utf-8")).get("sections", [])
            row["contents"] = [s.get("title", "") for s in secs if s.get("title")]
    base = OUT / slug(row["code"])
    kind = row.get("kind", "text")
    result = {"code": row["code"], "pdf": None, "docx": None, "src": []}
    if kind in ("text", "album"):
        if row.get("module"):
            mod = importlib.import_module(f"bidocs.{row['module']}")
            src = Path(mod.build(row))
        else:
            src = PUBLIC / row["source"]
        docx = frame_docx(src, row, base.with_suffix(".docx"))
        pdf = docx_to_pdf(docx, base.with_suffix(".pdf"), f"{row['code']} · {row['title']}")
        result.update(pdf=str(pdf.relative_to(PUBLIC)), docx=str(docx.relative_to(PUBLIC)),
                      src=[str(Path(s)) for s in row.get("sources", [])])
    elif kind == "sheet":
        if row.get("module"):
            mod = importlib.import_module(f"bidocs.{row['module']}")
            svgs = [Path(p) for p in mod.build(row)]
        else:
            svgs = [PUBLIC / s for s in row["sources"]]
        pdf = sheets_to_pdf(svgs, row, base.with_suffix(".pdf"))
        result.update(pdf=str(pdf.relative_to(PUBLIC)), src=[str(s) for s in row.get("sources", [])])
    elif kind == "pdf":
        src = PUBLIC / row["source"]
        shutil.copy(src, base.with_suffix(".pdf"))
        stamp_pdf(base.with_suffix(".pdf"), f"{row['code']} · {row['title']}")
        result.update(pdf=str(base.with_suffix(".pdf").relative_to(PUBLIC)))
    return result


def build(codes: list | None = None) -> list:
    sys.path.insert(0, str(TOOLS))
    rows = register()
    if codes:
        rows = [r for r in rows if r["code"] in codes]
    made, failed = [], []
    for r in rows:
        try:
            res = build_row(r)
            made.append(res)
            print(f"  {r['code']:26} → {res['pdf']}")
        except Exception as e:  # noqa: BLE001 — одна ошибка не должна валить выпуск
            failed.append((r["code"], str(e)[:160]))
            print(f"  {r['code']:26} ОШИБКА: {str(e)[:160]}")
    print(f"выпуск БИ Р0: собрано {len(made)}, ошибок {len(failed)}")
    rel_path = DATA / "bi_release.json"
    prev = json.loads(rel_path.read_text(encoding="utf-8")) if rel_path.exists() else {"docs": [], "failed": []}
    docs = {d["code"]: d for d in prev.get("docs", [])}
    for d in made:
        docs[d["code"]] = d
    fails = {c: e for c, e in prev.get("failed", []) if c not in docs}
    for c, e in failed:
        fails[c] = e
    order = [r["code"] for r in register()]
    rel_path.write_text(json.dumps({"rev": REV, "date": today(),
                                    "docs": [docs[c] for c in order if c in docs],
                                    "failed": [[c, fails[c]] for c in order if c in fails]},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    return made


if __name__ == "__main__":
    if "--list" in sys.argv:
        for r in register():
            print(f"{r['code']:26} Л{r.get('lot', 0)} эт.{r.get('stage', '-')} {r.get('kind', 'text'):6} {r['title']}")
    else:
        build([a for a in sys.argv[1:] if not a.startswith("-")] or None)
