#!/usr/bin/env python3
"""Печатный отчёт «Ansaldo Energia — разведка по сорсингу» из gt/data/ansaldo.json.

Рабочий документ сорсера: что за машины, кто их реально делает, у кого брать
детали горячего тракта и что делать на следующей неделе.

Вход:  gt/data/ansaldo.json
Выход: gt/docs/ANSALDO-разведка-2026-09.html (→ PDF через gt/tools/ansaldo_pdf.py)
"""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data/ansaldo.json"
OUT = ROOT / "docs/ANSALDO-разведка-2026-09.html"

e = lambda s: html.escape(str(s if s is not None else ""))

CSS = """
@page{size:A4 landscape;margin:11mm 9mm}
body{font:10px/1.45 'DejaVu Sans',Arial,sans-serif;color:#111;margin:0}
h1{font-size:20px;margin:0 0 3px}
h2{font-size:13px;margin:15px 0 5px;background:#14213d;color:#fff;padding:5px 9px;border-radius:3px;page-break-after:avoid}
h3{font-size:11.5px;margin:10px 0 3px;color:#7a1f1f;page-break-after:avoid}
.mut{color:#555;font-size:9px}
table{border-collapse:collapse;width:100%;margin:4px 0} tr{page-break-inside:avoid}
th,td{border:1px solid #bbb;padding:3px 5px;text-align:left;vertical-align:top;font-size:8.6px}
th{background:#eef1f6} b{color:#14213d}
.box{border:1px solid #ccc;border-left:4px solid #14213d;border-radius:4px;padding:6px 10px;margin:6px 0;page-break-inside:avoid;font-size:9.5px}
.warn{border-left-color:#c62828;background:#fff8f8}
.key{border-left-color:#1a7f37;background:#f6fbf7}
.act{border-left-color:#b8860b;background:#fffdf5}
.num{text-align:right;white-space:nowrap}
ul{margin:3px 0 3px 15px;padding:0} li{margin:1.5px 0}
.kpi{display:flex;gap:8px;margin:6px 0}
.kpi div{flex:1;border:1px solid #ccc;border-radius:4px;padding:6px 9px}
.kpi b{display:block;font-size:16px;color:#111}
.kpi span{font-size:8.5px;color:#555}
.src{font-size:8px;color:#666;word-break:break-all}
.t1{background:#e8f4ea} .t2{background:#fdf6e3} .t3{background:#fbfbfb}
.pill{display:inline-block;font-size:8px;border-radius:3px;padding:0 4px;margin-left:3px;background:#eef1f6;color:#14213d}
.hi{background:#ffe9a8;padding:0 2px}
.pb{page-break-before:always}
"""


def load():
    return json.loads(SRC.read_text(encoding="utf-8"))


def tbl(cols, rows, cls=""):
    """cols — [(заголовок, ширина%|None)], rows — списки готовых HTML-ячеек."""
    th = "".join(f'<th{f" style=width:{w}%" if w else ""}>{e(c)}</th>' for c, w in cols)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><tr>{th}</tr>{body}</table>'


def link(u):
    return f'<span class="src">{e(u)}</span>' if u else ""


