# -*- coding: utf-8 -*-
"""Лист для сорсеров по срочной заявке CAT: где искать сток и кого запрашивать.

Вход:  zip/data/cat_stock_2026-09.json  (строит zip/tools/cat_stock.py)
Выход: zip/orders/CAT-СТОКИ-СОРСИНГ.html + zip/orders/cat_stock.csv
       PDF собирает zip/tools/cat_stock_pdf.py
"""
import csv
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "cat_stock_2026-09.json"
OUT = ROOT / "orders"
e = lambda s: html.escape(str(s if s is not None else ""))

CLS_COLOR = {"массовый": "#1a7f37", "узловой": "#b26a00", "тяжёлый": "#c62828"}
LANE_COLOR = {
    "ЮАР · гидравлика": "#1a7f37", "ЮАР + CAT": "#0b3d91", "ЮАР · отгрузка": "#2a6f97",
    "КНР · CAT": "#b26a00", "ЮАР · логистика": "#5a6672",
    "ЮАР · происхождение": "#8a7300", "КНР · канал": "#5a6672",
}
GEO_COLOR = {"ЮАР": "#1a7f37", "КНР": "#b26a00"}

CSS = """
@page{size:A4 landscape;margin:10mm 8mm}
body{font:10px/1.45 'DejaVu Sans',Arial,sans-serif;color:#111;margin:0}
h1{font-size:17px;margin:0 0 3px}
h2{font-size:12.5px;margin:11px 0 4px;background:#1b2330;color:#fff;padding:4px 8px;border-radius:3px;page-break-after:avoid}
.mut{color:#555;font-size:9px}
table{border-collapse:collapse;width:100%;margin:3px 0}
tr{page-break-inside:avoid}
th,td{border:1px solid #bbb;padding:3px 5px;text-align:left;vertical-align:top;font-size:9px}
th{background:#eef2f7}
b{color:#0b3d91}
.num{text-align:right;white-space:nowrap}
.pn{font-family:'DejaVu Sans Mono',monospace;white-space:nowrap}
.box{border:1px solid #ccc;border-left:4px solid #1a7f37;border-radius:4px;padding:5px 9px;margin:5px 0;page-break-inside:avoid;font-size:9.5px}
.warn{border-left-color:#c62828;background:#fff8f8}
.tag{display:inline-block;border-radius:3px;padding:1px 5px;color:#fff;font-size:8.5px;white-space:nowrap}
.kpi{display:flex;gap:6px;margin:6px 0}
.kpi div{flex:1;border:1px solid #ccc;border-radius:4px;padding:5px 8px}
.kpi .v{font-size:16px;font-weight:700;color:#0b3d91}
.kpi .l{font-size:8.5px;color:#555}
ol,ul{margin:2px 0 2px 15px;padding:0} li{margin:1px 0}
.letter{border:1px solid #bbb;border-radius:4px;padding:7px 10px;background:#fbfbfb;font-size:9.5px;white-space:pre-wrap}
"""


def tag(text, color):
    return f'<span class="tag" style="background:{color}">{e(text)}</span>'


