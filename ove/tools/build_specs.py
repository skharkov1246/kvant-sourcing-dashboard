#!/usr/bin/env python3
"""Спецификации и опросные листы ОВЭ-75 (Word) → ove/public/docs/specs/.

Собирает по образцу build_bi_docs.py, переиспользуя OOXML-хелперы build_docx.py:
1. ove75-perechen-oborudovaniya.docx — перечень основного оборудования по лотам
   из ove/data/equipment.json (позиция, наименование, ключевые параметры,
   кол-во, лот, примечание); альбомная ориентация.
2. ove75-ol-<класс>.docx — опросные листы для запроса ТКП по 12 ключевым классам
   оборудования: шапка КВАНТ/ОВЭ-75, титул с плашкой «ЧЕРНОВИК — для запроса ТКП»,
   таблица «Параметр | Значение | Источник/Примечание». Значения берутся из
   equipment.json и bi_lot{1..4}.json; чего в данных нет — жёлтая строка
   «уточняется по ТКП» (заполняет поставщик).

Рабочие черновики, не выпущенная документация. Без внешних зависимостей.
Запуск: python3 ove/tools/build_specs.py
"""
import json
import re
import sys
import zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_docx as bd  # noqa: E402 — OOXML-хелперы (run/p/cell/table + части пакета)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUTDIR = ROOT / "public" / "docs" / "specs"

YELLOW = "FFEB9C"   # заливка «уточняется по ТКП» и плашки ЧЕРНОВИК
AMBER = "8A6200"    # текст на жёлтом (как у черновиков ПЗ в build_bi_docs)
HEAD_SHADE = "EFEFEF"
TBD = "уточняется по ТКП"

LOT_TITLE = {0: "Вне лотов · стыки границ (справочно)", 1: "Лот №1 · Цех обжига",
             2: "Лот №2 · Участок купоросов", 3: "Лот №3 · Электроэкстракция",
             4: "Лот №4 · Склад готовой продукции"}
EQ_SRC = {0: "ИД ч.2 (позиции вне БИ)", 1: "ИД ч.1 табл. 24; ТЗ п.3.4",
          2: "ИД ч.2 табл. 4.1; ТЗ п.4.4", 3: "ИД ч.2 табл. 4.2; ТЗ п.5.3",
          4: "ТЗ п.6.3"}

# Ширины: портрет A4, поля 1134 → полезных 9638 twips; альбом → 14570 twips.
W_PORT, W_LAND = 9638, 14570


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def eq_find(eq, lot, name_re):
    rx = re.compile(name_re, re.I)
    return [it for it in eq["items"] if it.get("lot") == lot and rx.search(it.get("name", ""))]


def eq_src(it) -> str:
    base = EQ_SRC.get(it.get("lot"), "ИД/ТЗ")
    return f"{base}; поз. {it['pos']}" if it.get("pos") else base


def bi_get(bi, label, sec=None):
    """Item из bi_lot*.json: сперва точное совпадение label, затем regex; sec — фильтр по id секции."""
    for exact in (True, False):
        rx = None if exact else re.compile(label, re.I)
        for s in bi.get("sections", []):
            if sec and sec not in s.get("id", ""):
                continue
            for i in s.get("items") or []:
                lab = i.get("label", "")
                if (exact and lab == label) or (rx and rx.search(lab)):
                    return i
    return None


def resolve(row, eqs, bi):
    """Строка ОЛ → (label, value|None, src). value None = «уточняется по ТКП» (жёлтым)."""
    label = row["p"]
    if "tkp" in row:
        return label, None, row["tkp"]
    if "v" in row:
        return label, row["v"], row.get("s", "")
    if "bi" in row:
        it = bi_get(bi, row["bi"], row.get("sec"))
        if it and str(it.get("value", "")).strip():
            return label, it["value"], row.get("s") or it.get("src", "")
        return label, None, row.get("s") or "нет в решениях БИ — запросить в ТКП"
    if "eq" in row:
        i = row.get("i", 0)
        v = eqs[i].get(row["eq"]) if i < len(eqs) else None
        if v is not None and str(v).strip() not in ("", "—", "-"):
            return label, v, row.get("s") or eq_src(eqs[i])
        return label, None, row.get("s") or "нет в ИД/перечне — запросить в ТКП"
    return label, None, ""