def main():
    d = load()
    P = []
    a = P.append
    a(f'<!doctype html><meta charset="utf-8"><title>{e(d["title"])}</title><style>{CSS}</style>')
    a(f'<h1>{e(d["title"])}</h1>')
    a(f'<div class="mut">{e(d["subtitle"])} · данные на {e(d["updated"])} · КВАНТ, отдел сорсинга</div>')

    # --- сводка и что делать ---
    k = d["kpi"]
    a('<div class="kpi">' + "".join(
        f'<div><b>{e(x["v"])}</b><span>{e(x["t"])}</span></div>' for x in k) + "</div>")

    a('<div class="box key"><b>Вывод одной строкой.</b> ' + d["verdict"] + "</div>")

    a("<h2>1. Что делать — план на ближайшие недели</h2>")
    a(tbl([("№", 3), ("Действие", 30), ("Кому/куда", 22), ("Зачем", 25), ("Срок", 8), ("Риск", 12)],
          [[e(x["n"]), f'<b>{e(x["what"])}</b>', e(x["who"]), e(x["why"]), e(x["when"]), e(x.get("risk", ""))]
           for x in d["actions"]]))
    for t in d.get("action_notes", []):
        a(f'<div class="box act">{t}</div>')

    # --- компания ---
    a('<h2 class="pb">2. Компания: кто такая Ansaldo Energia</h2>')
    for t in d["company"]["intro"]:
        a(f"<p>{t}</p>")
    a("<h3>Собственники и финансы</h3>")
    a(tbl([("Показатель", 22), ("Значение", 55), ("Источник", 23)],
          [[e(x["k"]), x["v"], link(x.get("src"))] for x in d["company"]["facts"]]))
    a("<h3>Хронология: откуда взялись машины и лицензии</h3>")
    a(tbl([("Год", 6), ("Событие", 72), ("Источник", 22)],
          [[e(x["year"]), x["what"], link(x.get("src"))] for x in d["company"]["timeline"]]))
    a("<h3>Площадки и что на них делают</h3>")
    a(tbl([("Площадка", 20), ("Страна", 10), ("Что делает", 48), ("Источник", 22)],
          [[f'<b>{e(x["name"])}</b>', e(x["country"]), x["what"], link(x.get("src"))]
           for x in d["company"]["sites"]]))

    # --- модели ---
    a('<h2 class="pb">3. Модельный ряд</h2>')
    a(tbl([("Модель", 11), ("Прежнее имя", 9), ("Класс", 6), ("Мощность", 8), ("КПД ПЦ", 6), ("КПД ПГУ", 6),
           ("Об/мин", 6), ("Компрессор / турбина", 14), ("Камера сгорания", 16), ("Парк", 18)],
          [[f'<b>{e(m["name"])}</b>', e(m.get("legacy")), e(m.get("cls")), e(m.get("power")), e(m.get("eff")),
            e(m.get("cc_eff")), e(m.get("rpm")), e(m.get("stages")), e(m.get("combustor")), m.get("fleet", "")]
           for m in d["models"]]))
    for t in d.get("model_notes", []):
        a(f'<div class="box">{t}</div>')
    a("<h3>Соответствие имён: Siemens ↔ Ansaldo ↔ Shanghai Electric ↔ РФ</h3>")
    a(tbl([("Siemens", 20), ("Ansaldo", 18), ("Китай", 18), ("РФ / лицензия", 22), ("Комментарий", 22)],
          [[e(x["s"]), e(x["a"]), e(x.get("cn", "")), e(x.get("ru", "")), x.get("note", "")]
           for x in d["naming"]]))

    # --- наследие Alstom ---
    a('<h2 class="pb">4. Наследие Alstom: GT26, GT36, GT13E2 — что кому досталось</h2>')
    for t in d["ge_split"]:
        a(f'<div class="box warn">{t}</div>')
    a("<h3>Машины</h3>")
    a(tbl([("Модель", 8), ("Конструкция", 27), ("Показатели", 27), ("Парк", 20), ("Интервалы", 18)],
          [[f'<b>{e(x["m"])}</b>', x["sp"], x["p"], x["f"], x["i"]] for x in d["alstom_gt"]]))
    a("<h3>Апгрейд-пакеты</h3>")
    a(tbl([("Пакет", 13), ("База", 13), ("Интервал", 10), ("M-режим", 14), ("XL / итог", 18), ("Что физически меняется", 32)],
          [[f'<b>{e(x["n"])}</b>', e(x["b"]), x["iv"], x["m"], x["x"], x["c"]] for x in d["alstom_upg"]]))
    a("<h3>Горячий тракт GT13E2: что меняется при продлении 36 → 48 kEOH</h3>")
    a(tbl([("Деталь", 15), ("Что с ней", 45), ("Механизм отказа", 40)],
          [[f'<b>{e(x["d"])}</b>', x["w"], x["f"]] for x in d["gt13e2_parts"]]))
    a("<h3>Парк Alstom в РФ и СНГ</h3>")
    a(tbl([("Станция", 18), ("Владелец", 11), ("ГТУ", 15), ("Мощность", 20), ("Ввод", 10), ("Что известно", 26)],
          [[f'<b>{e(x["p"])}</b>', e(x["o"]), x["g"], x["w"], e(x["y"]), x["n"]] for x in d["alstom_fleet_ru"]]))
    a('<h3 class="pb">Независимый аутмаркет по Alstom</h3>')
    a(tbl([("Компания", 12), ("Страна", 8), ("Что умеет", 34), ("Референсы", 22), ("Контакт", 14), ("Доступ", 10)],
          [[f'<b>{e(x["n"])}</b>', e(x["c"]), x["w"], x["r"], e(x["k"]), x["a"]] for x in d["alstom_market"]]))
    a("<h3>Alstom Owners Group — вход в живое сообщество</h3>")
    for t in d["aog"]:
        a(f'<div class="box">{t}</div>')

    # --- горячий тракт ---
    a('<h2 class="pb">5. Горячий тракт: что покупаем и в каком количестве</h2>')
    a(tbl([("Узел", 20), ("EN-термин для RFQ", 22), ("Кол-во на машину", 13), ("Ресурс / интервал", 20), ("Комментарий", 25)],
          [[f'<b>{e(x["ru"])}</b>', e(x["en"]), e(x.get("qty", "")), e(x.get("life", "")), x.get("note", "")]
           for x in d["parts"]]))
    for t in d.get("parts_notes", []):
        a(f'<div class="box">{t}</div>')
    if d.get("prices"):
        a("<h3>Ценовые ориентиры и сроки</h3>")
        a(tbl([("Позиция", 28), ("Цена / срок", 24), ("Основание", 26), ("Источник", 22)],
              [[e(x["item"]), f'<b>{e(x["val"])}</b>', x.get("basis", ""), link(x.get("src"))]
               for x in d["prices"]]))

    # --- парк в РФ ---
    a('<h2 class="pb">6. Парк в России и СНГ — наш рынок спроса</h2>')
    a(tbl([("Станция", 22), ("Владелец", 15), ("Модель", 14), ("Блоков", 6), ("Ввод", 7), ("Статус / что известно", 24), ("Источник", 12)],
          [[f'<b>{e(x["plant"])}</b>', e(x.get("owner")), e(x.get("model")), e(x.get("units")),
            e(x.get("year")), x.get("note", ""), link(x.get("src"))] for x in d["fleet_ru"]]))
    for t in d.get("fleet_notes", []):
        a(f'<div class="box">{t}</div>')

    # --- наш след ---
    a("<h2>7. Наш собственный след: сделки и таможня</h2>")
    a("<h3>Запросы КВАНТ по машинам этого семейства (Bitrix, СП-166)</h3>")
    a(tbl([("Сделка", 8), ("Дата", 8), ("Что запрашивали", 34), ("Кого спрашивали", 34), ("Итог", 16)],
          [[e(x["id"]), e(x["date"]), x["what"], x["who"], x["res"]] for x in d["trace"]["deals"]]))
    a("<h3>Ввоз в РФ по таможне (HS 8411, наша выгрузка 2023-01…2026-03)</h3>")
    a(tbl([("Дата", 8), ("Отправитель", 20), ("Получатель", 16), ("Что", 40), ("Нетто, кг", 8), ("Отправление", 8)],
          [[e(x["date"]), f'<b>{e(x["ex"])}</b>', e(x["im"]), e(x["what"]), e(x.get("kg", "")), e(x.get("co", ""))]
           for x in d["trace"]["customs"]]))
    for t in d["trace"].get("notes", []):
        a(f'<div class="box key">{t}</div>')

    # --- субпоставщики ---
    a('<h2 class="pb">8. Субпоставщики Ansaldo — кто физически делает детали</h2>')
    for grp in d["subs"]:
        a(f'<h3>{e(grp["title"])}</h3>')
        if grp.get("intro"):
            a(f'<div class="mut">{grp["intro"]}</div>')
        a(tbl([("Компания", 17), ("Страна", 8), ("Что делает для Ansaldo", 30), ("Доказательство связи", 24), ("Контакт / сайт", 13), ("Дост.", 8)],
              [[f'<b>{e(x["name"])}</b>', e(x.get("country")), x.get("what", ""), x.get("proof", ""),
                e(x.get("site", "")) + ("<br>" + e(x["email"]) if x.get("email") else ""), e(x.get("access", ""))]
               for x in grp["rows"]], cls=grp.get("cls", "")))

    # --- сеть и дилеры ---
    a('<h2 class="pb">9. Сеть Ansaldo: дочки, сервис, лицензиаты</h2>')
    a(tbl([("Структура", 20), ("Страна", 10), ("Роль", 30), ("Контакт", 18), ("Комментарий", 22)],
          [[f'<b>{e(x["name"])}</b>', e(x.get("country")), x.get("role", ""),
            e(x.get("contact", "")), x.get("note", "")] for x in d["network"]]))

    # --- независимый аftermarket ---
    a("<h2>10. Независимый аftermarket — кто продаст без OEM</h2>")
    for grp in d["aftermarket"]:
        a(f'<h3>{e(grp["title"])}</h3>')
        if grp.get("intro"):
            a(f'<div class="mut">{grp["intro"]}</div>')
        a(tbl([("Компания", 17), ("Страна", 9), ("Что умеет по машинам Ansaldo", 30), ("Доказательство", 22), ("Контакт", 14), ("Дост.", 8)],
              [[f'<b>{e(x["name"])}</b>', e(x.get("country")), x.get("what", ""), x.get("proof", ""),
                e(x.get("email", "") or x.get("site", "")), e(x.get("access", ""))] for x in grp["rows"]],
              cls=grp.get("cls", "")))

    # --- сервис в РФ ---
    a('<h2 class="pb">11. Кто в России реально работает по этим машинам</h2>')
    a(tbl([("Компания", 16), ("Город / ИНН", 12), ("Что умеет по V-серии", 28), ("Референсы", 22), ("Масштаб", 14), ("Реальность", 8)],
          [[f'<b>{e(x["n"])}</b>' + (f'<div class="src">{e(x["w"])}</div>' if x.get("w") else "")
            + (f'<div class="mut">{e(x["k"])}</div>' if x.get("k") else ""),
            e(x.get("c", "")) + (f'<div class="mut">{e(x["inn"])}</div>' if x.get("inn") else ""),
            x.get("cap", ""), x.get("ref", ""), x.get("sc", ""), e(x.get("r", ""))] for x in d["ru_service"]]))
    for grp in d["ru_closed"]:
        a(f'<h3>{e(grp["t"])}</h3>')
        a("<ul>" + "".join(f"<li>{t}</li>" for t in grp["items"]) + "</ul>")
    a(f'<div class="box key"><b>Вывод.</b> {d["ru_verdict"]}</div>')

    # --- генераторы ---
    a('<h2 class="pb">12. Генераторы Ansaldo</h2>')
    a(f'<div class="box warn">{d["gen_code"]}</div>')
    a("<h3>Типоряд</h3>")
    a(tbl([("Серия", 13), ("Происхождение", 13), ("Мощность", 13), ("Напряжение", 9), ("Охлаждение", 18), ("Типоразмеры и референс", 34)],
          [[f'<b>{e(x["s"])}</b>', e(x["o"]), e(x["p"]), e(x["u"]), e(x["cool"]), x["x"]] for x in d["gen_types"]]))
    a("<h3>Какой генератор идёт с какой ГТУ</h3>")
    a(tbl([("ГТУ", 8), ("ISO", 11), ("ПГУ", 11), ("H2", 6), ("Генератор", 36), ("Основание", 28)],
          [[f'<b>{e(x["gt"])}</b>', e(x["iso"]), e(x["cc"]), e(x["h2"]), x["g"], link(x["src"])] for x in d["gen_pairs"]]))
    a("<h3>Комплектация: кто делает узлы</h3>")
    a(tbl([("Узел", 13), ("Поставщик", 16), ("Что именно", 41), ("Примечание", 30)],
          [[f'<b>{e(x["u"])}</b>', e(x["who"]), x["what"], x["note"]] for x in d["gen_parts"]]))
    a('<h3 class="pb">Кто ремонтирует</h3>')
    a(tbl([("Компания", 15), ("База", 13), ("Что делает", 52), ("Доступность", 20)],
          [[f'<b>{e(x["n"])}</b>', e(x["c"]), x["what"], x["a"]] for x in d["gen_repair"]]))
    a("<h3>Объём инспекции без выемки ротора</h3>")
    a(tbl([("Уровень / метод", 20), ("Что входит", 80)],
          [[f'<b>{e(x["lvl"])}</b>', x["what"]] for x in d["gen_inspect"]]))
    a("<h3>Наши площадки</h3>")
    a(tbl([("Объект", 22), ("Состав", 78)], [[f'<b>{e(x["o"])}</b>', x["w"]] for x in d["gen_sites"]]))
    for t in d["gen_deal"]:
        a(f'<div class="box act">{t}</div>')

    # --- АСУ ТП ---
    a('<h2 class="pb">13. АСУ ТП, КИП и топливная арматура</h2>')
    for grp in d["controls"]:
        a(f'<h3>{e(grp["g"])}</h3>')
        a(tbl([("Позиция", 17), ("Кто", 14), ("Что это", 36), ("Где у нас", 21), ("Доступ", 12)],
              [[f'<b>{e(x["n"])}</b>', e(x["who"]), x["what"], x["ru"], e(x["av"])] for x in grp["rows"]]))
    for t in d["controls_notes"]:
        a(f'<div class="box">{t}</div>')

    # --- каналы и санкции ---
    a('<h2 class="pb">14. Санкции и рабочие каналы</h2>')
    for t in d["sanctions"]["intro"]:
        a(f'<div class="box warn">{t}</div>')
    a(tbl([("Канал", 16), ("Как работает", 34), ("Что реально получим", 24), ("Риск", 12), ("Первый шаг", 14)],
          [[f'<b>{e(x["name"])}</b>', x["how"], x["what"], e(x.get("risk", "")), x.get("step", "")]
           for x in d["channels"]]))

    # --- право и экспортконтроль ---
    a('<h2 class="pb">15. Право и экспортконтроль по позиции 8411</h2>')
    a(tbl([("Юрисдикция", 14), ("Инструмент", 17), ("Охват 8411", 25), ("Дата введения", 16), ("Что это значит", 28)],
          [[f'<b>{e(x["j"])}</b>', e(x["a"]), x["s"], x["w"], x["x"]] for x in d["legal_map"]]))
    for t in d["legal_notes"]:
        a(f'<div class="box warn">{t}</div>')

    # --- логистика и таможня ---
    a('<h2 class="pb">16. Логистика и таможня</h2>')
    a("<h3>Коды ТН ВЭД и ставки</h3>")
    a(tbl([("Код", 11), ("Наименование", 40), ("Пошлина", 7), ("НДС", 5), ("Примечание", 37)],
          [[f'<b>{e(x["c"])}</b>', e(x["n"]), f'<b>{e(x["d"])}</b>', e(x["v"]), x["x"]] for x in d["customs_codes"]]))
    a("<h3>Где переклассифицируют</h3>")
    for t in d["customs_risk"]:
        a(f'<div class="box warn">{t}</div>')
    a("<h3>Торговые соглашения ЕАЭС — что даёт по 8411</h3>")
    a(tbl([("Партнёр", 10), ("Тип", 14), ("В силе с", 14), ("Что по 8411", 44), ("Сертификат", 18)],
          [[f'<b>{e(x["p"])}</b>', e(x["t"]), e(x["in"]), x["r"], e(x["s"])] for x in d["trade_prefs"]]))
    a('<h3 class="pb">Маршруты и сроки</h3>')
    a(tbl([("Маршрут", 26), ("Сервис", 13), ("Транзит", 11), ("Частота / примечание", 50)],
          [[f'<b>{e(x["f"])}</b>', e(x["s"]), f'<b>{e(x["t"])}</b>', e(x["q"])] for x in d["routes"]]))
    a("<h3>Надбавки за негабарит (тариф FESCO FBSS, 08.2026)</h3>")
    a(tbl([("Позиция", 38), ("Ставка", 62)], [[e(x["i"]), f'<b>{e(x["v"])}</b>'] for x in d["oog_costs"]]))
    a("<h3>Габариты, упаковка, консервация</h3>")
    for t in d["pack_rules"]:
        a(f'<div class="box">{t}</div>')
    a("<h3>Логисты и брокеры</h3>")
    a(tbl([("Компания", 14), ("Что делает", 56), ("Контакт", 30)],
          [[f'<b>{e(x["n"])}</b>', x["w"], e(x["k"])] for x in d["log_cos"]]))
    a("<h3>Порядок действий</h3>")
    a("<ol>" + "".join(f"<li>{t}</li>" for t in d["log_steps"]) + "</ol>")

    # --- как писать RFQ ---
    a('<h2 class="pb">17. Как писать RFQ по этим машинам</h2>')
    for t in d["rfq"]["rules"]:
        a(f'<div class="box act">{t}</div>')
    if d["rfq"].get("template"):
        a("<h3>Шаблон запроса</h3>")
        a(f'<div class="box">{d["rfq"]["template"]}</div>')

    # --- источники ---
    a('<h2 class="pb">18. Источники</h2>')
    a(tbl([("№", 3), ("Что подтверждает", 45), ("Ссылка", 52)],
          [[str(i + 1), e(s["what"]), link(s["url"])] for i, s in enumerate(d["sources"])]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(P), encoding="utf-8")
    print(f"OK → {OUT} ({OUT.stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    main()
