#!/usr/bin/env python3
"""Расчётные приложения ОВЭ-75 (Word) из ove/data/calc.json.

Переиспользует низкоуровневые OOXML-хелперы build_docx.py (esc/run/p/cell/table).
Выход: ove/public/docs/ove75-raschet.docx — полная записка по комплексу, а также
пролотовые выборки ove75-raschet-l1.docx и ove75-raschet-l3.docx (блоки своего лота
плюс сквозные блоки комплекса) — содержание документов выпуска ОВЭ75-БИ-Л1-РР и
ОВЭ75-БИ-Л3-РР. Титул, лист ревизий и колонтитулы добавляет ove/tools/docframe.py
(первые два абзаца шапки снимаются, drop_head = 2). Вызывается из ove/build.py.
"""
import json
import re
import zipfile
from pathlib import Path

import build_docx as bd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "public" / "docs" / "ove75-raschet.docx"

V_RU = {"ok": "сходится", "warn": "в допуске", "question": "к уточнению"}
V_COLOR = {"ok": "1F7A1F", "warn": "B8860B", "question": "B22222"}
SEV_RU = {"info": "к сведению", "minor": "требует уточнения", "major": "существенный"}
LOTS = {1: "Цех обжига", 2: "Участок получения купороса",
        3: "Отделение электроэкстракции", 4: "Склад готовой продукции"}

# Ширина полосы набора в выпуске (docframe: A4, поля 1418 + 1134 twips) — таблицы
# шире этого значения выходят за правое поле листа.
TEXT_W = 9354

# Служебные обороты рабочей записки, не выносимые в документ для Заказчика.
STRIP = [
    (r"\s*Инженерная рецензия:.*$", ""),
    (r"методическое примечание черновика БИ",
     "методическое примечание к материалам Базового инжиниринга"),
    (r"сводка черновика БИ", "сводка по Базовому инжинирингу"),
    (r"черновик БИ", "расчёт БИ"),
    (r"\bequipment\.json\b", "перечень оборудования БИ"),
    (r"\s*Интервалы определяются входными данными, а не вычислительной мощностью:"
     r"[^.]*\.", ""),
    (r"«ВОПРОС»", "«к уточнению»"),
]


def clean(s: str) -> str:
    for pat, rep in STRIP:
        s = re.sub(pat, rep, s or "", flags=re.S)
    return s.strip()


def num(x):
    if x is None:
        return ""
    a = abs(x)
    nd = 0 if a >= 1000 else (1 if a >= 10 else (2 if a >= 1 else 3))
    return f"{x:,.{nd}f}".replace(",", " ").replace(".", ",")


def with_head(tbl: str) -> str:
    """Шапка таблицы повторяется на каждой странице (w:tblHeader)."""
    return tbl.replace("<w:tr>", "<w:tr><w:trPr><w:tblHeader/></w:trPr>", 1)


def head_row(head, widths) -> list:
    return [bd.cell([bd.p([bd.run(h, bold=True, size=19)])], w, shade="EFEFEF")
            for h, w in zip(head, widths)]


def block_xml(b, no: str) -> str:
    parts = []
    lot = f"Лот №{b['lot']} «{LOTS[b['lot']]}»" if b["lot"] else "Комплекс в целом"
    parts.append(bd.p([bd.run(f"{no} {clean(b['title'])}. {lot}. Блок {b['id']}",
                              bold=True, size=26)], style="Heading2"))
    iw = [2900, 4500, TEXT_W - 2900 - 4500]
    irows = [head_row(["Исходное данное", "Значение", "Источник"], iw)]
    for i in b["inputs"]:
        irows.append([bd.cell([bd.p([bd.run(clean(i["n"]), size=19)])], iw[0]),
                      bd.cell([bd.p([bd.run(clean(i["v"]), size=19)])], iw[1]),
                      bd.cell([bd.p([bd.run(clean(i["src"]), size=18, color="666666")])], iw[2])])
    parts.append(with_head(bd.table(irows, iw)))
    parts.append(bd.p([bd.run("Ход расчёта", bold=True, size=20)]))
    for s in b["steps"]:
        parts.append(bd.p([bd.run(f"{clean(s['d'])}:  ", bold=True, size=20), bd.run(clean(s["f"]), size=20)]))
    widths = [3900, 1450, 1450, 1050, TEXT_W - 3900 - 1450 - 1450 - 1050]
    head = ["Проверка", "Расчёт КВАНТ", "ИД", "Δ, %", "Вердикт"]
    rows = [head_row(head, widths)]
    for c in b["checks"]:
        what = clean(c["what"] + (f" — {c['comment']}" if c.get("comment") else ""))
        dev = f"{'+' if c['dev'] > 0 else ''}{str(c['dev']).replace('.', ',')}"
        vals = [
            bd.p([bd.run(what, size=19)]),
            bd.p([bd.run(num(c["ours"]), size=19)]),
            bd.p([bd.run(num(c["idv"]), size=19)]),
            bd.p([bd.run(dev, size=19)]),
            bd.p([bd.run(V_RU[c["verdict"]], bold=True, size=19, color=V_COLOR[c["verdict"]])]),
        ]
        rows.append([bd.cell([v], w) for v, w in zip(vals, widths)])
    parts.append(with_head(bd.table(rows, widths)))
    parts.append(bd.p([bd.run("Вывод: ", bold=True), bd.run(clean(b["concl"]))]))
    parts.append(bd.p([]))
    return "".join(parts)