# ---------------------------------------------------------------- общие блоки

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
        [bd.p([bd.run("ЧЕРНОВИК — для запроса ТКП", bold=True, size=26, color=AMBER)]),
         bd.p([bd.run("Не является выпущенной документацией. Значения — из исходных данных и технического "
                      "задания Заказчика и решений Базового инжиниринга; поля с пометкой «уточняется "
                      "по ТКП» заполняет поставщик в составе технико-коммерческого предложения.",
                      size=16, color=AMBER)])],
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


# ------------------------------------------------- 1) перечень оборудования

def build_perechen(eq, pj) -> Path:
    today = date.today().strftime("%d.%m.%Y")
    items = eq["items"]
    widths = [900, 3300, 5100, 1900, 600, 2770]           # = W_LAND
    head = ["Поз.", "Наименование", "Ключевые параметры", "Кол-во", "Лот", "Примечание"]

    body = header_block(pj, W_LAND)
    body.append(bd.p([bd.run("Перечень основного оборудования по лотам", bold=True, size=36)]))
    body.append(draft_plate(W_LAND))
    # Абзац даты — последний абзац шапки: каркас выпуска (docframe, drop_head) снимает
    # всё до него, дальше идёт первый содержательный заголовок.
    body.append(bd.p([bd.run(f"Сформировано {today}.", size=16, color="666666")], style="Muted"))
    counts = {n: sum(1 for i in items if i.get("lot") == n) for n in (1, 2, 3, 4, 0)}
    n_key = sum(1 for i in items if i.get("key"))
    body.append(bd.p("Общие сведения", style="Heading1"))
    body.append(bd.p([bd.run("Перечень охватывает основное и вспомогательное технологическое оборудование "
                             "комплекса по лотам. Для каждой позиции приведены наименование и модель по "
                             "исходным данным Заказчика, ключевые параметры, количество и статус поставки. "
                             "Полужирным выделены ключевые позиции, определяющие сроки и стоимость комплекса.",
                             size=19)]))
    body.append(bd.p([bd.run("Сводка: ", bold=True, size=19),
                      bd.run(f"позиций {len(items)} (Лот №1 — {counts[1]}, №2 — {counts[2]}, №3 — {counts[3]}, "
                             f"№4 — {counts[4]}, вне лотов — {counts[0]}); ключевых {n_key}.", size=19)]))
    body.append(bd.p([bd.run("Источники: ", bold=True, size=17, color="666666"),
                      bd.run(eq.get("source", ""), size=17, color="666666")]))
    if eq.get("normalization"):
        body.append(bd.p([bd.run("Нормализация: ", bold=True, size=17, color=AMBER),
                          bd.run(eq["normalization"], size=17, color=AMBER)]))
    leg = "; ".join(f"«{k}» — {v}" for k, v in (eq.get("status_legend") or {}).items())
    if leg:
        body.append(bd.p([bd.run("Статусы позиций: ", bold=True, size=17, color="666666"),
                          bd.run(leg, size=17, color="666666")]))

    for lot in (1, 2, 3, 4, 0):
        sec = [i for i in items if i.get("lot") == lot]
        if not sec:
            continue
        body.append(bd.p(f"{LOT_TITLE[lot]} — {len(sec)} поз.", style="Heading2"))
        rows = [[bd.cell([bd.p([bd.run(h, bold=True, size=18)])], w, shade=HEAD_SHADE)
                 for h, w in zip(head, widths)]]
        for n, it in enumerate(sec, 1):
            pos = it.get("pos") or f"{lot}.{n:02d}"
            name_paras = [bd.p([bd.run(it.get("name", ""), bold=bool(it.get("key")), size=18)])]
            if it.get("model") and it["model"] != "—":
                name_paras.append(bd.p([bd.run(it["model"], size=16, color="666666")]))
            par_paras = []
            for lab, field in (("", "param"), ("Габариты/НЗП: ", "dims"),
                               ("Мощность: ", "power"), ("Материал: ", "material")):
                v = it.get(field)
                if v and str(v).strip() != "—":
                    par_paras.append(bd.p([bd.run(f"{lab}{v}", size=17)]))
            if not par_paras:
                par_paras = [bd.p([bd.run(TBD, italic=True, size=17, color=AMBER)])]
            note_paras = [bd.p([bd.run(it.get("status", ""), bold=True, size=17)])]
            if it.get("purpose"):
                note_paras.append(bd.p([bd.run(it["purpose"], size=16, color="666666")]))
            if it.get("note"):
                note_paras.append(bd.p([bd.run(it["note"], size=16, color="444444")]))
            rows.append([
                bd.cell([bd.p([bd.run(pos, size=18)])], widths[0]),
                bd.cell(name_paras, widths[1]),
                bd.cell(par_paras, widths[2]),
                bd.cell([bd.p([bd.run(str(it.get("qty", "") or TBD), size=17)])], widths[3]),
                bd.cell([bd.p([bd.run(str(lot) if lot else "—", size=18)])], widths[4]),
                bd.cell(note_paras, widths[5]),
            ])
        body.append(bd.table(rows, widths))
    body.append(bd.p("Открытые позиции", style="Heading1"))
    n_tbd = sum(1 for i in items if "уточ" in str(i.get("qty", "")).lower())
    n_nopar = sum(1 for i in items
                  if not any(str(i.get(f, "") or "").strip() not in ("", "—")
                             for f in ("param", "dims", "power", "material")))
    for t in (f"Количество по {n_tbd} позициям указано как уточняемое: определяется на стадии БИ по "
              "результатам технологических расчётов и компоновочных решений.",
              f"По {n_nopar} позициям технические характеристики в исходных данных не заданы: состав и "
              "параметры определяются на стадии БИ и уточняются по техническим предложениям изготовителей.",
              "Материальное исполнение позиций, контактирующих с сернокислыми растворами и "
              "сернистыми газами, подтверждается изготовителем в составе технико-коммерческого "
              "предложения.",
              "Позиции со статусом «Вне БИ» приведены справочно — для увязки границ проектирования; "
              "их состав и параметры определяются на последующих стадиях.",
              "Позиции со статусом «Перепроектируется» и «Часть в наличии» требуют подтверждения "
              "Заказчиком фактического состояния существующего оборудования."):
        body.append(bd.p([bd.run("— " + t, size=19)]))
    body.append(bd.p([bd.run("Опросные листы по ключевым классам оборудования выпускаются отдельными "
                             "документами Базового инжиниринга.", size=17, color="666666")], style="Muted"))
    return write_docx(OUTDIR / "ove75-perechen-oborudovaniya.docx", body, landscape=True)


