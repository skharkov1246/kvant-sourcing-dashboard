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
    pr = load("telsmith_prices.json", {"estimates": [], "basis": [], "fx_rub_usd": None})
    est = {i["pn"]: i for x in pr.get("estimates", []) for i in x.get("items", []) if i.get("pn")}
    fx = pr.get("fx_rub_usd")
    rec = load("telsmith_recon.json", {"items": [], "summary": ""})
    recon = {i["pn"]: i for i in rec.get("items", [])}
    br = load("telsmith_brands.json", {"synth": {}, "brands": []})
    m, st = d["machine"], d["stats"]
    classes = {c["key"]: c for c in d["classes"]}
    need = d["need"]

    H = [f'<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>']
    H.append(f"""<h1>Щековая дробилка Telsmith 3858: куда идти за запчастями</h1>
<div class="mut">Реестр исследований ГШО · {date.today().strftime('%d.%m.%Y')} · {m['type']}, {m['oem']} · {m['site']}<br>
Потребность — {st['need_positions']} позиций на {st['need_sum_rub']/1e6:.1f} млн ₽ по действующему каналу ({m['channel']}).
Состав изделия — {st['catalog_rows']} строк каталога в {st['catalog_nodes']} узлах, сопоставлено с потребностью {st['matched']} позиций.
Цены — рубли без НДС, склад Норильск.</div>""")

    if m.get("series_note"):
        H.append('<div class="box warn"><b>Как искать эту машину.</b> ' + e(m["series_note"]) + ". "
                 + e(m.get("search_key", "")) + " " + e(m.get("status", "")) + "</div>")

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

    # 2. Аутмаркет-цена и запас
    if est and fx:
        rows, d_sum, a_sum = [], 0, 0
        for n in need:
            it = est.get(n.get("oem")) if n.get("oem") else None
            if not it or not isinstance(n["sum_rub"], (int, float)) or not n["qty"] \
                    or not isinstance(n["price_rub"], (int, float)):
                continue
            a_unit = it["delivered_usd"] * fx
            d_sum += n["sum_rub"]
            a_sum += a_unit * n["qty"]
            rows.append((n, it, a_unit))
        if rows:
            H.append("<h2>2. Сколько это стоит на аутмаркете и какой запас</h2>")
            H.append(
                '<div class="box"><b>По ' + str(len(rows)) + " позициям, охваченным оценкой: прейскурант "
                + f"{d_sum/1e6:.1f} млн \u20bd, аутмаркет с доставкой в Норильск {a_sum/1e6:.1f} млн \u20bd. "
                + f"Запас — {(d_sum-a_sum)/1e6:.1f} млн \u20bd, цена дилера выше в {d_sum/a_sum:.1f} раза.</b> "
                + f"Курс расчёта — {fx:.2f} \u20bd за доллар США. Аутмаркет — независимые китайские изготовители; "
                + "оригинал через дистрибьюторов и обходные каналы в расчёт не входят. Оценка ориентировочная: "
                + "до получения предложений заводов её следует считать рамкой для переговоров, а не ценой закупки.</div>")
            H.append('<table><tr><th>Номер</th><th style="width:24%">Наименование</th><th class="num">Кол-во</th>'
                     '<th class="num">Дилер, \u20bd/шт</th><th class="num">Аутмаркет FOB, $/шт</th>'
                     '<th class="num">С доставкой, \u20bd/шт</th><th class="num">Кратность</th>'
                     '<th class="num">Запас, млн \u20bd</th><th style="width:20%">Что это на самом деле</th>'
                     '<th>Надёжность</th></tr>')
            for n, it, a_unit in sorted(rows, key=lambda r: -(r[0]["sum_rub"] - r[2] * r[0]["qty"])):
                gap = n["sum_rub"] - a_unit * n["qty"]
                mult = n["price_rub"] / a_unit if a_unit else 0
                fob = f'{it["unit_usd_low"]:,.0f}\u2013{it["unit_usd_high"]:,.0f}'.replace(",", " ")
                H.append("<tr><td><b>" + e(n["oem"]) + "</b></td><td>" + e(n["name"])[:44] + "</td>"
                         + '<td class="num">' + e(n["qty"]) + "</td>"
                         + '<td class="num">' + rub(n["price_rub"]) + "</td>"
                         + '<td class="num">' + fob + "</td>"
                         + '<td class="num">' + rub(a_unit) + "</td>"
                         + f'<td class="num">{mult:.0f}\u00d7</td><td class="num">{gap/1e6:.2f}</td>'
                         + "<td>" + e(it["identified_as"])[:88] + "</td><td>" + e(it["confidence"])[:32] + "</td></tr>")
            H.append("</table>")
            methods = [e(x["cls"]) + ": " + e(x["method"]) for x in pr.get("estimates", []) if x.get("method")]
            if methods:
                H.append('<div class="box"><b>Как считалось:</b><ul>'
                         + "".join("<li>" + m + "</li>" for m in methods) + "</ul></div>")

    # 3. Сходимость двух оценок
    if recon:
        conv = [i for i in recon.values() if i.get("converges")]
        H.append("<h2>3. Сходимость встречных оценок: где считать надёжным, где копать</h2>")
        H.append('<div class="box">Цена считалась двумя независимыми путями. Снизу вверх — от материала и переделов: '
                 "масса, марка стали, литьё или ковка, механическая и термическая обработка, контроль, прибыль завода. "
                 "Сверху вниз — от рынка: опубликованные цены аутмаркета на такую же или ближайшую деталь. "
                 "Совпадение двух путей в пределах двух раз означает, что цифру можно брать в переговоры. "
                 "Расхождение больше — сигнал, что мы чего-то не знаем о самой позиции: массы, комплектности, типоразмера. "
                 "Сошлось по " + str(len(conv)) + " позициям из " + str(len(recon)) + ".</div>")
        if rec.get("summary"):
            H.append('<div class="box">' + e(rec["summary"]) + "</div>")
        H.append('<table><tr><th>Номер</th><th style="width:22%">Наименование</th>'
                 '<th class="num">Снизу вверх, $/шт</th><th class="num">Сверху вниз, $/шт</th>'
                 '<th class="num">Расхождение</th><th class="num">В работу, $/шт</th>'
                 '<th class="num">Дилер, \u20bd/шт</th><th class="num">Кратность</th>'
                 '<th style="width:30%">Куда копать</th></tr>')
        by_pn = {n["oem"]: n for n in need if n.get("oem")}
        for i in sorted(recon.values(), key=lambda x: (x.get("converges", False), x.get("pn", ""))):
            n = by_pn.get(i["pn"], {})
            dp = n.get("price_rub")
            mult = (dp / (i["take_usd"] * fx)) if (fx and i.get("take_usd") and isinstance(dp, (int, float))) else None
            cls_ = "" if i.get("converges") else ' class="flag"'
            H.append("<tr><td><b>" + e(i["pn"]) + "</b></td><td>" + e(i.get("name", ""))[:42] + "</td>"
                     + '<td class="num">' + (f'{i["bottom_up_usd"]:,.0f}'.replace(",", " ") if i.get("bottom_up_usd") else "—") + "</td>"
                     + '<td class="num">' + (f'{i["top_down_usd"]:,.0f}'.replace(",", " ") if i.get("top_down_usd") else "—") + "</td>"
                     + "<td" + cls_ + ">" + e(i.get("spread", ""))[:26] + "</td>"
                     + '<td class="num"><b>' + (f'{i["take_usd"]:,.0f}'.replace(",", " ") if i.get("take_usd") else "—") + "</b></td>"
                     + '<td class="num">' + rub(dp) + "</td>"
                     + '<td class="num">' + (f"{mult:.0f}\u00d7" if mult else "—") + "</td>"
                     + "<td>" + e(i.get("dig", ""))[:190] + "</td></tr>")
        H.append("</table>")

    # 4. Внутренние коды — главный резерв по цене
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
    # Каналы снабжения по итогам разбора марок
    sy = br.get("synth") or {}
    if sy.get("channels"):
        H.append("<h2>5. Другие имена машины и каналы снабжения</h2>")
        if sy.get("verdict"):
            H.append('<div class="box">' + e(sy["verdict"]) + "</div>")
        H.append('<table><tr><th style="width:22%">Канал</th><th style="width:34%">Что делать</th>'
                 '<th style="width:22%">Что даёт</th><th>Риск</th></tr>')
        for c in sy["channels"]:
            H.append("<tr><td><b>" + e(c["channel"])[:150] + "</b></td><td>" + e(c["what_to_do"])[:420] + "</td>"
                     + "<td>" + e(c["value"])[:240] + "</td><td>" + e(c["risk"])[:240] + "</td></tr>")
        H.append("</table>")

    H.append('<h2>6. Куда идти: изготовители аутмаркета</h2>')
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
        H.append('<h2>7. Что запрашивать и как принимать</h2>')
        for blk in sup["requests"]:
            H.append(f'<h3>{e(blk["title"])}</h3><div class="box"><b>Запрос:</b><ul>'
                     + "".join(f"<li>{e(x)}</li>" for x in blk.get("request", [])) + '</ul>')
            if blk.get("acceptance"):
                H.append('<b>Приёмка:</b><ul>' + "".join(f"<li>{e(x)}</li>" for x in blk["acceptance"]) + '</ul>')
            H.append('</div>')

    # 5. Позиции первой волны
    H.append('<h2>8. Позиции для первого запроса</h2>')
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
        H.append('<h2>9. Замечания по рынку</h2><div class="box"><ul>'
                 + "".join(f"<li>{e(x)}</li>" for x in sup["notes"]) + '</ul></div>')

    H.append('<div class="mut" style="margin-top:8px">Источники: прейскурант дилера и каталог запасных частей '
             'Telsmith 3858 (документы заказчика), реестр поставщиков ГШО, открытые данные производителей. '
             'Контакты приведены только те, что опубликованы на сайтах компаний.</div></body></html>')

    OUT.mkdir(exist_ok=True)
    (OUT / "ТЕЛСМИТ-СОРСИНГ.html").write_text("\n".join(H), encoding="utf-8")
    print(f"HTML готов: {st['need_positions']} позиций, поставщиков {len(sup.get('suppliers', []))}, "
          f"расшифровок кодов {len(sup.get('crossrefs', []))}, оценок цен {len(est)}, сверок {len(recon)}")


if __name__ == "__main__":
    main()