def open_positions_xml(calc, blocks) -> str:
    """Открытые позиции: вопросы к ИД, сверки «к уточнению», данные для сужения интервалов."""
    parts = [bd.p([bd.run("Открытые позиции", bold=True, size=28)], style="Heading1"),
             bd.p([bd.run("Перечень того, что определяется на стадии БИ: базис исходных "
                          "данных, требующий подтверждения разработчиком ИД; сверки, не "
                          "закрытые расчётом; данные, уточнение которых сужает расчётные "
                          "интервалы.", size=20)])]
    n = 0
    parts.append(bd.p([bd.run("Подтверждение базиса исходных данных", bold=True, size=21)]))
    for f in calc.get("flags", []):
        n += 1
        parts.append(bd.p([bd.run(f"{n}. ", bold=True, size=19),
                           bd.run(f"{clean(f['t'])} ({SEV_RU[f['sev']]}).", size=19)]))
    q = [(b, c) for b in blocks for c in b["checks"] if c["verdict"] == "question"]
    if q:
        parts.append(bd.p([bd.run("Сверки, не закрытые расчётом", bold=True, size=21)]))
        for b, c in q:
            n += 1
            parts.append(bd.p([bd.run(f"{n}. ", bold=True, size=19),
                               bd.run(f"Блок {b['id']}. {clean(c['what'])}: расчёт КВАНТ "
                                      f"{num(c['ours'])} против {num(c['idv'])} по ИД "
                                      f"(отклонение {str(c['dev']).replace('.', ',')} %).", size=19)]))
    mc = calc.get("mc") or {}
    if mc.get("rows"):
        parts.append(bd.p([bd.run("Данные, сужающие расчётные интервалы", bold=True, size=21)]))
        for r in mc["rows"]:
            n += 1
            parts.append(bd.p([bd.run(f"{n}. ", bold=True, size=19),
                               bd.run(f"{clean(r['name'])}: размах P10–P90 "
                                      f"{str(r['span_pct']).replace('.', ',')} % — {clean(r['fix'])}.", size=19)]))
    return "".join(parts)