# ---------------------------------------------------- 2) опросные листы (ОЛ)

# 12 ключевых классов: slug → файл ove75-ol-<slug>.docx; rows — правила подстановки:
#   {"p": параметр, "eq": поле equipment.json} | {"bi": label из bi_lot<lot>.json[, "sec": id секции]}
#   | {"v": литерал, "s": источник} | {"tkp": что запросить у поставщика} (жёлтая строка).
CLASSES = [
    dict(slug="pech-ks", title="Печь кипящего слоя (КС)", lot=1, eq_re=r"^Печь кипящего слоя", rows=[
        {"p": "Назначение", "eq": "purpose"},
        {"p": "Производительность по шихте", "eq": "param"},
        {"p": "Количество", "eq": "qty"},
        {"p": "Геометрия аппарата", "bi": "Печь КС: геометрия"},
        {"p": "Температура слоя / время пребывания", "bi": "Температура кипящего слоя / время пребывания"},
        {"p": "Единовременная загрузка", "eq": "dims"},
        {"p": "Дутьё (осн./вспом. вариант)", "bi": "Дутьё (осн./вспом.)"},
        {"p": "Удельная производительность подины", "bi": "Удельная производительность подины"},
        {"p": "Тепловой эффект окисления шихты", "bi": "Тепловой эффект окисления шихты"},
        {"p": "Материальное исполнение", "eq": "material"},
        {"p": "Масса (оценка)", "bi": "Масса печи (оценка)"},
        {"p": "Сушка футеровки", "bi": "Сушка футеровки"},
        {"p": "Статус позиции", "eq": "status"},
        {"p": "Комплектность поставки (сопла, футеровка, КИП, ЗИП)", "tkp": "состав поставки — по ТКП изготовителя"},
        {"p": "Масса и членение поставочных блоков", "tkp": "по ТКП изготовителя (габариты доставки)"},
        {"p": "Срок изготовления и поставки", "tkp": "по ТКП изготовителя"},
    ]),
    dict(slug="kotel-utilizator", title="Котёл-утилизатор", lot=1, eq_re=r"^Котёл-утилизатор", rows=[
        {"p": "Назначение", "eq": "purpose"},
        {"p": "Расход газа / параметры пара", "eq": "param"},
        {"p": "Количество", "eq": "qty"},
        {"p": "Тепловосприятие (осн./вспом.)", "bi": "Тепловосприятие КУ"},
        {"p": "Паропроизводительность", "bi": "Пар КУ"},
        {"p": "Годовая выдача пара (до РОУ)", "bi": "Пар до РОУ"},
        {"p": "Питательная вода (ХОВ)", "bi": "ХОВ на КУ"},
        {"p": "Бункер пылесборника / НЗП", "eq": "dims"},
        {"p": "Материальное исполнение", "eq": "material"},
        {"p": "Гидроиспытание (ориентир)", "bi": "Гидроиспытание КУ (ориентир)"},
        {"p": "Статус позиции", "eq": "status"},
        {"p": "Поверхности нагрева и компоновка (радиация/конвекция/экономайзер)",
         "tkp": "расчёт завода-изготовителя — по ТКП"},
        {"p": "Способ очистки поверхностей нагрева", "tkp": "подтвердить в ТКП (ориентир ИД: мехобстукивание, термоволновая)"},
        {"p": "Масса и членение поставочных блоков", "tkp": "по ТКП изготовителя"},
    ]),
    dict(slug="tsiklony", title="Циклоны (одиночный циклон газоочистки)", lot=1, eq_re=r"^Одиночный циклон", rows=[
        {"p": "Типоразмер", "eq": "model"},
        {"p": "Назначение", "eq": "purpose"},
        {"p": "Производительность по газу", "eq": "param"},
        {"p": "Количество", "eq": "qty"},
        {"p": "Материальное исполнение", "eq": "material",
         "s": "ИД ч.1 табл. 24; марка нормализована (в ИД опечатка «12Х1812Т»)"},
        {"p": "Эффективность в цепочке газоочистки", "bi": "Цепочка газоочистки"},
        {"p": "Высота / бункер пылесборника", "bi": "Высота циклона"},
        {"p": "Бункер и НЗП по перечню", "eq": "dims"},
        {"p": "Масса, монтаж", "bi": "Циклон"},
        {"p": "Статус позиции", "eq": "status"},
        {"p": "Гидравлическое сопротивление", "tkp": "подтвердить в ТКП (ориентир ИД: 868/2232 Па осн./вспом.)"},
        {"p": "Опоры, теплоизоляция, обогрев бункера", "tkp": "по ТКП поставщика"},
    ]),
    dict(slug="elektrofiltr", title="Электрофильтр сухой", lot=1, eq_re=r"^Сухой электрофильтр", rows=[
        {"p": "Типоразмер", "eq": "model"},
        {"p": "Назначение", "eq": "purpose"},
        {"p": "Производительность / электропитание", "eq": "param"},
        {"p": "Количество", "bi": "Электрофильтр УГТ1-20-4"},
        {"p": "Материальное исполнение", "eq": "material",
         "s": "ИД ч.1 табл. 24; марка нормализована (в ИД опечатка «12Х1812Т»)"},
        {"p": "Габаритные размеры", "bi": "Габарит ЭФ"},
        {"p": "Остаточная запылённость (гарантия)", "bi": "Запылённость газа на СКЦ"},
        {"p": "Эффективность в цепочке газоочистки", "bi": "Цепочка газоочистки"},
        {"p": "Мощность агрегатов питания", "eq": "power"},
        {"p": "Годовой расход электроэнергии", "bi": "Электропитание ЭФ"},
        {"p": "Блокировки / защита от точки росы", "bi": "Защита ЭФ"},
        {"p": "Условия включения полей", "bi": "Включение полей ЭФ"},
        {"p": "Монтажные массы", "bi": "Самая тяжёлая единица газоочистки"},
        {"p": "Бункеры / НЗП", "eq": "dims"},
        {"p": "Статус позиции", "eq": "status"},
        {"p": "Число и длина полей, тип агрегатов питания",
         "tkp": "подтвердить в ТКП (ориентир ИД: 4 поля по 2500 мм, ОПМД-400 до 80 кВ)"},
        {"p": "Система встряхивания и обогрев бункеров", "tkp": "по ТКП поставщика"},
    ]),
    dict(slug="dymosos", title="Дымосос", lot=1, eq_re=r"^Дымосос$", rows=[
        {"p": "Типоразмер", "eq": "model"},
        {"p": "Назначение", "eq": "purpose"},
        {"p": "Производительность по газу", "eq": "param"},
        {"p": "Количество / комплектация", "bi": "Дымосос ДН-15БНЖ"},
        {"p": "Материальное исполнение", "eq": "material",
         "s": "ИД ч.1 табл. 24; марка нормализована (в ИД опечатка «12Х1812Т»)"},
        {"p": "Привод", "eq": "power"},
        {"p": "Динамика (частота возмущения, масса агрегата)", "bi": "Дымосос: частота возмущения"},
        {"p": "Массы дымососа / двигателя", "bi": "Дымосос / двигатель"},
        {"p": "Температура перемещаемого газа", "bi": "Температура газа на СКЦ"},
        {"p": "Годовой расход электроэнергии", "bi": "Электроэнергия 6 кВ (дымосос)"},
        {"p": "Статус позиции", "eq": "status"},
        {"p": "Полное давление (напор)", "tkp": "подтвердить в ТКП (ориентир ИД: 1650–7800 Па)"},
        {"p": "Регулирование производительности", "tkp": "рекомендован ЧРП — подтвердить схему регулирования в ТКП"},
        {"p": "Класс балансировки ротора", "tkp": "по ТКП изготовителя"},
    ]),
    dict(slug="vozduhoduvka", title="Воздуходувка дутьевая", lot=1, eq_re=r"^Воздуходувка", rows=[
        {"p": "Назначение", "eq": "purpose"},
        {"p": "Производительность / давление", "eq": "param"},
        {"p": "Количество", "eq": "qty"},
        {"p": "Мощность привода (оценка)", "bi": "Воздуходувка (оценка)"},
        {"p": "Годовой расход технологического воздуха", "bi": "Технологический воздух"},
        {"p": "Резерв под фундамент", "bi": "Воздуходувка: резерв под фундамент"},
        {"p": "Материальное исполнение", "eq": "material"},
        {"p": "Статус позиции", "eq": "status"},
        {"p": "Тип машины (центробежная/винтовая), рабочие/резервные", "tkp": "по ТКП поставщика"},
        {"p": "Схема регулирования производительности", "tkp": "по ТКП поставщика"},
        {"p": "Шумовые характеристики и виброизоляция", "tkp": "по ТКП поставщика"},
    ]),
    dict(slug="vakuum-filtr", title="Вакуум-фильтр барабанный", lot=1, eq_re=r"^Барабанные вакуум-фильтры", rows=[
        {"p": "Назначение", "eq": "purpose"},
        {"p": "Поверхность фильтрации / показатели", "bi": "Барабанные вакуум-фильтры"},
        {"p": "Количество", "bi": "Вакуум-фильтры"},
        {"p": "Питание (пульпа концентрата)", "bi": "Концентрат ОРФ на шихту"},
        {"p": "Требование к параметрам", "eq": "param"},
        {"p": "Материальное исполнение", "eq": "material"},
        {"p": "Статус позиции", "eq": "status"},
        {"p": "Вакуумная система (насосы, ресивер, гидрозатвор)",
         "tkp": "тип и число вакуум-насосов, объём ресивера — по ТКП поставщика фильтров"},
        {"p": "Фильтроткань и регенерация полотна", "tkp": "по ТКП поставщика"},
        {"p": "Система отдувки кека и промывки", "tkp": "по ТКП поставщика"},
    ]),
    dict(slug="vyparnoy-apparat", title="Выпарной аппарат (испаритель)", lot=2, eq_re=r"^Испаритель", rows=[
        {"p": "Состав и количество", "bi": "Испарители"},
        {"p": "Назначение", "v": "Вакуумная кристаллизация медного (нитка 1) и смешанного медно-никелевого "
                                 "(нитка 2) купоросов, I/II ступени", "s": "ИД ч.2 разд. 4.1; поз. 4.1.1–4.1.24"},
        {"p": "Материальное исполнение", "bi": "Материал", "sec": "oborud"},
        {"p": "Требование к материалу", "bi": "Требование ИД"},
        {"p": "Температуры ступеней", "bi": "Температуры ступеней"},
        {"p": "Давления (разрежения)", "bi": "Давления"},
        {"p": "Греющий пар", "bi": "Греющий пар I ступеней"},
        {"p": "Коэффициент теплопередачи (требуемый)", "bi": "K требуемый"},
        {"p": "Крупность кристаллов / влажность", "bi": "Крупность кристаллов"},
        {"p": "Нагрузка от заполненного аппарата (нитка 1)", "bi": "Испаритель нитки 1 (заполненный)"},
        {"p": "Нагрузка от заполненного аппарата (нитка 2)", "bi": "Испаритель нитки 2 (заполненный)"},
        {"p": "Монтажная единица", "bi": "Самая тяжёлая монтажная единица"},
        {"p": "Контроль материала", "bi": "Контроль 904L"},
        {"p": "Пробное давление паровых полостей", "bi": "Пробное давление паровых полостей"},
        {"p": "Статус позиции", "eq": "status"},
        {"p": "Циркуляционные насосы, сепараторы, КИП в комплекте", "tkp": "состав комплектной поставки — по ТКП"},
    ]),
    dict(slug="tsentrifuga", title="Центрифуга фильтрующая (пульсирующая)", lot=2, eq_re=r"центрифуга", rows=[
        {"p": "Тип", "eq": "model"},
        {"p": "Количество / производительность", "bi": "Центрифуги"},
        {"p": "Назначение (I стадия)", "eq": "purpose", "i": 0},
        {"p": "Назначение (II стадия)", "eq": "purpose", "i": 1},
        {"p": "Материальное исполнение", "eq": "material"},
        {"p": "Влажность и крупность осадка", "bi": "Крупность кристаллов"},
        {"p": "Промывка (смешанный купорос)", "bi": "Промывка смешанного купороса"},
        {"p": "Выпуск медного купороса", "bi": "Выпуск медного купороса"},
        {"p": "Выпуск смешанного купороса", "bi": "Выпуск смешанного купороса"},
        {"p": "Статус позиции", "eq": "status"},
        {"p": "Привод, масса, вибронагрузки", "tkp": "по ТКП поставщика (задание на фундаменты)"},
        {"p": "Электроисполнение и локальная аспирация", "tkp": "по ТКП поставщика"},
    ]),
    dict(slug="vanna-elektroliznaya", title="Ванна электролизная", lot=3, eq_re=r"^Электролизная ванна", rows=[
        {"p": "Тип / референс", "eq": "model"},
        {"p": "Количество", "bi": "Ванны"},
        {"p": "Габариты / электроды", "eq": "dims"},
        {"p": "Материал", "eq": "material"},
        {"p": "Ток серии / плотность тока", "bi": "Ток серии ном./факт."},
        {"p": "Напряжение ванны / серии", "bi": "Напряжение ванны / серии"},
        {"p": "Площадь осаждения", "bi": "Площадь осаждения ванны"},
        {"p": "Производительность", "eq": "param"},
        {"p": "Первичная загрузка электродов", "bi": "Электроды (первичная загрузка)"},
        {"p": "Температурный коридор электролита", "bi": "Температурный коридор"},
        {"p": "Рабочая среда", "bi": "Среда"},
        {"p": "Масса ванны в работе", "bi": "Ванна в работе"},
        {"p": "Монтажная единица", "bi": "Ванна (монтажная единица)"},
        {"p": "Аспирация / конструктив", "eq": "note"},
        {"p": "Испытания", "bi": "Ванны", "sec": "ispytaniya"},
        {"p": "Статус позиции", "eq": "status"},
        {"p": "Срок службы и гарантии химстойкости полимербетона", "tkp": "по ТКП поставщика"},
    ]),
    dict(slug="kran-mostovoy", title="Кран мостовой специальный (автоматический)", lot=3,
         eq_re=r"^Специальные автоматические краны", rows=[
        {"p": "Назначение", "eq": "purpose"},
        {"p": "Количество", "eq": "qty"},
        {"p": "Грузоподъёмность / режим работы", "bi": "Спецкраны"},
        {"p": "Нагрузки (груз + траверса)", "bi": "Кран"},
        {"p": "Материал оснастки", "eq": "material"},
        {"p": "Автоматика / функции", "eq": "param"},
        {"p": "Пролёт корпуса (справочно)", "bi": "Пролёт, поперёк"},
        {"p": "Разграничение поставки", "bi": "Краны", "sec": "granitsy"},
        {"p": "Испытания", "bi": "Краны", "sec": "ispytaniya"},
        {"p": "Примечание перечня", "eq": "note"},
        {"p": "Статус позиции", "eq": "status"},
        {"p": "Пролёт крана, высота подъёма, скорости", "tkp": "по ТКП поставщика"},
        {"p": "Точность позиционирования", "tkp": "по ТКП поставщика (требование АСУ — автоматический график выгрузки)"},
    ]),
    dict(slug="shtabeler", title="Штабелёр (система автоматического складирования)", lot=4,
         eq_re=r"складирования", rows=[
        {"p": "Количество / грузоподъёмность", "bi": "Штабелёры"},
        {"p": "Назначение (в составе системы складирования)", "eq": "purpose"},
        {"p": "Алгоритм работы / АСУ", "eq": "param"},
        {"p": "Функции АСУ по ТЗ", "bi": "Функции АСУ по ТЗ"},
        {"p": "Груз — пакет катодов", "bi": "Пакет (целевой)"},
        {"p": "Темп работы", "bi": "Поток"},
        {"p": "Ярусность хранения", "bi": "Ярусность хранения"},
        {"p": "Нагрузка на пол (колесо)", "bi": "Штабелёр"},
        {"p": "Масса машины", "bi": "Самая тяжёлая самоходная единица"},
        {"p": "Требование к полу", "bi": "Ровность пола"},
        {"p": "Статус позиции", "eq": "status"},
        {"p": "Тип АКБ, время работы/заряда, зарядная инфраструктура", "tkp": "по ТКП поставщика"},
        {"p": "Система позиционирования (тип, точность)", "tkp": "по ТКП поставщика"},
    ]),
]

