#!/usr/bin/env python3
"""Черновик «Плана реализации ЦИМ» (аналог BIM Execution Plan) стадии БИ ОВЭ-75.

Собирает ove/public/docs/ove75-bim-plan-draft.docx из ove/data/bim_plan.json:
титул с плашкой «ЧЕРНОВИК v0.1», разделы — цели и рамки, перечень моделей по
лотам с LOD/LOI, среда общих данных и обменные форматы (IFC, цикл выдачи
4 недели), проверка коллизий, роли, открытые вопросы. Всё содержимое — из
найденных требований (ТЗ на ЦИМ ред.2, Прил.1 LOD/LOI, реестры, график);
источник указан у каждого блока. Рабочий черновик для согласования, не выпуск.

Переиспользует OOXML-хелперы build_docx.py (без внешних зависимостей).
Запуск вручную: .venv/bin/python ove/tools/build_bim_plan.py
(в ove/build.py намеренно не подключён — отдельный документ вне цикла CI).
"""
import json
import re
import zipfile
from datetime import date
from pathlib import Path

import build_docx as bd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "public" / "docs" / "ove75-bim-plan-draft.docx"

ACCENT = "8A6200"   # цвет плашки черновика — как в остальных черновиках проекта
MUTED = "666666"


DB_NAME = {
    "deliverables": "реестр состава выдачи Базового инжиниринга",
    "discrepancies": "реестр расхождений конкурсной документации",
    "gantt": "план-график Базового инжиниринга",
    "lots": "реестр лотов и объектов комплекса",
    "equipment": "перечень оборудования комплекса",
    "bim_plan": "база решений по информационному моделированию",
}


def clean_src(text: str) -> str:
    """Убрать из текста источника имена файлов базы решений (документ читает Заказчик)."""
    def rep(m):
        return DB_NAME.get(m.group(1), "база решений проекта")
    t = re.sub(r"(?:ove/data/)?(\w+)\.json", rep, str(text or ""))
    return re.sub(r"\s{2,}", " ", t).strip()


def para(text, *, size="20", color=None, bold=False):
    return bd.p([bd.run(clean_src(text), size=size, color=color, bold=bold)])


def src_line(text):
    return bd.p([bd.run("Источник: ", bold=True, size="18", color=MUTED),
                 bd.run(clean_src(text), size="18", color=MUTED)])


def head_cells(labels, widths):
    return [bd.cell([bd.p([bd.run(h, bold=True, size="19")])], w, shade="EFEFEF")
            for h, w in zip(labels, widths)]


def body_cell(text, width, *, size="19", color=None):
    return bd.cell([bd.p([bd.run(clean_src(text), size=size, color=color)])], width)


def draft_plaque():
    """Плашка «ЧЕРНОВИК v0.1» — одноячеечная таблица с заливкой."""
    w = 9638
    inner = [
        bd.p([bd.run("ЧЕРНОВИК v0.1", bold=True, size="30", color=ACCENT)]),
        bd.p([bd.run("Рабочий материал КВАНТ для согласования с Заказчиком. "
                     "Не является выпущенной документацией и не заменяет документы по ТЗ.",
                     size="19", color=ACCENT)]),
    ]
    return bd.table([[bd.cell(inner, w, shade="FFF3D6")]], [w])


def sec_goal_scope(d):
    xml = [bd.p("1. Цели и рамки", style="Heading1")]
    g = d.get("goal") or {}
    if g.get("t"):
        xml.append(bd.p([bd.run("Цель разработки ЦИМ. ", bold=True, size="20"),
                         bd.run(clean_src(g["t"]), size="20")]))
        if g.get("src"):
            xml.append(src_line(g["src"]))
    xml.append(para("Рамки стадии БИ:", bold=True))
    for s in d.get("scope") or []:
        xml.append(bd.p([bd.run("— ", bold=True, size="20"), bd.run(s.get("t", ""), size="20")]))
        if s.get("src"):
            xml.append(src_line(s["src"]))
    return xml


