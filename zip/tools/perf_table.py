# -*- coding: utf-8 -*-
"""Таблица для сорсеров: классы деталей гидроперфораторов Sandvik / Epiroc —
куда идти, что запрашивать, чтобы провести тестирование.

Вход:  zip/data/perf_sourcing_map.json   (карта: позиции, поставщики, КП, материалы)
       zip/data/perf_test_requests.json  (что запрашивать — по классам, после проверки)
Выход: zip/orders/ПЕРФОРАТОРЫ-СОРСИНГ.html/.pdf + zip/orders/perf_sourcing.csv
"""
import csv
import html
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D, OUT = ROOT / "data", ROOT / "orders"
e = lambda s: html.escape(str(s or ""))


def short(name):
    """Короткое имя поставщика для сводной таблицы: до первой скобки или « — »."""
    for sep in (" (", " — "):
        if sep in name:
            name = name.split(sep)[0]
    return name.strip(" ,/")


def load(n):
    p = D / n
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


CSS = """
@page{size:A4 landscape;margin:11mm 9mm}
body{font:10px/1.45 'DejaVu Sans',Arial,sans-serif;color:#111;margin:0}
h1{font-size:18px;margin:0 0 3px} h2{font-size:12.5px;margin:12px 0 4px;background:#1b2330;color:#fff;padding:4px 8px;border-radius:3px;page-break-after:avoid}
.mut{color:#555;font-size:9px}
table{border-collapse:collapse;width:100%;margin:3px 0} tr{page-break-inside:avoid}
th,td{border:1px solid #bbb;padding:3px 5px;text-align:left;vertical-align:top;font-size:9px}
th{background:#eef2f7} b{color:#0b3d91}
.box{border:1px solid #ccc;border-left:4px solid #1a7f37;border-radius:4px;padding:5px 9px;margin:5px 0;page-break-inside:avoid;font-size:9.5px}
.warn{border-left-color:#c62828;background:#fff8f8}
.num{text-align:right;white-space:nowrap} .flag{color:#c62828;font-size:8.5px}
.k{display:inline-block;border:1px solid #bbb;border-radius:3px;padding:1px 6px;margin:1px 3px 1px 0;font-size:9px}
ul{margin:2px 0 2px 14px;padding:0} li{margin:1px 0}
"""


