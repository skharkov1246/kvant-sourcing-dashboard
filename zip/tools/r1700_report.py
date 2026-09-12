#!/usr/bin/env python3
"""Печатный отчёт по Caterpillar R1700G → zip/orders/R1700-СОРСИНГ.html.

Справочная выжимка досье (zip/data/r1700.json) для чтения и распечатки: шаги,
паспорт, документация, перечень запчастей с аналогами, каналы закупки, цены,
торги, факты сделок, пробелы. Рабочие данные живут на портале (вкладка
«CAT R1700G»), этот файл — только чтобы держать в руках.

Вёрстка по docs/ПРАВИЛА-PDF.md: таблицы текут сами, шапка повторяется, строка не
рвётся, ни одной фиксированной высоты и ни одной обрезки текста.

Выход: zip/orders/R1700-СОРСИНГ.html (PDF собирает zip/tools/r1700_pdf.py)
Запуск: python zip/tools/r1700_report.py
"""
import html
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D, OUT = ROOT / "data", ROOT / "orders"

e = lambda s: html.escape(str(s if s is not None else ""))


def num(v):
    return f"{v:,}".replace(",", " ") if isinstance(v, (int, float)) else "—"


CSS = """
@page{size:A4 landscape;margin:11mm 9mm}
body{font:10px/1.45 'DejaVu Sans',Arial,sans-serif;color:#111;margin:0}
h1{font-size:19px;margin:0 0 4px}
h2{font-size:13px;margin:15px 0 5px;background:#1b2330;color:#fff;padding:5px 9px;border-radius:3px;page-break-after:avoid}
h3{font-size:11.5px;margin:10px 0 3px;color:#0b3d91;page-break-after:avoid}
.mut{color:#555;font-size:9px}
table{border-collapse:collapse;width:100%;margin:4px 0;table-layout:fixed}
thead{display:table-header-group}
tr{page-break-inside:avoid}
th,td{border:1px solid #bbb;padding:3px 5px;text-align:left;vertical-align:top;font-size:9px;
word-wrap:break-word;overflow-wrap:anywhere}
th{background:#eef2f7}
b{color:#0b3d91}
.pn{font-family:'DejaVu Sans Mono',monospace;white-space:nowrap}
.num{text-align:right;white-space:nowrap}
.box{border:1px solid #ccc;border-left:4px solid #1a7f37;border-radius:4px;padding:6px 10px;margin:6px 0;
page-break-inside:avoid;font-size:9.5px}
.warn{border-left-color:#c62828;background:#fff8f8}
.key{border-left-color:#b8860b;background:#fffdf5}
.kpi{display:flex;gap:8px;margin:6px 0;flex-wrap:wrap}
.kpi div{flex:1 1 130px;border:1px solid #ccc;border-radius:4px;padding:6px 9px}
.kpi b{display:block;font-size:15px;color:#111}
.sec{page-break-before:always}
.sec:first-of-type{page-break-before:auto}
ul{margin:3px 0 3px 15px;padding:0}li{margin:1.5px 0}
"""


def table(cols, rows, cls=""):
    """cols — [(заголовок, ширина %, класс ячейки)]; сумма ширин ровно 100."""
    th = "".join(f'<th style="width:{w}%">{e(t)}</th>' for t, w, _ in cols)
    body = ""
    for r in rows:
        tds = "".join(f'<td class="{c}">{v}</td>' for (_, _, c), v in zip(cols, r))
        body += f"<tr>{tds}</tr>"
    return f'<table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'