def sec_models(d):
    xml = [bd.p("2. Перечень моделей по лотам (LOD/LOI)", style="Heading1")]
    widths = [560, 2680, 1200, 2600, 2598]
    rows = [head_cells(["Лот", "Объект / модель", "Дисциплина", "LOD (графическая детализация)",
                        "LOI (источник требований)"], widths)]
    for m in d.get("models") or []:
        rows.append([
            body_cell(f"№{m.get('lot', '')}", widths[0]),
            body_cell(m.get("name", ""), widths[1]),
            body_cell(m.get("discipline", ""), widths[2]),
            body_cell(m.get("lod", ""), widths[3], size="18"),
            body_cell(m.get("loi_src", ""), widths[4], size="18", color="444444"),
        ])
    xml.append(bd.table(rows, widths))
    if d.get("models_note"):
        xml.append(para(d["models_note"], size="19", color=MUTED))
    return xml


def sec_cde_cycle(d):
    xml = [bd.p("3. Среда общих данных и обменные форматы", style="Heading1")]
    cde = d.get("cde") or {}
    if cde.get("name"):
        xml.append(bd.p([bd.run(cde["name"] + ". ", bold=True, size="20"),
                         bd.run(clean_src(cde.get("role", "")), size="20")]))
    if cde.get("src"):
        xml.append(src_line(cde["src"]))

    sw = cde.get("software") or []
    if sw:
        xml.append(para("Программное обеспечение (рекомендуемый перечень ред.2):", bold=True))
        widths = [3000, 4400, 2238]
        rows = [head_cells(["Разделы проекта", "Программное обеспечение", "Формат файлов"], widths)]
        for r in sw:
            rows.append([body_cell(r.get("scope", ""), widths[0]),
                         body_cell(r.get("tools", ""), widths[1]),
                         body_cell(r.get("fmt", ""), widths[2])])
        xml.append(bd.table(rows, widths))
    if cde.get("software_note"):
        xml.append(para(cde["software_note"], size="19", color=MUTED))

    ifc = cde.get("ifc") or {}
    if ifc:
        xml.append(para("Требования к файлам IFC:", bold=True))
        for k in ("versions", "size", "structure"):
            if ifc.get(k):
                xml.append(bd.p([bd.run("— ", bold=True, size="20"), bd.run(ifc[k], size="20")]))
        if ifc.get("src"):
            xml.append(src_line(ifc["src"]))

    if cde.get("naming"):
        xml.append(bd.p([bd.run("Наименование файлов. ", bold=True, size="20"),
                         bd.run(clean_src(cde["naming"]), size="20")]))
        if cde.get("naming_src"):
            xml.append(src_line(cde["naming_src"]))

    if cde.get("transfer"):
        xml.append(para("Состав передачи Заказчику:", bold=True))
        for t in cde["transfer"]:
            xml.append(bd.p([bd.run("— ", bold=True, size="20"), bd.run(t, size="20")]))
        if cde.get("transfer_req"):
            xml.append(para(cde["transfer_req"]))
        if cde.get("transfer_src"):
            xml.append(src_line(cde["transfer_src"]))

    if cde.get("description_cim"):
        xml.append(para("Отчёт «Описание ЦИМ» должен содержать:", bold=True))
        for t in cde["description_cim"]:
            xml.append(bd.p([bd.run("— ", bold=True, size="20"), bd.run(t, size="20")]))
        if cde.get("description_src"):
            xml.append(src_line(cde["description_src"]))

    cyc = d.get("cycle") or {}
    xml.append(bd.p([bd.run("Цикл выдачи и взаимодействие", bold=True, size="24")], style="Heading2"))
    for k, label in (("interim", "Промежуточные выгрузки"), ("interim_status", "Статус выгрузок"),
                     ("meetings", "Совещания"), ("final", "Итоговая передача")):
        if cyc.get(k):
            xml.append(bd.p([bd.run(label + ": ", bold=True, size="20"), bd.run(clean_src(cyc[k]), size="20")]))
    plan = cyc.get("plan") or []
    if plan:
        xml.append(para("Плановые сроки (целевой контур КВАНТ):", bold=True))
        widths = [7238, 2400]
        rows = [head_cells(["Работы", "Сроки"], widths)]
        for r in plan:
            rows.append([body_cell(r.get("t", ""), widths[0]),
                         body_cell(r.get("dates", ""), widths[1])])
        xml.append(bd.table(rows, widths))
        if cyc.get("plan_src"):
            xml.append(src_line(cyc["plan_src"]))
    if cyc.get("src"):
        xml.append(src_line(cyc["src"]))
    return xml


