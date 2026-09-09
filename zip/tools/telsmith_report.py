#!/usr/bin/env python3
"""Отчёт по сорсингу ЗИП щековой дробилки Telsmith 3858: куда идти и что запрашивать.

Вход:  zip/data/telsmith_3858.json    (потребность, каталог, классы — telsmith_parse.py)
       zip/data/telsmith_suppliers.json (поставщики и расшифровки кодов — по итогам проверки)
Выход: zip/orders/ТЕЛСМИТ-СОРСИНГ.html  (+ PDF через zip/tools/telsmith_pdf.py)
"""
import html
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D, OUT = ROOT / "data", ROOT / "orders"
e = lambda s: html.escape(str(s if s is not None else ""))
mln = lambda v: f"{v/1e6:,.2f}".replace(",", " ") if isinstance(v, (int, float)) else "—"
rub = lambda v: f"{v:,.0f}".replace(",", " ") if isinstance(v, (int, float)) else "—"


def load(n, default=None):
    p = D / n
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else (default if default is not None else {})


CSS = """
@page{size:A4 landscape;margin:11mm 9mm}
body{font:10px/1.45 'DejaVu Sans',Arial,sans-serif;color:#111;margin:0}
h1{font-size:18px;margin:0 0 3px}
h2{font-size:12.5px;margin:13px 0 4px;background:#1b2330;color:#fff;padding:4px 8px;border-radius:3px;page-break-after:avoid}
h3{font-size:11px;margin:9px 0 3px;color:#0b3d91;page-break-after:avoid}
.mut{color:#555;font-size:9px}
table{border-collapse:collapse;width:100%;margin:3px 0} tr{page-break-inside:avoid}
th,td{border:1px solid #bbb;padding:3px 5px;text-align:left;vertical-align:top;font-size:9px}
th{background:#eef2f7} b{color:#0b3d91}
.box{border:1px solid #ccc;border-left:4px solid #1a7f37;border-radius:4px;padding:5px 9px;margin:5px 0;page-break-inside:avoid;font-size:9.5px}
.warn{border-left-color:#c62828;background:#fff8f8}
.num{text-align:right;white-space:nowrap}
.flag{color:#c62828;font-size:8.5px}
.ok{color:#1a7f37;font-weight:bold} .no{color:#c62828}
ul{margin:2px 0 2px 14px;padding:0} li{margin:1px 0}
"""

# порядок вывода классов — по деньгам, но футеровка/распорная первыми как основной расход
ORDER = ["распорная", "футеровка", "вал", "гидравлика", "смазка", "рти", "крепёж", "электрика", "прочее"]


