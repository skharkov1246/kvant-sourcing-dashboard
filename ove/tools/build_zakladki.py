#!/usr/bin/env python3
"""Сводная ведомость закладных решений для цифровизации ОВЭ-75 (Word).

Собирает ove/public/docs/specs/ove75-zakladki-vedomost.docx из
ove/data/zakladki.json по образцу build_specs.py, переиспользуя
OOXML-хелперы build_docx.py: шапка КВАНТ, плашка черновика, таблицы
по лотам (Лоты №1–4, общезаводские закладки — в конце), альбомная
ориентация. Колонки: № | Узел | Требование | Куда внесено |
Обоснование | Источник.

Рабочий черновик, не выпущенная документация. Без внешних зависимостей.
Запуск: python3 ove/tools/build_zakladki.py
"""
import json
import sys
import zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_docx as bd  # noqa: E402 — OOXML-хелперы (run/p/cell/table + части пакета)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "public" / "docs" / "specs" / "ove75-zakladki-vedomost.docx"

YELLOW = "FFEB9C"   # заливка плашки ЧЕРНОВИК (как у build_specs / build_bi_docs)
AMBER = "8A6200"    # текст на жёлтом
HEAD_SHADE = "EFEFEF"

LOT_TITLE = {1: "Лот №1 · Цех обжига", 2: "Лот №2 · Участок купоросов",
             3: "Лот №3 · Электроэкстракция", 4: "Лот №4 · Склад готовой продукции",
             0: "Общезаводские закладки (все лоты и стыки границ)"}
LOT_ORDER = (1, 2, 3, 4, 0)

# Альбом A4, поля 1134 → полезных 14570 twips.
W_LAND = 14570
WIDTHS = [600, 1600, 4600, 2700, 3300, 1770]            # = W_LAND
HEAD = ["№", "Узел", "Требование (закладка в базовый состав)",
        "Куда внесено", "Обоснование", "Источник"]


def ru_date(iso: str) -> str:
    """2026-08-28 → 28.08.2026 (в документе — единый формат даты)."""
    parts = str(iso or "").split("-")
    return f"{parts[2]}.{parts[1]}.{parts[0]}" if len(parts) == 3 else (iso or "—")


def header_block(pj, width) -> list:
    w1 = 2200
    w2 = width - w1
    return [bd.table([[
        bd.cell([bd.p([bd.run("КВАНТ", bold=True, size=34)]),
                 bd.p([bd.run("рабочие материалы проекта", size=15, color="666666")])], w1),
        bd.cell([bd.p([bd.run(f"{pj['short']} · {pj['customer']}", bold=True, size=20)]),
                 bd.p([bd.run(f"{pj['title']} · шифр {pj['code']}", size=17, color="444444")])], w2),
    ]], [w1, w2])]


def draft_plate(width) -> str:
    return bd.table([[bd.cell(
        [bd.p([bd.run("ЧЕРНОВИК — рабочий материал базового инжиниринга", bold=True, size=26, color=AMBER)]),
         bd.p([bd.run("Не является выпущенной документацией. Ведомость собрана из единого реестра "
                      "закладных решений проекта; формулировки требований подлежат переносу "
                      "в пояснительные записки, опросные листы, перечни сигналов и задания смежникам "
                      "дословно, с сохранением идентификаторов Z-NN.", size=16, color=AMBER)])],
        width, shade=YELLOW)]], [width])


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


def item_row(it) -> list:
    adresat = it.get("adresat") or []
    return [
        bd.cell([bd.p([bd.run(it.get("id", ""), bold=True, size=17)])], WIDTHS[0]),
        bd.cell([bd.p([bd.run(it.get("uzel", ""), bold=True, size=17)]),
                 bd.p([bd.run(it.get("name", ""), size=16, color="444444")])], WIDTHS[1]),
        bd.cell([bd.p([bd.run(it.get("trebovanie", ""), size=17)])], WIDTHS[2]),
        bd.cell([bd.p([bd.run("— " + a, size=16)]) for a in adresat] or
                [bd.p([bd.run("—", size=16)])], WIDTHS[3]),
        bd.cell([bd.p([bd.run(it.get("obosnovanie", ""), size=16)])], WIDTHS[4]),
        bd.cell([bd.p([bd.run(it.get("istochnik", ""), size=15, color="666666")]),
                 bd.p([bd.run(it.get("status", ""), bold=True, size=15)])], WIDTHS[5]),
    ]