# Закладные решения для последующей цифровизации: строки «Закладная (цифровизация)»
# добавляются в конец существующих листов, КСМ получает собственный лист.
# Формулировки живут в ol_zakladki.py (единый источник — ove/data/zakladki.json).
import ol_zakladki  # noqa: E402
for _slug, _rows in ol_zakladki.EXTRA.items():
    for _c in CLASSES:
        if _c["slug"] == _slug:
            _c["rows"].extend(_rows)
            break
    else:
        raise SystemExit(f"ol_zakladki: неизвестный слаг {_slug}")
CLASSES.append(ol_zakladki.NEWCLASS)


def general_rows(pj, lot_meta):
    site = pj.get("site", {})
    clim = "; ".join(x for x in (
        str(site.get("climate_zone", "")).split(";")[0],
        f"t хол. суток {site['t_cold_day_098']}" if site.get("t_cold_day_098") else "",
        str(site.get("snow", "")).split(";")[0],
        str(site.get("wind", "")).split(";")[0],
        f"сейсмичность {site['seismic']}" if site.get("seismic") else "") if x)
    return [
        ("Заказчик / объект", f"{pj.get('customer', '')} · {pj.get('title', '')}", "ТЗ на БИ, общие данные"),
        ("Шифр проекта", pj.get("code"), "ТЗ на БИ"),
        ("Площадка", pj.get("location"), "ТЗ на БИ"),
        ("Климат / внешние условия", clim or None, "ТЗ п.2.9"),
        ("Режим работы лота", (lot_meta or {}).get("regime"), "ТЗ, карточка лота"),
    ]