def main():
    d = load("telsmith_3858.json")
    sup = load("telsmith_suppliers.json", {"suppliers": [], "crossrefs": [], "notes": []})
    m, st = d["machine"], d["stats"]
    classes = {c["key"]: c for c in d["classes"]}
    need = d["need"]

    H = [f'<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>']
    H.append(f"""<h1>Щековая дробилка Telsmith 3858: куда идти за запчастями</h1>
<div class="mut">Реестр исследований ГШО · {date.today().strftime('%d.%m.%Y')} · {m['type']}, {m['oem']} · {m['site']}<br>
Потребность — {st['need_positions']} позиций на {st['need_sum_rub']/1e6:.1f} млн ₽ по действующему каналу ({m['channel']}).
Состав изделия — {st['catalog_rows']} строк каталога в {st['catalog_nodes']} узлах, сопоставлено с потребностью {st['matched']} позиций.
Цены — рубли без НДС, склад Норильск.</div>""")

    # 1. Экономика потребности
    H.append('<h2>1. Из чего складывается потребность</h2>')
    H.append('<table><tr><th style="width:42%">Класс деталей</th><th class="num">Позиций</th>'
             '<th class="num">Сумма, млн ₽</th><th class="num">Доля</th><th>Кто это делает</th></tr>')
    tot = st["need_sum_rub"]
    MAKERS = {
        "распорная": "Литейный завод + расчёт на срез: плита работает как предохранитель",
        "футеровка": "Литьё марганцовистой стали Mn13Cr2/Mn18Cr2 с термообработкой",
        "вал": "Тяжёлая поковка и расточка; подшипники — серийные, покупаются отдельно",
        "гидравлика": "Серийные изделия под кодом Telsmith: цилиндры, насосы, клапаны",
        "смазка": "Станции смазки и фильтрация — серийные изделия",
        "рти": "Уплотнения по размеру: серийная номенклатура",
        "крепёж": "Метизы по чертежу, класс прочности по документации",
        "электрика": "Реле и датчики — серийные приборы",
        "прочее": "Смешанный состав: пальцы, планки, кожухи, РВД",
    }
    for k in ORDER:
        c = classes.get(k)
        if not c:
            continue
        H.append(f'<tr><td><b>{e(c["title"])}</b></td><td class="num">{c["positions"]}</td>'
                 f'<td class="num">{mln(c["sum_rub"])}</td><td class="num">{c["sum_rub"]/tot*100:.1f}%</td>'
                 f'<td>{e(MAKERS.get(k,""))}</td></tr>')
    H.append('</table>')

    # 2. Внутренние коды — главный резерв по цене
    inner = [n for n in need if n["pn_kind"] == "внутренний код покупного"]
    inner_sum = sum(n["sum_rub"] for n in inner if isinstance(n["sum_rub"], (int, float)))
    H.append(f'''<div class="box warn"><b>Главный резерв: {len(inner)} позиций на {inner_sum/1e6:.1f} млн ₽
({inner_sum/tot*100:.0f}% суммы) идут под внутренними кодами Telsmith вида 62M77, 14T47, 63R53.</b>
За такими кодами стоят серийные изделия других производителей — насосы, цилиндры, подшипники, уплотнения,
перемаркированные под номенклатуру дробилки. Установив изготовителя и его номер, позицию покупают
как каталожную, без наценки за принадлежность к дробилке. Ориентир масштаба наценки по прейскуранту:
уплотнительное кольцо 63R53 — 1,04 млн ₽ за штуку, сферический роликоподшипник 14T47 — 1,64 млн ₽ за штуку.</div>''')

    if sup.get("crossrefs"):
        H.append('<h3>Расшифровка внутренних кодов</h3><table>'
                 '<tr><th>Код Telsmith</th><th>Наименование</th><th class="num">Цена по прейскуранту, ₽/шт</th>'
                 '<th>Изготовитель</th><th>Номер изготовителя</th><th>Надёжность расшифровки</th></tr>')
        by_pn = {n["oem"]: n for n in need if n.get("oem")}
        for c in sup["crossrefs"]:
            n = by_pn.get(c["telsmith_pn"], {})
            price = n.get("price_rub")
            H.append(f'<tr><td><b>{e(c["telsmith_pn"])}</b></td><td>{e(n.get("name",""))[:46]}</td>'
                     f'<td class="num">{rub(price)}</td>'
                     f'<td>{e(c["real_maker"])}</td><td>{e(c["real_pn"])}</td><td>{e(c["confidence"])[:70]}</td></tr>')
        H.append('</table>')

    # 3. Поставщики
    H.append('<h2>2. Куда идти: проверенные изготовители</h2>')
    if not sup.get("suppliers"):
        H.append('<div class="box">Проверка поставщиков не выполнена — раздел пуст.</div>')
    else:
        H.append('<table><tr><th style="width:20%">Компания</th><th style="width:8%">Город</th>'
                 '<th style="width:9%">Тип</th><th style="width:15%">Закрывает классы</th>'
                 '<th style="width:19%">Чем подтверждён опыт</th><th style="width:14%">Контакт</th>'
                 '<th>Очередь запроса</th></tr>')
        for s in sup["suppliers"]:
            ct = "<br>".join(x for x in (e(s.get("email")), e(s.get("phone")), e(s.get("whatsapp"))) if x) \
                 or '<span class="mut">контакт не подтверждён</span>'
            H.append(f'<tr><td><b>{e(s["name"])[:54]}</b><br><span class="mut">{e(s.get("site",""))[:44]}</span></td>'
                     f'<td>{e(s.get("city",""))[:22]}</td><td>{e(s.get("kind",""))[:26]}</td>'
                     f'<td>{e(", ".join(s.get("covers_classes",[])))[:60]}</td>'
                     f'<td>{e(s.get("evidence",""))[:170]}</td><td>{ct}</td>'
                     f'<td>{e(s.get("recommend",""))[:60]}</td></tr>')
        H.append('</table>')
        for s in sup["suppliers"]:
            if s.get("risks"):
                H.append(f'<div class="box warn"><b>{e(s["name"])[:60]} — на что смотреть:</b><ul>'
                         + "".join(f"<li>{e(r)}</li>" for r in s["risks"][:5]) + '</ul></div>')

    # 4. Что запрашивать
    if sup.get("requests"):
        H.append('<h2>3. Что запрашивать и как принимать</h2>')
        for blk in sup["requests"]:
            H.append(f'<h3>{e(blk["title"])}</h3><div class="box"><b>Запрос:</b><ul>'
                     + "".join(f"<li>{e(x)}</li>" for x in blk.get("request", [])) + '</ul>')
            if blk.get("acceptance"):
                H.append('<b>Приёмка:</b><ul>' + "".join(f"<li>{e(x)}</li>" for x in blk["acceptance"]) + '</ul>')
            H.append('</div>')

    # 5. Позиции первой волны
    H.append('<h2>4. Позиции для первого запроса</h2>')
    first = sorted([n for n in need if isinstance(n["sum_rub"], (int, float))],
                   key=lambda x: -x["sum_rub"])[:24]
    H.append('<table><tr><th>Element ID</th><th>Номер Telsmith</th><th style="width:34%">Наименование</th>'
             '<th>Узел</th><th class="num">Кол-во</th><th class="num">Цена, ₽/шт</th>'
             '<th class="num">Сумма, млн ₽</th><th>Вид номера</th></tr>')
    for n in first:
        H.append(f'<tr><td>{e(n["eid"])}</td><td><b>{e(n["oem"])}</b></td><td>{e(n["name"])[:60]}</td>'
                 f'<td>{e(n["node"])[:26]}</td><td class="num">{e(n["qty"])}</td>'
                 f'<td class="num">{rub(n["price_rub"])}</td>'
                 f'<td class="num">{mln(n["sum_rub"])}</td><td>{e(n["pn_kind"])}</td></tr>')
    H.append('</table>')

    if sup.get("notes"):
        H.append('<h2>5. Замечания по рынку</h2><div class="box"><ul>'
                 + "".join(f"<li>{e(x)}</li>" for x in sup["notes"]) + '</ul></div>')

    H.append('<div class="mut" style="margin-top:8px">Источники: прейскурант дилера и каталог запасных частей '
             'Telsmith 3858 (документы заказчика), реестр поставщиков ГШО, открытые данные производителей. '
             'Контакты приведены только те, что опубликованы на сайтах компаний.</div></body></html>')

    OUT.mkdir(exist_ok=True)
    (OUT / "ТЕЛСМИТ-СОРСИНГ.html").write_text("\n".join(H), encoding="utf-8")
    print(f"HTML готов: {st['need_positions']} позиций, поставщиков {len(sup.get('suppliers', []))}, "
          f"расшифровок кодов {len(sup.get('crossrefs', []))}")


if __name__ == "__main__":
    main()