def build():
    d = json.loads((D / "r1700.json").read_text(encoding="utf-8"))
    s, m, own = d["stats"], d["machine"], d["own"]
    cst, btx = own["customs"], own.get("bitrix") or {}
    parts = d["parts"]
    H = []

    # ── шапка и итог ────────────────────────────────────────────────────────
    H.append(f"""<h1>Caterpillar R1700G — комплектующие: перечень, аналоги, каналы, торги</h1>
<div class="mut">Погрузочно-доставочная машина подземных горных работ. Собрано {e(d['updated'])} ·
источников {num(s['sources'])} · направлений разведки {num(s['recon_slices'])} ·
рабочая версия с фильтрами и поиском — на портале ГШО, вкладка «CAT R1700G».</div>
<div class="kpi">
<div><b>{num(s['parts'])}</b>деталей</div>
<div><b>{num(s['parts_with_alt'])}</b>с аналогом</div>
<div><b>{num(s['alts'])}</b>кроссов / {num(s['alt_brands'])} брендов</div>
<div><b>{num(s['docs'])}</b>документов</div>
<div><b>{num(s['orgs'])}</b>каналов</div>
<div><b>{num(s['prices'])}</b>цен</div>
<div><b>{num(s['customs_importers'])}</b>импортёров Cat</div>
<div><b>{num(s['odm_candidates'])}</b>заводов ODM</div>
</div>""")

    if d.get("playbook"):
        steps = "".join(f'<div class="box key"><b>{e(p["step"])}</b><br>{e(p["why"])}'
                        + (f'<br><span class="mut">{e(p["how"])}</span>' if p.get("how") else "")
                        + "</div>" for p in d["playbook"])
        H.append(f"<h2>Что делать (посчитано по данным ниже)</h2>{steps}")

    # ── паспорт ─────────────────────────────────────────────────────────────
    if d.get("variants"):
        H.append('<div class="sec"><h2>Модификации и взаимозаменяемость</h2>' + table(
            [("Модель", 10, ""), ("Годы", 8, ""), ("Двигатель", 12, ""), ("кВт", 5, "num"),
             ("Ковш, м³", 7, "num"), ("Гр/п, т", 6, "num"), ("Масса, т", 6, "num"),
             ("Шины", 10, ""), ("Префикс", 8, "pn"), ("Примечание", 28, "")],
            [[e(v.get("model")), e(v.get("years")), e(v.get("engine")), e(v.get("power_kw")),
              e(v.get("bucket_m3")), e(v.get("payload_t")), e(v.get("weight_t")), e(v.get("tyres")),
              e(v.get("serial_prefix")), e(v.get("note"))] for v in d["variants"]]) + "</div>")
    if d.get("specs"):
        H.append("<h3>Параметры</h3>" + table(
            [("Параметр", 26, ""), ("Значение", 30, ""), ("Модификация", 22, ""), ("Доверие", 10, ""),
             ("Источник", 12, "")],
            [[e(x.get("param")), f'{e(x.get("value"))} {e(x.get("unit"))}', e(x.get("variant")),
              e(x.get("confidence")), e((x.get("source") or "")[:60])] for x in d["specs"]]))

    # ── документация ────────────────────────────────────────────────────────
    if d.get("docs"):
        H.append('<div class="sec"><h2>Документация по машине · ' + num(len(d["docs"])) + "</h2>"
                 + '<div class="mut">Номер формы — ключ поиска у дилера, в SIS и на вторичном рынке. '
                   "Каталог (SEBP) нужен, чтобы собрать ведомость на машину; схемы (UENR/RENR) — "
                   "чтобы ремонтировать электрику и гидравлику.</div>" + table(
            [("Форма", 9, "pn"), ("Название", 27, ""), ("Тип", 12, ""), ("Покрывает", 16, ""),
             ("Яз.", 4, ""), ("Где взять", 20, ""), ("Проверка", 12, "")],
            [[e(x.get("form")), e(x.get("title_ru")), e(x.get("kind")), e(x.get("covers")),
              e(x.get("lang")), e(x.get("where")), e(x.get("verdict") or x.get("confidence"))]
             for x in d["docs"]]) + "</div>")

    # ── запчасти по узлам ───────────────────────────────────────────────────
    H.append('<div class="sec"><h2>Перечень запчастей · ' + num(len(parts)) + "</h2>"
             + '<div class="mut">«Наша база» — номер пришёл из перечня ЗИП ГШО, применимость подтверждена '
               "заявкой заказчика. «Снят» — разведка номер нашла, проверка источником не подтвердила: "
               "в заявку такой номер не ставить.</div>")
    by_node = {}
    for p in parts:
        by_node.setdefault(p["node"] or "— узел не определён", []).append(p)
    for node in sorted(by_node):
        rows = []
        for p in by_node[node]:
            alts = " · ".join(f'{a["brand"]} {a["pn"]}' for a in (p.get("alts") or [])[:6])
            price = p.get("price_usd") or (
                f'{p.get("price_eur_min")}–{p.get("price_eur_max")} EUR' if p.get("price_eur_min") else "")
            rows.append([f'<span class="pn">{e(p["pn"])}</span>', e(p.get("name_ru")),
                         e((p.get("applic") or "")[:90]), e(p.get("qty")), e(p.get("interval")),
                         e(", ".join(p.get("kv") or [])), e(alts), e(price),
                         e(p.get("bitrix_status") or ""), e(p.get("verdict") or p.get("confidence"))])
        H.append(f"<h3>{e(node)} · {len(rows)}</h3>" + table(
            [("Cat PN", 9, "pn"), ("Наименование", 18, ""), ("Применимость", 16, ""), ("Кол", 3, "num"),
             ("Интервал", 6, ""), ("Наш номер", 9, "pn"), ("Аналоги", 21, ""), ("Цена", 7, "num"),
             ("Битрикс", 5, ""), ("Проверка", 6, "")], rows))
    H.append("</div>")

    # ── каналы ──────────────────────────────────────────────────────────────
    for lane, title, hint in (
        ("dealers", "Официальные каналы Caterpillar",
         "Первый вопрос дилеру — не цена, а готовность отгружать в РФ."),
        ("aftermarket", "Заводы неоригинала",
         "Письмо изготовителя о применимости на торгах весит больше прайса перекупщика."),
        ("traders", "Поставщики и склады РФ/СНГ",
         "Наличие на складе в РФ снимает срок и таможню — этим и выигрываются короткие закупки."),
    ):
        rows = [[e(o["org"]), e(o.get("kind")), f'{e(o.get("country"))} {e(o.get("city"))}',
                 e(o.get("role") or o.get("brands")), e(o.get("stock")),
                 e(o.get("email")), e(o.get("phone")), e((o.get("note") or "")[:140]),
                 e(o.get("verdict") or o.get("confidence"))]
                for o in d["orgs"] if o["slice"] == lane]
        if not rows:
            continue
        H.append(f'<div class="sec"><h2>{e(title)} · {len(rows)}</h2><div class="mut">{e(hint)}</div>'
                 + table([("Организация", 17, ""), ("Тип", 9, ""), ("Где", 9, ""), ("Роль и бренды", 16, ""),
                          ("Наличие и срок", 11, ""), ("Почта", 11, ""), ("Телефон", 8, ""),
                          ("Чем полезен", 13, ""), ("Пров.", 6, "")], rows) + "</div>")

    # ── таможня ─────────────────────────────────────────────────────────────
    H.append('<div class="sec"><h2>Ввоз Caterpillar по таможенной выгрузке</h2>'
             f'<div class="mut">{e(cst["note"])} Строк с признаком Caterpillar — {num(len(cst["rows"]))}, '
             f'импортёров {num(len(cst["importers"]))}, отправителей {num(len(cst["exporters"]))}. '
             "Это не мнение, а факт отгрузки: у этих компаний канал уже работает.</div>")
    if cst["r1700_rows"]:
        H.append("<h3>Прямо по R1700G</h3>" + table(
            [("Дата", 7, ""), ("Импортёр", 16, ""), ("Отправитель", 16, ""), ("Проис.", 5, ""),
             ("Условия", 9, ""), ("ТН ВЭД", 8, "pn"), ("Описание", 39, "")],
            [[e(x.get("date")), e(x.get("importer")), e(x.get("exporter")), e(x.get("origin")),
              f'{e(x.get("incoterms"))} {e(x.get("place"))}', e(x.get("hs10")), e(x.get("desc"))]
             for x in cst["r1700_rows"]]))
    H.append("<h3>Импортёры</h3>" + table(
        [("Организация", 70, ""), ("Отгрузок", 30, "num")],
        [[e(x["org"]), num(x["shipments"])] for x in cst["importers"][:40]]))
    H.append("<h3>Отправители</h3>" + table(
        [("Организация", 70, ""), ("Отгрузок", 30, "num")],
        [[e(x["org"]), num(x["shipments"])] for x in cst["exporters"][:40]]))
    H.append("<h3>Маршруты</h3>" + table(
        [("Происхождение", 25, ""), ("Инкотермс", 25, ""), ("Место", 30, ""), ("Отгрузок", 20, "num")],
        [[e(x["origin"]), e(x["incoterms"]), e(x["place"]), num(x["shipments"])]
         for x in cst["lanes"][:25]]) + "</div>")

    # ── цены ────────────────────────────────────────────────────────────────
    if d.get("prices"):
        H.append('<div class="sec"><h2>Ценовые ориентиры · ' + num(len(d["prices"])) + "</h2>" + table(
            [("PN", 10, "pn"), ("Наименование", 22, ""), ("Уровень", 9, ""), ("Бренд", 11, ""),
             ("Цена", 7, "num"), ("Вал.", 4, ""), ("Продавец", 17, ""), ("Регион", 6, ""),
             ("Дата", 7, ""), ("Дов.", 7, "")],
            [[e(x.get("pn")), e(x.get("name_ru") or x.get("name")), e(x.get("tier")), e(x.get("brand")),
              e(x.get("price")), e(x.get("currency")), e(x.get("seller")), e(x.get("region")),
              e(x.get("date")), e(x.get("confidence"))] for x in d["prices"]]) + "</div>")

    # ── торги ───────────────────────────────────────────────────────────────
    t = d["tenders"]
    if t.get("platforms") or t.get("owners") or t.get("checklist"):
        H.append('<div class="sec"><h2>Торги: где, у кого и по каким требованиям</h2>')
        if t.get("platforms"):
            H.append("<h3>Площадки</h3>" + table(
                [("Площадка", 16, ""), ("Закон", 10, ""), ("Что там", 30, ""), ("Как искать", 32, ""),
                 ("Дов.", 12, "")],
                [[e(x.get("name")), e(x.get("law")), e(x.get("what")), e(x.get("how")),
                  e(x.get("confidence"))] for x in t["platforms"]]))
        if t.get("owners"):
            H.append("<h3>Эксплуатанты машин</h3>" + table(
                [("Организация", 24, ""), ("Регион", 20, ""), ("Машины", 18, ""), ("Примечание", 38, "")],
                [[e(x.get("org")), e(x.get("region")), e(x.get("machines")), e(x.get("note"))]
                 for x in t["owners"]]))
        if t.get("checklist"):
            H.append("<h3>Чтобы заявку не отклонили</h3>" + table(
                [("Требование", 34, ""), ("Зачем", 66, "")],
                [[e(x.get("step")), e(x.get("why"))] for x in t["checklist"]]))
        H.append("</div>")

    # ── Битрикс ─────────────────────────────────────────────────────────────
    bs = btx.get("stats") or {}
    rows = []
    for r in (btx.get("sold") or []) + (btx.get("quoted") or []):
        deals = " · ".join(f'{x.get("date")} {x.get("client")} — {x.get("status")}'
                           for x in r.get("deals") or [])
        rows.append([f'<span class="pn">{e(r.get("pn"))}</span>', e(r.get("name")), e(r.get("status")),
                     e(r.get("machine")), f'{e(r.get("price_min"))}–{e(r.get("price_max"))} {e(r.get("price_cur"))}',
                     e(deals)])
    if rows:
        H.append('<div class="sec"><h2>Что по Caterpillar уже проходило сделку</h2>'
                 f'<div class="mut">Режим сбора: {"живой портал" if btx.get("live") else "снимки репозитория"}. '
                 f'Продавали {num(bs.get("sold"))}, квотировали {num(bs.get("quoted"))}, '
                 f'ценовых фактов {num(bs.get("prices"))}. Исходы: '
                 + ", ".join(f"{k} — {v}" for k, v in (bs.get("deal_outcomes") or {}).items()) + "</div>"
                 + table([("PN", 9, "pn"), ("Наименование", 20, ""), ("Статус", 9, ""),
                          ("Применимость", 12, ""), ("Вилка", 10, "num"), ("Сделки", 40, "")], rows)
                 + "</div>")

    # ── пробелы ─────────────────────────────────────────────────────────────
    gaps = d.get("gaps") or {}
    if gaps:
        H.append('<div class="sec"><h2>Чего в досье нет</h2>'
                 + "".join(f'<div class="box warn"><b>{e(k)}</b><br>{e(v)}</div>' for k, v in gaps.items())
                 + "</div>")

    doc = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Caterpillar R1700G — сорсинг комплектующих</title><style>{CSS}</style></head>
<body>{''.join(H)}
<div class="mut" style="margin-top:10px">Собрано {e(date.today().isoformat())} ·
источник данных: zip/data/r1700.json · портал ГШО → вкладка «CAT R1700G» ·
разведка с вердиктами: zip/data/r1700_recon/</div>
</body></html>"""
    OUT.mkdir(exist_ok=True)
    p = OUT / "R1700-СОРСИНГ.html"
    p.write_text(doc, encoding="utf-8")
    print(f"{p.relative_to(ROOT.parent)}: {p.stat().st_size:,} байт | деталей {len(parts)}, "
          f"узлов {len(by_node)}, документов {len(d.get('docs') or [])}, каналов {len(d['orgs'])}")
    return p


if __name__ == "__main__":
    build()