def document_xml(calc, blocks, lot=None) -> str:
    """Тело записки: шапка (2 абзаца, снимаются в выпуске) и разделы по составу."""
    checks = [c for b in blocks for c in b["checks"]]
    cnt = {v: sum(1 for c in checks if c["verdict"] == v) for v in ("ok", "warn", "question")}
    scope = (f"Лот №{lot} «{LOTS[lot]}» и сквозные расчёты комплекса" if lot
             else "Комплекс в целом")

    body = [bd.p([bd.run(f"ОВЭ-75 · Расчётные приложения к пояснительной записке · {scope}",
                         bold=True, size=32)], style="Heading1"),
            bd.p([bd.run("АО «Кольская ГМК», комплекс «обжиг – выщелачивание – электроэкстракция» "
                         "производительностью 75 000 т катодной меди в год. К Техническому заданию "
                         "на Базовый инжиниринг (ред. 4.1 от 25.08.2026); база сверки — исходные "
                         "данные Заказчика, части 1 и 2.", size=20)])]

    body.append(bd.p([bd.run("1. Назначение и порядок расчётов", bold=True, size=28)], style="Heading1"))
    body.append(bd.p([bd.run(clean(calc["note"]), size=20)]))
    body.append(bd.p([bd.run(f"Объём документа: расчётных блоков {len(blocks)}, сверок с исходными "
                             f"данными {len(checks)} (сходится {cnt['ok']}, в допуске {cnt['warn']}, "
                             f"к уточнению {cnt['question']}); вопросов к разработчику исходных "
                             f"данных (Гипроникель) — {len(calc.get('flags', []))}.", size=20)]))
    if lot:
        body.append(bd.p([bd.run(f"Раздел расчётных блоков включает расчёты Лота №{lot} "
                                 f"«{LOTS[lot]}» и сквозные расчёты комплекса. Вопросы к исходным "
                                 f"данным, гарантийные показатели и оценка неопределённости "
                                 f"приведены по комплексу в целом — они общие для лотов.",
                                 size=20)]))
    body.append(bd.p([]))

    body.append(bd.p([bd.run("2. Вопросы к исходным данным по комплексу",
                             bold=True, size=28)], style="Heading1"))
    for i, f in enumerate(calc["flags"], 1):
        body.append(bd.p([bd.run(f"2.{i} {clean(f['t'])}", bold=True, size=21)]))
        body.append(bd.p([bd.run(f"Статус вопроса: {SEV_RU[f['sev']]}. ", bold=True, size=20),
                          bd.run(clean(f["d"]), size=20)]))
    body.append(bd.p([]))

    ns = 2
    if calc.get("guarantees"):
        ns += 1
        body.append(bd.p([bd.run(f"{ns}. Гарантийные показатели комплекса: предлагаемые значения",
                                 bold=True, size=28)], style="Heading1"))
        body.append(bd.p([bd.run("Гарантийные значения приняты с запасом к расчётным и действуют при "
                                 "подтверждённых характеристиках сырья и данных изготовителей. "
                                 "Расчётные значения приведены справочно; окончательные гарантии "
                                 "фиксируются по итогам согласования исходных данных и получения "
                                 "технических предложений изготовителей.", size=20)]))
        # Колонка «Базис» (обоснования принятых запасов) в документ не выводится —
        # обоснования остаются в базе решений для внутренней работы.
        gw = [2900, 3200, TEXT_W - 2900 - 3200]
        grows = [head_row(["Показатель", "Расчёт / исходные данные",
                           "Предлагаемое гарантийное значение"], gw)]
        for g in calc["guarantees"]:
            vals = [bd.p([bd.run(clean(g["param"]), bold=True, size=19)]),
                    bd.p([bd.run(clean(g["calc"]), size=19)]),
                    bd.p([bd.run(clean(g["guar"]), bold=True, size=19, color="8B0000")])]
            grows.append([bd.cell([v], w) for v, w in zip(vals, gw)])
        body.append(with_head(bd.table(grows, gw)))
        body.append(bd.p([]))

    if calc.get("mc"):
        mc = calc["mc"]
        ns += 1
        runs = f"{mc['n']:,}".replace(",", " ")
        body.append(bd.p([bd.run(f"{ns}. Оценка неопределённости показателей комплекса "
                                 f"(метод Монте-Карло, {runs} прогонов)",
                                 bold=True, size=28)], style="Heading1"))
        body.append(bd.p([bd.run(clean(mc["note"]), size=20)]))
        mw = [2600, 1170, 1170, 1170, 1070, TEXT_W - 2600 - 1170 * 3 - 1070]
        mrows = [head_row(["Показатель", "P10", "P50", "P90", "Размах", "Что уточнять"], mw)]
        for r in mc["rows"]:
            vals = [bd.p([bd.run(clean(r["name"]), bold=True, size=19)]),
                    bd.p([bd.run(num(r["p10"]), size=19)]),
                    bd.p([bd.run(num(r["p50"]), bold=True, size=19)]),
                    bd.p([bd.run(num(r["p90"]), size=19)]),
                    bd.p([bd.run(f"{r['span_pct']} %".replace(".", ","), size=19)]),
                    bd.p([bd.run(clean(r["fix"]), size=18)])]
            mrows.append([bd.cell([v], w) for v, w in zip(vals, mw)])
        body.append(with_head(bd.table(mrows, mw)))
        body.append(bd.p([]))

    ns += 1
    body.append(bd.p([bd.run(f"{ns}. Расчётные блоки и сверки с исходными данными",
                             bold=True, size=28)], style="Heading1"))
    for i, b in enumerate(blocks, 1):
        body.append(block_xml(b, f"{ns}.{i}"))

    body.append(open_positions_xml(calc, blocks))

    sect = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1418"'
            ' w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body>{"".join(body)}{sect}</w:body></w:document>')


def write(path: Path, document: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", bd.CONTENT_TYPES)
        z.writestr("_rels/.rels", bd.RELS)
        z.writestr("word/_rels/document.xml.rels", bd.DOC_RELS)
        z.writestr("word/styles.xml", bd.STYLES)
        z.writestr("word/document.xml", document)
    return path


def build() -> Path:
    calc = json.loads((DATA / "calc.json").read_text(encoding="utf-8"))
    blocks = calc["blocks"]
    made = []

    write(OUT, document_xml(calc, blocks))
    made.append(f"{OUT.name}: блоков {len(blocks)}")

    # Пролотовые выборки под коды выпуска Л1-РР и Л3-РР: блоки своего лота
    # плюс сквозные расчёты комплекса (lot = 0), порядок — как в базе решений.
    for lot in (1, 3):
        sel = [b for b in blocks if b["lot"] in (lot, 0)]
        path = OUT.with_name(f"{OUT.stem}-l{lot}.docx")
        write(path, document_xml(calc, sel, lot=lot))
        made.append(f"{path.name}: блоков {len(sel)}")

    print("DOCX расчётных приложений →", "; ".join(made))
    return OUT


if __name__ == "__main__":
    build()