def sec_checks(d):
    xml = [bd.p("4. Контроль качества и проверка коллизий", style="Heading1")]
    checks = d.get("checks") or []
    if checks:
        widths = [6238, 3400]
        rows = [head_cells(["Вид проверки", "Требования"], widths)]
        for c in checks:
            rows.append([body_cell(c.get("name", ""), widths[0]),
                         body_cell(c.get("req", ""), widths[1])])
        xml.append(bd.table(rows, widths))
    if d.get("checks_note"):
        xml.append(para(d["checks_note"], size="19", color=MUTED))
    return xml


def sec_roles(d):
    xml = [bd.p("5. Роли и ответственность", style="Heading1")]
    widths = [2000, 4600, 3038]
    rows = [head_cells(["Роль", "По требованиям ТЗ", "Источник"], widths)]
    for r in d.get("roles") or []:
        rows.append([body_cell(r.get("who", ""), widths[0]),
                     body_cell(r.get("duty", ""), widths[1]),
                     body_cell(r.get("src", ""), widths[2], size="18", color=MUTED)])
    xml.append(bd.table(rows, widths))
    return xml


def sec_open(d):
    xml = [bd.p("7. Открытые позиции", style="Heading1"),
           para("Перечисленные ниже позиции требуют решения Заказчика. До его получения "
                "соответствующая часть состава и организации информационного моделирования "
                "определяется на стадии БИ; принятые решения вносятся в очередную ревизию "
                "настоящего документа.", size="20"),
           para("Кроме того, на стадии БИ определяется: перечень объектов проектирования и "
                "шифр проекта в системе наименования файлов; состав библиотечных компонентов "
                "оборудования по фактическим данным изготовителей; регламент доступа "
                "Исполнителя к среде общих данных Заказчика.", size="20")]
    for i, o in enumerate(d.get("open") or [], 1):
        xml.append(bd.p([bd.run(f"В-{i:02d}. ", bold=True, size="20"),
                         bd.run(clean_src(o.get("q", "")), size="20")]))
        if o.get("src"):
            xml.append(src_line(o["src"]))
    return xml


def build() -> Path:
    d = json.loads((DATA / "bim_plan.json").read_text(encoding="utf-8"))
    today = date.today().strftime("%d.%m.%Y")

    body = [
        bd.p([bd.run("ОВЭ-75 · План реализации ЦИМ стадии БИ", bold=True, size="36")]),
        bd.p([bd.run("Аналог BIM Execution Plan (BEP) — организация информационного моделирования "
                     "Базового инжиниринга", size="24")]),
        draft_plaque(),
        bd.p([bd.run("АО «Кольская ГМК» · комплекс «обжиг – выщелачивание – электроэкстракция» "
                     "производительностью 75 000 т катодной меди в год · шифр ОВЭ-75", size="20")]),
        bd.p([bd.run(f"{clean_src(d.get('note', ''))}", size="18", color=MUTED)]),
        # Абзац даты — последний абзац шапки: каркас выпуска (docframe, drop_head) снимает
        # всё до него, дальше идёт первый содержательный заголовок.
        bd.p([bd.run(f"Сформировано {today} (данные от {d.get('updated', '')}).",
                     size="18", color=MUTED)]),
    ]
    body += sec_goal_scope(d)
    body += sec_models(d)
    body += sec_cde_cycle(d)
    body += sec_checks(d)
    body += sec_roles(d)

    srcs = d.get("sources") or []
    if srcs:
        body.append(bd.p("6. Использованные источники", style="Heading1"))
        for one in srcs:
            body.append(bd.p([bd.run("— ", bold=True, size="19"),
                              bd.run(clean_src(one), size="19", color="444444")]))

    body += sec_open(d)

    sect = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"'
            ' w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body>' + "".join(body) + sect + '</w:body></w:document>')

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", bd.CONTENT_TYPES)
        z.writestr("_rels/.rels", bd.RELS)
        z.writestr("word/_rels/document.xml.rels", bd.DOC_RELS)
        z.writestr("word/styles.xml", bd.STYLES)
        z.writestr("word/document.xml", document)
    print(f"DOCX план ЦИМ → {OUT} ({OUT.stat().st_size // 1024} КБ, "
          f"моделей {len(d.get('models') or [])}, проверок {len(d.get('checks') or [])}, "
          f"открытых вопросов {len(d.get('open') or [])})")
    return OUT


if __name__ == "__main__":
    build()