def build() -> Path:
    zk = json.loads((DATA / "zakladki.json").read_text(encoding="utf-8"))
    pj = json.loads((DATA / "project.json").read_text(encoding="utf-8"))
    items = zk["items"]
    today = date.today().strftime("%d.%m.%Y")
    counts = {n: sum(1 for i in items if i.get("lot") == n) for n in LOT_ORDER}

    body = header_block(pj, W_LAND)
    body.append(bd.p([bd.run("ВЕДОМОСТЬ ЗАКЛАДНЫХ РЕШЕНИЙ ДЛЯ ЦИФРОВИЗАЦИИ", bold=True, size=36)]))
    body.append(draft_plate(W_LAND))
    # Абзац даты — последний абзац шапки: каркас выпуска (docframe, drop_head) снимает
    # всё до него, дальше идёт первый содержательный заголовок.
    body.append(bd.p([bd.run(f"Ведомость сформирована {today}.", size=16, color="666666")], style="Muted"))
    body.append(bd.p("Общие сведения", style="Heading1"))
    body.append(bd.p([bd.run(zk.get("note", ""), size=18)]))
    body.append(bd.p([bd.run("Сводка: ", bold=True, size=19),
                      bd.run(f"закладок {len(items)} (Лот №1 — {counts[1]}, №2 — {counts[2]}, "
                             f"№3 — {counts[3]}, №4 — {counts[4]}, общезаводских — {counts[0]}). "
                             f"Реестр закладных решений обновлён {ru_date(zk.get('updated', ''))}.",
                             size=19)]))
    body.append(bd.p([bd.run("Колонка «Куда внесено» указывает документы Базового инжиниринга, в состав "
                             "которых включается требование; колонка «Обоснование» — эффект, ради "
                             "которого закладка вносится в базовый состав.", size=18, color="666666")]))

    for lot in LOT_ORDER:
        sec = [i for i in items if i.get("lot") == lot]
        if not sec:
            continue
        body.append(bd.p(f"{LOT_TITLE[lot]} — {len(sec)} закладок", style="Heading2"))
        rows = [[bd.cell([bd.p([bd.run(h, bold=True, size=17)])], w, shade=HEAD_SHADE)
                 for h, w in zip(HEAD, WIDTHS)]]
        rows += [item_row(it) for it in sec]
        body.append(bd.table(rows, WIDTHS))

    body.append(bd.p("Открытые позиции", style="Heading1"))
    for t in ("Точки установки, типоразмеры и количество закладных элементов (гильз, штуцеров, "
              "бобышек, площадок обслуживания) определяются на стадии БИ по компоновочным решениям "
              "и уточняются по техническим предложениям изготовителей.",
              "Объём закладок, попадающих в границы поставки комплектного оборудования, "
              "подтверждается изготовителем в составе технико-коммерческого предложения и "
              "фиксируется в опросных листах.",
              "Резервы по кабельным трассам, каналам и площадям под последующее дооснащение "
              "принимаются на стадии БИ и подлежат согласованию с Заказчиком.",
              "Перечень закладок, переносимых в задания смежным разделам (ЭОМ, АК, СС, ВК), "
              "формируется на стадии БИ после выпуска компоновочных решений."):
        body.append(bd.p([bd.run("— " + t, size=19)]))
    body.append(bd.p([bd.run("Источник формулировок — «Реестр предложений по повышению эффективности "
                             "производства, автоматизации и контролю», редакция 2.0 от 28.08.2026 "
                             "(раздел 1 — базовый состав, раздел 2 — подготовка в базовом составе, "
                             "раздел 3 — закладные части).",
                             size=16, color="666666")], style="Muted"))

    out = write_docx(OUT, body, landscape=True)
    print(f"DOCX ведомость закладных → {out.relative_to(ROOT)} ({out.stat().st_size // 1024} КБ, "
          f"закладок {len(items)}: Л1 {counts[1]}, Л2 {counts[2]}, Л3 {counts[3]}, "
          f"Л4 {counts[4]}, общезав. {counts[0]})")
    return out


if __name__ == "__main__":
    build()