def main() -> int:
    d = json.loads(SRC.read_text(encoding="utf-8"))
    req, mach, ch = d["request"], d["machine"], {c["id"]: c for c in d["channels"]}
    OUT.mkdir(exist_ok=True)
    H = [f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>CAT: стоки и запросы</title>'
         f'<style>{CSS}</style></head><body>']

    H.append(f'<h1>Срочная заявка CAT: {req["lines"]} позиций — склады Китая и ЮАР</h1>'
             f'<div class="mut">Лист для сорсеров · собран {e(d["updated"])} · назначение {e(req["destination"])} · '
             f'срок поставки по перечню {e(req["deadline"])} · отгрузка в течение недели, то есть до {e(req["ship_by"])}. '
             f'<b>{e(req["geo_note"])}</b> '
             f'Источники: перечень заказчика, наша таможенная выгрузка за '
             f'{e(" … ".join(d["customs_stats"]["period"]))} '
             f'({d["customs_stats"]["za_rows"]} строк из ЮАР и {d["customs_stats"]["cat_rows"]} строк genuine CAT) '
             f'и справочник ODM. Наличие по конкретным номерам здесь не подтверждено: остатки складов закрыты, '
             f'их даёт только запрос.</div>')

    H.append(f'<div class="kpi">'
             f'<div><div class="v">{req["lines"]}</div><div class="l">строк / {req["units"]} шт</div></div>'
             f'<div><div class="v">{req["days_left"]}</div><div class="l">дней до срока 15.10, а не 7</div></div>'
             f'<div><div class="v">{sum(1 for p in d["positions"] if p["stock_class"] == "массовый")}</div>'
             f'<div class="l">позиций закрываются складским стоком</div></div>'
             f'<div><div class="v">{sum(1 for p in d["positions"] if p["stock_class"] == "тяжёлый")}</div>'
             f'<div class="l">позиции определяют бюджет и срок</div></div>'
             f'<div><div class="v">{sum(1 for x in d["leads"] if x["za_ship_from"])}</div>'
             f'<div class="l">компании реально отгружают из ЮАР</div></div>'
             f'</div>')

    H.append(f'<div class="box"><b>Машина.</b> {e(mach["verdict"])}. {e(mach["basis"])}<br>'
             f'<b style="color:#c62828">Чего не хватает:</b> {e(mach["unknown"])}.</div>')
    H.append('<div class="box warn"><b>Что понять до рассылки</b><ol>'
             + "".join(f"<li>{e(f)}</li>" for f in d["findings"]) + "</ol></div>")

    H.append('<h2>Куда идти: каналы Китая и ЮАР с реальными сроками до Красноярска</h2>')
    H.append('<table><tr><th style="width:5%">Страна</th><th style="width:19%">Канал</th><th style="width:8%">Тип</th>'
             '<th style="width:8%">Срок, дн.</th><th style="width:25%">Почему он в списке</th>'
             '<th style="width:20%">Что спрашивать</th><th>Риск</th></tr>')
    for c in d["channels"]:
        H.append(f'<tr><td>{tag(c["geo"], GEO_COLOR[c["geo"]])}</td>'
                 f'<td><b>{e(c["title"])}</b><br><span class="mut">{e(c["place"])}</span></td>'
                 f'<td>{e(c["kind"])}</td><td class="num">{e(c["days"])}</td>'
                 f'<td>{e(c["why"])}</td><td>{e(c["ask"])}</td><td class="mut">{e(c["risk"])}</td></tr>')
    H.append('</table>')

    H.append('<h2>Позиции: узел, ходовой класс и маршрут сорсера</h2>')
    H.append('<table><tr><th class="num" style="width:3%">№</th><th style="width:8%">Парт-номер</th>'
             '<th style="width:20%">Наименование</th><th class="num" style="width:4%">Кол-во</th>'
             '<th style="width:15%">Узел</th><th style="width:8%">Класс</th><th style="width:6%">ТН ВЭД</th>'
             '<th style="width:16%">Куда идти по порядку</th><th>Что спросить</th></tr>')
    for p in d["positions"]:
        route = " → ".join(ch[i]["short"] for i in p["route"])
        H.append(f'<tr><td class="num">{p["pp"]}</td><td class="pn"><b>{e(p["pn_cat"])}</b></td>'
                 f'<td>{e(p["name_ru"])}<br><span class="mut">{e(p["name_en"])}</span></td>'
                 f'<td class="num">{p["qty"]}</td><td>{e(p["group"])}</td>'
                 f'<td>{tag(p["stock_class"], CLS_COLOR[p["stock_class"]])}</td>'
                 f'<td class="pn mut">{e(p["hs_hint"])}</td><td>{e(route)}</td>'
                 f'<td class="mut">{e(p["ask"])}</td></tr>')
    H.append('</table>')

    H.append('<h2>Сводка по узлам</h2><table><tr><th style="width:24%">Узел</th><th class="num">Позиций</th>'
             '<th class="num">Штук</th><th style="width:20%">Ходовые классы</th><th>Парт-номера</th></tr>')
    for g in d["groups"]:
        cls = ", ".join(f"{k} {v}" for k, v in g["classes"].items())
        H.append(f'<tr><td><b>{e(g["group"])}</b></td><td class="num">{g["positions"]}</td>'
                 f'<td class="num">{g["qty"]}</td><td>{e(cls)}</td>'
                 f'<td class="pn mut">{e(", ".join(g["pns"]))}</td></tr>')
    H.append('</table>')

    H.append('<h2>Кого запрашивать: у кого канал из ЮАР и Китая уже работает</h2>')
    H.append('<div class="mut">Отбор по нашей таможенной выгрузке: компании, которые реально ввозили в РФ товар '
             'из ЮАР, и компании, которым отгружал сам CATERPILLAR. Считается страна отправления, а не происхождения. '
             'Сверху — те, кто отгружает из ЮАР ровно наши '
             'классы: гидроцилиндры и клапаны гидротрансмиссий. Порядок в таблице — порядок обзвона. '
             'Контактов в базе нет: ИНН нужен, чтобы найти компанию в ЕГРЮЛ и в Битриксе перед звонком.</div>')
    H.append('<table><tr><th style="width:17%">Компания</th><th style="width:8%">ИНН</th>'
             '<th style="width:11%">Чем полезен</th><th class="num" style="width:8%">Отгрузок из ЮАР / КНР</th>'
             '<th style="width:7%">Последняя</th><th style="width:17%">Маршруты</th>'
             '<th>Что везли (из декларации)</th></tr>')
    for lead in d["leads"][:14]:
        ev = "; ".join(f'{x["date"]} {x["exporter"][:26]}: {x["desc"][:70]}' for x in lead["evidence"][:2])
        H.append(f'<tr><td><b>{e(lead["importer"])}</b>'
                 + (f'<br><span class="mut">{e(lead["role_note"])}</span>' if lead["role_note"] else "")
                 + f'</td><td class="pn">{e(lead["inn"])}</td>'
                 f'<td>{tag(lead["lane"], LANE_COLOR[lead["lane"]])}'
                 f'<br><span class="mut">{e(lead["lane_note"])}</span></td>'
                 f'<td class="num">{lead["za_ship_from"]} / {lead["cn_shipments"]}</td>'
                 f'<td>{e(lead["last"])}</td>'
                 f'<td class="mut">{e("; ".join(lead["routes"][:2]))}</td>'
                 f'<td class="mut">{e(ev)}</td></tr>')
    H.append('</table>')

    am = d["aftermarket"]
    H.append('<h2>Китай: заводы афтермаркета из нашей базы ODM</h2>')
    if am["note"]:
        H.append(f'<div class="box warn">{e(am["note"])}</div>')
    if am["relevant"]:
        H.append(f'<div class="mut">По Caterpillar в справочнике {am["cat_records"]} записей, из них профильных под '
                 f'эту заявку — {len(am["relevant"])}. Остальное фильтроэлементы, сюда не годятся.</div>')
        H.append('<table><tr><th style="width:26%">Завод</th><th style="width:8%">Страна</th>'
                 '<th style="width:8%">Уверенность</th><th style="width:34%">Что делает</th><th>Ссылка</th></tr>')
        for a in am["relevant"]:
            H.append(f'<tr><td><b>{e(a["name"])}</b></td><td>{e(a["country"])}</td><td>{e(a["confidence"])}</td>'
                     f'<td>{e(a["makes"])}</td><td class="mut">{e(a["url"])}</td></tr>')
        H.append('</table>')

    H.append('<h2>План на пять рабочих дней</h2><table><tr><th style="width:12%">Когда</th><th>Что сделать</th></tr>')
    for step in d["playbook"]:
        H.append(f'<tr><td><b>{e(step["day"])}</b></td><td>{e(step["what"])}</td></tr>')
    H.append('</table>')

    pns = ", ".join(p["pn_cat"].replace(" (?)", "") for p in d["positions"])
    H.append('<h2>Готовый текст запроса — вставить в почту Битрикса</h2>')
    H.append('<div class="letter">' + e(
        "Subject / Тема: Stock request — Caterpillar parts, 29 line items, shipment within one week\n\n"
        "Добрый день!\n\n"
        "Просим подтвердить наличие на вашем складе и условия отгрузки по перечню ниже.\n"
        "Назначение — г. Красноярск, Россия. Срок поставки по проекту 15.10.2026, отгрузка требуется\n"
        "в течение недели, доставка — авиа.\n\n"
        f"Парт-номера Caterpillar: {pns}.\n"
        "По позиции 294221 номер уточняется — просим подтвердить, читается ли он у вас как номер КПП.\n\n"
        "Просим в ответе:\n"
        "1. Разделить перечень на «в наличии на складе» и «под заказ» — по наличию нужен отдельный счёт.\n"
        "2. По каждой позиции: количество на складе, цена за штуку с НДС, срок отгрузки.\n"
        "3. По позициям под заказ: срок готовности и базис (EXW / FOB).\n"
        "4. Указать статус детали: оригинал Cat, Cat Reman, восстановленная, б/у, аналог.\n"
        "5. Вес брутто и габариты упаковки по каждой позиции — нужны для расчёта авиафрахта.\n"
        "6. Подтвердить готовность отгрузить в течение недели с даты оплаты и возможность\n"
        "   поставки в Российскую Федерацию.\n\n"
        "Количества по позициям вышлем сразу после подтверждения наличия.\n"
        "Ответ просим до конца рабочего дня завтра — заявка срочная.\n\n"
        "С уважением,\nотдел сорсинга") + '</div>')

    H.append(f'<div class="mut" style="margin-top:8px">Собрано zip/tools/cat_stock_table.py по '
             f'zip/data/cat_stock_2026-09.json. Наличие складов не проверялось программно — '
             f'закрытых остатков в открытых источниках нет.</div>')
    H.append('</body></html>')

    html_path = OUT / "CAT-СТОКИ-СОРСИНГ.html"
    html_path.write_text("\n".join(H), encoding="utf-8")

    csv_path = OUT / "cat_stock.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["№", "Парт-номер", "Наименование RU", "Наименование EN", "Кол-во", "Ед.",
                    "Узел", "Ходовой класс", "ТН ВЭД (ориентир)", "Куда идти по порядку", "Что спросить"])
        for p in d["positions"]:
            w.writerow([p["pp"], p["pn_cat"], p["name_ru"], p["name_en"], p["qty"], p["unit"],
                        p["group"], p["stock_class"], p["hs_hint"],
                        " → ".join(ch[i]["short"] for i in p["route"]), p["ask"]])

    print(f"HTML: {html_path.name} {html_path.stat().st_size:,} байт · CSV: {csv_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