def main():
    m = load("perf_sourcing_map.json")
    tr = {t["class"]: t for t in load("perf_test_requests.json").get("classes", [])}
    classes = m.get("classes", [])
    H = [f'<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>']
    tot = sum(c["positions"] for c in classes)
    H.append(f"""<h1>Гидроперфораторы Sandvik / Epiroc: куда идти и что запрашивать для тестирования</h1>
<div class="mut">Таблица для сорсеров по классам деталей · {date.today().strftime('%d.%m.%Y')} · {tot} позиций базы ЗИП в {len(classes)} классах.
Ничего нового не искалось: переработаны база ЗИП, статусы Битрикса, заводские КП, CRM прозвона, реестр поставщиков и материаловедческая стратегия.
Цены — USD за штуку, EXW, медиана по позициям класса: «CN» — лучший китайский завод, «WMS» — реселлер уровня OEM (ориентир, в тестовую закупку не идёт).</div>""")

    # сводная
    H.append('<h2>Сводка по классам</h2><table><tr><th>Класс детали</th><th class="num">Позиций</th><th class="num">С КП</th><th class="num">Продавали</th>'
             '<th>Машины</th><th class="num">CN, $</th><th class="num">WMS, $</th><th>Куда идти первым</th><th>Приоритет теста</th></tr>')
    for c in classes:
        t = tr.get(c["class"], {})
        first = e(short(c["suppliers"][0]["name"])) if c["suppliers"] else "—"
        if c["suppliers"] and c["suppliers"][0].get("flag"):
            first += ' <span class="flag">(дочернее Atlas Copco)</span>'  # подробно — в примечании у класса
        H.append(f'<tr><td><b>{e(c["title"])}</b></td><td class="num">{c["positions"]}</td><td class="num">{c["with_quote"]}</td>'
                 f'<td class="num">{c["sold"]}</td><td>{e(", ".join(c["machines"][:5]))}</td>'
                 f'<td class="num">{c["price_best_cn_usd"] if c["price_best_cn_usd"] is not None else "—"}</td>'
                 f'<td class="num">{c["price_wms_usd"] if c["price_wms_usd"] is not None else "—"}</td>'
                 f'<td>{first}</td><td>{e(t.get("priority",""))}</td></tr>')
    H.append('</table>')

    for c in classes:
        t = tr.get(c["class"], {})
        H.append(f'<h2>{e(c["title"])} — {c["positions"]} поз.</h2>')
        if t.get("summary"):
            H.append(f'<div class="box">{e(t["summary"])}</div>')
        # куда идти
        H.append('<table><tr><th style="width:24%">Куда идти</th><th style="width:9%">Страна</th><th style="width:25%">Контакт</th>'
                 '<th class="num" style="width:6%">КП</th><th style="width:14%">Статус в CRM</th><th>Примечание</th></tr>')
        for s in c["suppliers"][:6]:
            ct = "<br>".join(x for x in (e(s.get("email")), e(s.get("phone")), e(s.get("whatsapp"))) if x) or '<span class="mut">контакт не найден</span>'
            H.append(f'<tr><td><b>{e(s["name"][:60])}</b></td><td>{e(str(s.get("country"))[:18])}</td><td>{ct}</td>'
                     f'<td class="num">{s.get("quotes") or ""}</td><td>{e(s.get("crm_stage"))}</td>'
                     f'<td class="flag">{e(s.get("flag"))}</td></tr>')
        H.append('</table>')
        # что запрашивать
        if t.get("request"):
            H.append('<div class="box"><b>Что запрашивать для теста:</b><ul>' + "".join(f"<li>{e(x)}</li>" for x in t["request"]) + '</ul>')
            if t.get("acceptance"):
                H.append('<b>Приёмка образца:</b><ul>' + "".join(f"<li>{e(x)}</li>" for x in t["acceptance"]) + '</ul>')
            if t.get("risks"):
                H.append('<b>На что смотреть:</b><ul>' + "".join(f"<li>{e(x)}</li>" for x in t["risks"]) + '</ul>')
            H.append('</div>')
        # позиции-кандидаты
        H.append('<table><tr><th style="width:11%">KV</th><th style="width:12%">Номер OEM</th><th style="width:30%">Наименование</th><th style="width:19%">Машина</th><th style="width:9%">Битрикс</th><th>КП (завод: $)</th></tr>')
        for x in c["examples"][:8]:
            q = ", ".join(f"{a}: {b}" for a, b in x.get("quotes", []))
            H.append(f'<tr><td>{e(x.get("kv"))}</td><td>{e(x["pn"])}</td><td>{e(x["name"][:52])}</td><td>{e(str(x.get("model"))[:30])}</td><td>{e(x.get("bitrix"))}</td><td>{e(q)}</td></tr>')
        H.append('</table>')

    H.append('<div class="mut" style="margin-top:8px">Источники: zip/data — positions, price_records, supplier_crm, material_strategy, bom; pnw/data — supplier_master, part_suppliers. '
             'Контакты поставщиков собраны с их сайтов. Где данных нет — прочерк.</div></body></html>')
    (OUT / "ПЕРФОРАТОРЫ-СОРСИНГ.html").write_text("\n".join(H), encoding="utf-8")

    with open(OUT / "perf_sourcing.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Класс", "Позиций", "С КП", "Продавали", "Машины", "CN $", "WMS $", "Ранг", "Поставщик", "Страна", "Email", "Телефон", "WhatsApp/WeChat", "КП по классу", "Статус CRM", "Пометка", "Приоритет теста"])
        for c in classes:
            t = tr.get(c["class"], {})
            for i, s in enumerate(c["suppliers"][:6], 1):
                w.writerow([c["title"], c["positions"], c["with_quote"], c["sold"], ", ".join(c["machines"][:5]),
                            c["price_best_cn_usd"] or "", c["price_wms_usd"] or "", i, s["name"], s.get("country", ""),
                            s.get("email", ""), s.get("phone", ""), s.get("whatsapp", ""), s.get("quotes") or "",
                            s.get("crm_stage", ""), s.get("flag", ""), t.get("priority", "")])
    print(f"HTML + CSV готовы: {len(classes)} классов, рекомендаций по тесту: {len(tr)}")


if __name__ == "__main__":
    main()
