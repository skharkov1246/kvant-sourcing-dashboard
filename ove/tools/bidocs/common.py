#!/usr/bin/env python3
"""Общие кирпичи для генераторов содержания документов БИ (ove/tools/bidocs/*).

Каждый генератор — модуль с функцией build(row) -> Path: пишет DOCX БЕЗ титула и
колонтитулов в ove/public/docs/bi/src/<slug>.docx (оформление добавит
docframe.frame_docx), либо для листов возвращает список путей SVG.

Правила содержания (нарушение = брак):
— прямой инженерный язык, без рекламных оборотов, без обращений к читателю;
— все числа — из базы решений проекта (ove/data/*.json) с указанием источника
  (ИД ч.1/ч.2, ТЗ v4.1, расчётная модель); чего нет — «определяется на стадии БИ»
  или «уточняется по ТКП», а не выдумка;
— никаких имён файлов базы, «v0.1», квадратных скобок-заглушек, служебных
  пометок для команды; документ читает Заказчик;
— заголовки разделов — Heading1/Heading2, таблицы — table(), единицы СИ.
"""
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
PUBLIC = ROOT / "public"
SRC = PUBLIC / "docs" / "bi" / "src"
sys.path.insert(0, str(ROOT / "tools"))
import build_docx as bd  # noqa: E402  (OOXML-хелперы: run/p/cell/table, STYLES)
import docframe as df    # noqa: E402

LOTS = df.LOTS
ORG, CUSTOMER, OBJECT, CODE = df.ORG, df.CUSTOMER, df.OBJECT, df.CODE


def load(name: str):
    return json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))


def out_path(row: dict) -> Path:
    SRC.mkdir(parents=True, exist_ok=True)
    return SRC / (df.slug(row["code"]).replace("-r0", "") + ".docx")


# ------------------------------------------------------------ текст

def h1(text: str) -> str:
    return bd.p([bd.run(text, bold=True, size=28)], style="Heading1")


def h2(text: str) -> str:
    return bd.p([bd.run(text, bold=True, size=24)], style="Heading2")


def h3(text: str) -> str:
    return bd.p([bd.run(text, bold=True, size=21)])


def para(text: str, *, size=20, color=None, bold=False, italic=False) -> str:
    return bd.p([bd.run(text, size=size, color=color, bold=bold, italic=italic)])


def paras(text: str, **kw) -> str:
    """Абзацы через пустую строку."""
    return "".join(para(t.strip(), **kw) for t in re.split(r"\n\s*\n", text or "") if t.strip())


def note(text: str) -> str:
    return bd.p([bd.run(text, size=18, color="555555", italic=True)])


def open_item(text: str) -> str:
    return bd.p([bd.run("Определяется на стадии БИ: ", bold=True, size=19, color="8A6200"),
                 bd.run(text, size=19)])


def bullet(text: str) -> str:
    return bd.p([bd.run("— " + text, size=20)])


def source(text: str) -> str:
    return bd.p([bd.run("Источник: " + text, size=17, color="666666")])


# ----------------------------------------------------------- таблицы

def table(head: list, rows: list, widths: list, *, size=18) -> str:
    """Таблица с шапкой; ячейки — строки. Ширины в twips (сумма ≤ 9354 портрет / 14570 альбом)."""
    hdr = [bd.cell([bd.p([bd.run(str(h), bold=True, size=size)])], w, shade="EFEFEF")
           for h, w in zip(head, widths)]
    body = []
    for r in rows:
        body.append([bd.cell([bd.p([bd.run(str(c if c is not None else ""), size=size)])], w)
                     for c, w in zip(r, widths)])
    return bd.table([hdr] + body, widths)


def kv(rows: list, widths=(3200, 6154), *, size=19) -> str:
    """Таблица «параметр — значение» без шапки."""
    body = [[bd.cell([bd.p([bd.run(str(k), bold=True, size=size)])], widths[0], shade="F7F7F7"),
             bd.cell([bd.p([bd.run(str(v if v is not None else ""), size=size)])], widths[1])]
            for k, v in rows]
    return bd.table(body, list(widths))


# ------------------------------------------------------------ данные

def equipment(lot: int | None = None) -> list:
    items = load("equipment")["items"]
    return [i for i in items if lot is None or i.get("lot") == lot]


def bi_sections(lot: int) -> list:
    return load(f"bi_lot{lot}").get("sections", [])


def bi_section(lot: int, key_re: str) -> dict | None:
    for s in bi_sections(lot):
        if re.search(key_re, s.get("id", "") + " " + s.get("title", ""), re.I):
            return s
    return None


def section_body(s: dict) -> str:
    """Разделы базы решений → абзацы + таблица параметров + открытые позиции."""
    if not s:
        return ""
    parts = [paras(s.get("body") or "")]
    items = s.get("items") or []
    if items:
        parts.append(table(["Параметр", "Значение", "Источник"],
                           [[i.get("label", ""), i.get("value", ""), i.get("src", "")] for i in items],
                           [3400, 4400, 1554]))
    for o in s.get("open") or []:
        parts.append(open_item(str(o)))
    return "".join(parts)


def num(s, default=None):
    """Первое число из строки («до 78 000 м³/ч» → 78000.0)."""
    if s is None:
        return default
    m = re.search(r"-?\d[\d\s]*(?:[.,]\d+)?", str(s))
    if not m:
        return default
    return float(m.group(0).replace(" ", "").replace(",", "."))


# ------------------------------------------------------------- запись

def write_docx(path: Path, body: list, *, landscape=False) -> Path:
    if landscape:
        sect = ('<w:sectPr><w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>'
                '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"'
                ' w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>')
    else:
        sect = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"'
                ' w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body>' + "".join(body) + sect + '</w:body></w:document>')
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", bd.CONTENT_TYPES)
        z.writestr("_rels/.rels", bd.RELS)
        z.writestr("word/_rels/document.xml.rels", bd.DOC_RELS)
        z.writestr("word/styles.xml", bd.STYLES)
        z.writestr("word/document.xml", document)
    return path


def docx_text(path: Path) -> str:
    x = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    t = re.sub(r"<w:p [^>]*>|<w:p>", "\n", x)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\n{2,}", "\n", t).strip()