def build_ol(idx, spec, eq, pj, lots, bi_all) -> tuple[Path, int, int]:
    eqs = eq_find(eq, spec["lot"], spec["eq_re"])
    bi = bi_all.get(spec["lot"], {})
    today = date.today().strftime("%d.%m.%Y")
    lot_meta = lots.get(spec["lot"], {})
    area = eqs[0].get("area", "") if eqs else ""

    body = header_block(pj, W_PORT)
    body.append(bd.p([bd.run(f"ОПРОСНЫЙ ЛИСТ ОЛ-ОВЭ75-{idx:02d}", bold=True, size=34)]))
    body.append(bd.p([bd.run(spec["title"], bold=True, size=28)]))
    body.append(bd.p([bd.run(f"{LOT_TITLE[spec['lot']]}" + (f" · участок: {area}" if area else "")
                             + f" · сформирован {today}", size=18, color="666666")]))
    body.append(draft_plate(W_PORT))

    if eqs:
        pw = [1100, 5038, 1800, 1700]
        rows = [[bd.cell([bd.p([bd.run(h, bold=True, size=18)])], w, shade=HEAD_SHADE)
                 for h, w in zip(["Поз.", "Наименование · модель", "Кол-во", "Статус"], pw)]]
        for it in eqs:
            nm = it.get("name", "") + (f" · {it['model']}" if it.get("model") and it["model"] != "—" else "")
            rows.append([
                bd.cell([bd.p([bd.run(it.get("pos") or "—", size=18)])], pw[0]),
                bd.cell([bd.p([bd.run(nm, size=18)])], pw[1]),
                bd.cell([bd.p([bd.run(str(it.get("qty", "")), size=17)])], pw[2]),
                bd.cell([bd.p([bd.run(it.get("status", ""), size=17)])], pw[3]),
            ])
        body.append(bd.p([bd.run("Позиции перечня оборудования, закрываемые листом:", bold=True, size=19)]))
        body.append(bd.table(rows, pw))

    widths = [2600, 4200, 2838]                            # = W_PORT
    rows = [[bd.cell([bd.p([bd.run(h, bold=True, size=19)])], w, shade=HEAD_SHADE)
             for h, w in zip(["Параметр", "Значение", "Источник / Примечание"], widths)]]
    n_tbd = 0
    all_rows = general_rows(pj, lot_meta) + [resolve(r, eqs, bi) for r in spec["rows"]]
    for label, value, src in all_rows:
        missing = value is None or not str(value).strip()
        if missing:
            n_tbd += 1
        rows.append([
            bd.cell([bd.p([bd.run(label, size=19)])], widths[0]),
            bd.cell([bd.p([bd.run(TBD if missing else str(value), italic=missing, size=19,
                                  color=AMBER if missing else None)])],
                    widths[1], shade=YELLOW if missing else None),
            bd.cell([bd.p([bd.run(str(src or ""), size=17, color="666666")])], widths[2]),
        ])
    body.append(bd.p("Технические характеристики", style="Heading2"))
    body.append(bd.table(rows, widths))
    body.append(bd.p([bd.run("Заливкой отмечены поля «уточняется по ТКП» — их заполняет поставщик; остальные "
                             "значения — исходные требования Заказчика (исходные данные, техническое "
                             "задание) и решения Базового инжиниринга.", size=17, color=AMBER)]))

    body.append(bd.p("Состав ответа поставщика", style="Heading2"))
    for b in ("ТКП с ценой, сроком изготовления и условиями поставки (DDP/DAP — указать);",
              "заполненный настоящий опросный лист (поля «уточняется по ТКП») и опросный лист "
              "завода при наличии;",
              "чертёж общего вида, массогабаритные характеристики, членение на поставочные блоки;",
              "материальное исполнение с подтверждением марок (сертификаты, для CRA — ПМИ/МКК);",
              "объём шеф-монтажа, ПНР, ЗИП на 2 года; референс-лист аналогичных поставок."):
        body.append(bd.p([bd.run("— " + b, size=19)]))
    body.append(bd.p([bd.run("Контакт для ответа: " + "_" * 48, size=19, color="888888")]))
    body.append(bd.p([bd.run("Источники данных: перечень оборудования по исходным данным и техническому "
                             f"заданию Заказчика; инженерные решения Базового инжиниринга по "
                             f"{LOT_TITLE[spec['lot']]}.", size=16, color="666666")], style="Muted"))

    out = write_docx(OUTDIR / f"ove75-ol-{spec['slug']}.docx", body)
    return out, len(all_rows), n_tbd


def build() -> list[Path]:
    eq = load("equipment.json")
    pj = load("project.json")
    lots = {int(l["id"]): l for l in load("lots.json").get("lots", []) if str(l.get("id", "")).isdigit()}
    bi_all = {n: load(f"bi_lot{n}.json") for n in (1, 2, 3, 4) if (DATA / f"bi_lot{n}.json").exists()}

    OUTDIR.mkdir(parents=True, exist_ok=True)
    made = []
    out = build_perechen(eq, pj)
    made.append(out)
    print(f"DOCX перечень → {out.relative_to(ROOT)} ({out.stat().st_size // 1024} КБ, "
          f"позиций {len(eq['items'])})")
    for idx, spec in enumerate(CLASSES, 1):
        out, n_rows, n_tbd = build_ol(idx, spec, eq, pj, lots, bi_all)
        made.append(out)
        print(f"DOCX ОЛ-{idx:02d} → {out.relative_to(ROOT)} ({out.stat().st_size // 1024} КБ, "
              f"строк {n_rows}, «{TBD}» {n_tbd})")
    print(f"Итого файлов: {len(made)} в {OUTDIR.relative_to(ROOT)}/")
    return made


if __name__ == "__main__":
    build()
