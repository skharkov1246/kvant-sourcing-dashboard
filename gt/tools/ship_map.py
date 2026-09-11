#!/usr/bin/env python3
"""Карта закупки по заявке ЛУКОЙЛ: у кого что брать со склада.

Читает gt/data/ship_lukoil.json, собирает gt/docs/ЗАКУПКА-ЛУКОЙЛ.html и печатает
его Chromium'ом в PDF. Вёрстка по docs/ПРАВИЛА-PDF.md: таблицы текут сами, страницы
режет движок печати, текст в ячейках не обрезается.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "gt/data/ship_lukoil.json"
HTML = ROOT / "gt/docs/ЗАКУПКА-ЛУКОЙЛ.html"
PDF = ROOT / "gt/docs/ЗАКУПКА-ЛУКОЙЛ.pdf"
CHROME = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
]

STOCK_RU = {"yes": "да", "no": "нет", "conditional": "условно", "unknown": "н/д"}
COVERS_RU = {"full": "весь объём", "partial": "часть", "no": "нет", "unknown": "н/д"}
KIND_RU = {"oem": "оригинал OEM", "component_maker": "изготовитель узла",
           "aftermarket": "аналог", "unknown": ""}
VERDICT_RU = {
    "in_stock": "на складе продавца",
    "available_lead": "под заказ, срок назван",
    "pn_found_no_stock": "артикул живой, склад не подтверждён",
    "oem_only": "только OEM, канала нет",
    "pn_not_found": "артикул не опознан",
    "not_checked": "не проверялось",
}
ORDER = ["in_stock", "available_lead", "pn_found_no_stock", "oem_only",
         "pn_not_found", "not_checked"]


def E(x) -> str:
    return html.escape(str(x if x is not None else ""))


def ru(n: float, digits: int = 0) -> str:
    return f"{n:,.{digits}f}".replace(",", " ")


def unit_price(r: dict):
    """Цена продавца, приведённая к штуке."""
    p = r.get("price")
    if p in (None, ""):
        return None
    try:
        p = float(p)
    except (TypeError, ValueError):
        return None
    pack = r.get("pack_qty") or 1
    try:
        pack = float(pack) or 1.0
    except (TypeError, ValueError):
        pack = 1.0
    return p / pack


def line_value(r: dict) -> float:
    u = unit_price(r)
    return 0.0 if u is None else u * float(r.get("qty") or 0)


def budget_mid(r: dict):
    lo, hi = r.get("usd_lo"), r.get("usd_hi")
    return None if lo is None or hi is None else (lo + hi) / 2


def money_cell(r: dict) -> str:
    p = r.get("price")
    if p in (None, ""):
        return "—"
    try:
        val = float(p)
    except (TypeError, ValueError):
        return E(p)
    out = f"{ru(val, 2)} {E(r.get('currency') or 'USD')}"
    pack = r.get("pack_qty") or 1
    return out + (f" / уп. {pack}" if pack and pack != 1 else "")


def seller_key(r: dict) -> str:
    """Нормализованное имя продавца: отсекает орг-формы и регистр."""
    name = re.sub(r"\s+", " ", (r.get("seller") or "").strip())
    name = re.sub(r"[,.]?\s*(LLC|Ltd\.?|Inc\.?|GmbH|B\.?V\.?|S\.?r\.?l\.?|S\.?A\.?S\.?|"
                  r"Co\.?|Corp\.?|AG|Limited|ООО|АО)\b\.?", "", name, flags=re.I)
    return name.strip(" -—·").lower()


CSS = """
@page { size: A4 landscape; margin: 9mm 8mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 7.4pt; color: #111; margin: 0; }
h1 { font-size: 17pt; margin: 0 0 1mm; }
h2 { font-size: 11.5pt; margin: 0 0 2mm; border-bottom: 1.4pt solid #111; padding-bottom: 1mm; }
h3 { font-size: 9pt; margin: 4mm 0 1.5mm; }
p { margin: 0 0 2mm; line-height: 1.35; }
.sec { page-break-before: always; }
.sec:first-child { page-break-before: auto; }
.lead { font-size: 8.4pt; }
.dim { color: #666; }
table.t { width: 100%; border-collapse: collapse; table-layout: fixed; margin-bottom: 3mm; }
.t thead { display: table-header-group; }
.t tbody.p { page-break-inside: avoid; }
.t th { background: #111; color: #fff; text-align: left; padding: 1.2mm 1.4mm; font-size: 7pt; }
.t td { padding: 1.2mm 1.4mm; word-wrap: break-word; overflow-wrap: anywhere; vertical-align: top; }
.t tr.d td { border-top: 0.3pt solid #bbb; padding-top: 1.6mm; }
.t tr.a td { font-size: 6.9pt; padding-top: 0.6mm; padding-bottom: 0.2mm; line-height: 1.35; }
.t tr.n td { color: #444; font-size: 6.9pt; padding-top: 0.4mm; padding-bottom: 1.6mm; line-height: 1.3; }
.t tbody.p:nth-of-type(even) td { background: #f6f6f6; }
.pn { font-family: "DejaVu Sans Mono", monospace; font-weight: bold; }
table.k { border-collapse: collapse; margin: 0 0 3mm; width: 100%; }
.k td, .k th { padding: 1.3mm 3mm 1.3mm 0; border-bottom: 0.3pt solid #ddd; vertical-align: top;
               text-align: left; }
.k th { font-size: 7pt; color: #666; border-bottom-width: 1pt; border-bottom-color: #111; }
.k td.l { font-weight: bold; }
.big { font-size: 12pt; font-weight: bold; }
ol, ul { margin: 0 0 3mm; padding-left: 5mm; }
li { margin-bottom: 1.5mm; line-height: 1.35; }
.warn { border-left: 2pt solid #111; padding-left: 3mm; margin-bottom: 3mm; }
.two { column-count: 2; column-gap: 7mm; }
"""


def addressees(r: dict, limit: int = 4) -> str:
    """Строка адресатов: кому писать по этой позиции, с контактом, если он есть."""
    out = []
    for sl in (r.get("sellers") or [])[:limit]:
        bits = [f'<b>{E(sl["seller"])}</b>']
        if sl.get("country"):
            bits.append(E(sl["country"]))
        for em in (sl.get("emails") or [])[:2]:
            bits.append(f'<span class="pn">{E(em)}</span>')
        for ph in (sl.get("phones") or [])[:1]:
            bits.append(E(ph))
        if not (sl.get("emails") or sl.get("phones")):
            bits.append(f'<span class="dim">{E(sl.get("site") or "контакт не собран")}</span>')
        if sl.get("lead_time"):
            bits.append(f'<span class="dim">{E(sl["lead_time"])}</span>')
        out.append(" · ".join(bits))
    return " ⁄ ".join(out)


def rows_table(rows: list[dict], cols: list, with_addr: bool = False) -> str:
    if not rows:
        return '<p class="dim">Строк нет.</p>'
    th = "".join(f'<th style="width:{w}%">{E(t)}</th>' for t, w, _ in cols)
    bodies = []
    for r in rows:
        tds = "".join(f"<td>{fn(r)}</td>" for _, _, fn in cols)
        extra = ""
        if with_addr:
            addr = addressees(r)
            if addr:
                extra = (f'<tr class="a"><td colspan="{len(cols)}">Кому писать: '
                         f"{addr}</td></tr>")
        note = E(r.get("note"))
        nrow = f'<tr class="n"><td colspan="{len(cols)}">{note}</td></tr>' if note else ""
        bodies.append(f'<tbody class="p"><tr class="d">{tds}</tr>{extra}{nrow}</tbody>')
    return f'<table class="t"><thead><tr>{th}</tr></thead>{"".join(bodies)}</table>'


LINE_COLS = [
    ("Артикул", 10, lambda r: f'<span class="pn">{E(r["pn"])}</span>'),
    ("Бренд заявки", 7, lambda r: E(r["man"])),
    ("Наименование", 19, lambda r: E(r["name"])),
    ("Кол-во", 5, lambda r: f'{r.get("qty", 0)} {E(r.get("unit"))}'),
    ("Продавец", 15, lambda r: (E(r.get("seller")) or "—") + (
        f'<br><span class="dim">{KIND_RU.get(r.get("kind"), "")}</span>'
        if KIND_RU.get(r.get("kind")) else "")),
    ("Стр.", 4, lambda r: E(r.get("seller_country"))),
    ("Нал.", 4, lambda r: STOCK_RU.get(r.get("in_stock"), "н/д")),
    ("Срок отгрузки", 13, lambda r: E(r.get("lead_time")) or "—"),
    ("Цена", 9, money_cell),
    ("Объём", 5, lambda r: COVERS_RU.get(r.get("covers_qty"), "н/д")),
    ("Первоисточник", 9, lambda r: E(r.get("real_maker")) or E(r.get("substitute")) or "—"),
]


def supplier_map(rows: list[dict]) -> str:
    """Раздел «у кого что брать»: продавцы с подтверждённым складом."""
    groups = defaultdict(list)
    for r in rows:
        if r["verdict"] in ("in_stock", "available_lead") and r.get("seller"):
            groups[seller_key(r)].append(r)
    if not groups:
        return '<p class="dim">Продавцов с подтверждённым складом нет.</p>'

    cards = []
    for key in sorted(groups, key=lambda k: (-len(groups[k]), -sum(line_value(r) for r in groups[k]))):
        g = groups[key]
        g.sort(key=lambda r: -line_value(r))
        name = max((r.get("seller") or "" for r in g), key=len)
        countries = sorted({r.get("seller_country") for r in g if r.get("seller_country")})
        val = sum(line_value(r) for r in g)
        full = sum(1 for r in g if r.get("covers_qty") == "full")
        stock_now = sum(1 for r in g if r.get("in_stock") == "yes")
        sheets = sorted({r["sheet"] for r in g})
        cats = sorted({r["cat"] for r in g})
        lead = next((r.get("lead_time") for r in g if r.get("lead_time")), "")
        url = next((r.get("seller_url") for r in g if r.get("seller_url")), "")
        pns = ", ".join(r["pn"] for r in g[:24]) + (" …" if len(g) > 24 else "")
        cards.append(
            f'<tbody class="p"><tr class="d">'
            f'<td><b>{E(name)}</b></td>'
            f'<td>{E(", ".join(countries)) or "—"}</td>'
            f'<td>{len(g)}</td><td>{stock_now}</td><td>{full}</td>'
            f'<td>{ru(val) if val else "—"}</td>'
            f'<td>{E(lead) or "—"}</td>'
            f'<td>{E(", ".join(sheets))}</td>'
            f'<td>{E(", ".join(cats))}</td></tr>'
            f'<tr class="n"><td colspan="9"><span class="pn">{E(pns)}</span>'
            + (f' · {E(url)}' if url else "") + "</td></tr></tbody>"
        )
    head = ("".join(f'<th style="width:{w}%">{E(t)}</th>' for t, w in [
        ("Продавец", 18), ("Страна", 8), ("Наших позиций", 7), ("Склад сейчас", 7),
        ("Закрывают весь объём", 8), ("Закупка, USD", 9), ("Типовой срок", 13),
        ("Лист", 8), ("Что берут", 22)]))
    return f'<table class="t"><thead><tr>{head}</tr></thead>{"".join(cards)}</table>'


def makers_table(rows: list[dict]) -> str:
    g = [r for r in rows if (r.get("real_maker") or "").strip()]
    g.sort(key=lambda r: (-line_value(r), r["pn"]))
    if not g:
        return '<p class="dim">Изготовителей не установлено.</p>'
    cols = [
        ("Наш артикул", 11, lambda r: f'<span class="pn">{E(r["pn"])}</span>'),
        ("Шильда заявки", 9, lambda r: E(r["man"])),
        ("Наименование", 22, lambda r: E(r["name"])),
        ("Кол-во", 5, lambda r: f'{r.get("qty", 0)} {E(r.get("unit"))}'),
        ("Реальный изготовитель", 17, lambda r: f'<b>{E(r["real_maker"])}</b>'),
        ("Его номер", 12, lambda r: f'<span class="pn">{E(r.get("real_pn")) or "—"}</span>'),
        ("Лист", 7, lambda r: E(r["sheet"])),
        ("Вердикт", 17, lambda r: VERDICT_RU.get(r["verdict"], "")),
    ]
    return rows_table(g, cols)


def substitutes_table(rows: list[dict]) -> str:
    g = [r for r in rows if (r.get("substitute") or "").strip()
         and not (r.get("real_maker") or "").strip()]
    g.sort(key=lambda r: (r["cat"], -float(r.get("qty") or 0)))
    if not g:
        return '<p class="dim">Позиций под подбор не выделено.</p>'
    cols = [
        ("Артикул", 11, lambda r: f'<span class="pn">{E(r["pn"])}</span>'),
        ("Бренд заявки", 8, lambda r: E(r["man"])),
        ("Наименование", 24, lambda r: E(r["name"])),
        ("Кол-во", 5, lambda r: f'{r.get("qty", 0)} {E(r.get("unit"))}'),
        ("Категория", 12, lambda r: E(r["cat"])),
        ("Чем закрывается", 28, lambda r: f'<b>{E(r["substitute"])}</b>'),
        ("Лист", 7, lambda r: E(r["sheet"])),
    ]
    return rows_table(g, cols)


def gaps_table(rows: list[dict]) -> list:
    """Строки, где цена продавца расходится с нашей вилкой в разы."""
    out = []
    for r in rows:
        u, mid = unit_price(r), budget_mid(r)
        if u is None or not mid or u <= 0:
            continue
        ratio = mid / u if u < mid else -(u / mid)
        if abs(ratio) >= 2.5:
            out.append((abs(ratio), ratio > 0, r, u, mid))
    out.sort(key=lambda t: -abs(t[2].get("qty", 0) * (t[4] - t[3])))
    return out


def build_html(doc: dict) -> str:
    rows = doc["rows"]
    by_sheet = defaultdict(list)
    for r in rows:
        by_sheet[r["sheet"]].append(r)

    def cnt(rs, v):
        return sum(1 for r in rs if r["verdict"] == v)

    sheets = sorted(by_sheet)
    kpi_head = "".join(f"<th>{E(s)}</th>" for s in sheets)
    kpi_rows = []
    for v in ORDER:
        cells = "".join(f"<td>{cnt(by_sheet[s], v)}</td>" for s in sheets)
        total = cnt(rows, v)
        kpi_rows.append(f'<tr><td class="l">{E(VERDICT_RU[v])}</td>{cells}'
                        f'<td class="big">{total}</td></tr>')
    stock = [r for r in rows if r["verdict"] == "in_stock"]
    lead = [r for r in rows if r["verdict"] == "available_lead"]
    full = [r for r in stock if r.get("covers_qty") == "full"]
    makers = [r for r in rows if (r.get("real_maker") or "").strip()]
    subs = [r for r in rows if (r.get("substitute") or "").strip()
            and not (r.get("real_maker") or "").strip()]
    sellers = {seller_key(r) for r in stock + lead if r.get("seller")}
    stock_val = sum(line_value(r) for r in stock)
    gaps = gaps_table(rows)
    over = [g for g in gaps if g[1]]

    kpi = (f'<table class="k"><thead><tr><th>Вердикт</th>{kpi_head}<th>Всего</th></tr></thead>'
           f'<tbody>{"".join(kpi_rows)}</tbody></table>')

    totals = f"""
<table class="k"><tbody>
<tr><td class="l">Позиций в заявке</td><td class="big">{len(rows)}</td>
<td class="dim">{" · ".join(f"{s} — {len(by_sheet[s])}" for s in sheets)}</td></tr>
<tr><td class="l">Продавцов с живым складом</td><td class="big">{len(sellers)}</td>
<td class="dim">по ним разложена карта закупки в разделе 2</td></tr>
<tr><td class="l">Закупка по складским строкам</td><td class="big">{ru(stock_val)} USD</td>
<td class="dim">по ценам карточек, на заявленные количества; цены есть не у всех строк</td></tr>
<tr><td class="l">Вскрыто изготовителей узлов</td><td class="big">{len(makers)}</td>
<td class="dim">имя реального изготовителя под шильдой OEM — раздел 3</td></tr>
<tr><td class="l">Закрывается стандартом или подбором</td><td class="big">{len(subs)}</td>
<td class="dim">крепёж, РТИ, прокладки, клеммы, предохранители — раздел 4</td></tr>
<tr><td class="l">Строк с ценой вне нашей вилки в 2,5+ раза</td><td class="big">{len(gaps)}</td>
<td class="dim">из них завышено в ТКП — {len(over)}; раздел 5</td></tr>
</tbody></table>"""

    todo = """
<h2>Что делать — по убыванию отдачи</h2>
<ol>
<li><b>Складские строки — в твёрдый оффер сегодня.</b> По каждой запросить: остаток числом
на сегодня, срок под наше количество, цену за весь объём, срок действия оффера, Инкотермс,
условия возврата. Карточка на сайте — витрина, а не бронь: пока остаток не подтверждён
письмом, строка не считается отгружаемой.</li>
<li><b>Писать изготовителям узлов, а не перепродавцам OEM.</b> Раздел 3 — готовый список
адресатов: наш номер, реальный изготовитель, его каталожный номер. По нашей же практике
прямой канал изготовителя дешевле каталога OEM в 2–10 раз. Это самая дешёвая экономия в
работе: письмо вместо торга.</li>
<li><b>Класс C не искать, а подбирать.</b> Раздел 4 — строки, которые закрываются стандартом
(DIN/ISO/ASME, AS568, bonded seal, ASME B16.20). Искать оригинальный номер OEM по ним
бессмысленно и вредно: тот же метиз по стандарту дешевле кратно при нулевом техническом
риске. Нужно получить от заказчика типоразмер, класс прочности и материал.</li>
<li><b>Проверить строки из раздела 5 до отправки ТКП.</b> Расхождение цены на порядок
заказчик заметит раньше нас. Завышение бьёт по доверию ко всему ТКП, занижение — по марже.</li>
<li><b>Запрашивать пакетами по бренду.</b> Сплошная рассылка файла на тысячу строк дала ноль
ответов за тринадцать попыток; узкий запрос по знакомому бренду отвечается в двух третях
случаев. Карта закупки в разделе 2 уже сгруппирована по продавцам — это и есть готовая
разбивка на письма.</li>
<li><b>Ненайденные артикулы — работа на расшифровку, а не на поиск.</b> Повторный поиск тем
же способом ничего не даст: это номера-цепочки, кириллические суффиксы учётной системы
заказчика, позиции без размеров и снятые с производства с заменой. Каждая расшифровка
меняет вердикт, а не формулировку.</li>
</ol>"""

    origin = f"""
<div class="warn">
<p><b>Что это за документ.</b> Сплошная выкладка по всей заявке ЛУКОЙЛа — оба листа,
{len(rows)} уникальных каталожных номеров. Сведены три поколения проверки: ранняя 08.2026,
проверка 505 строк «Энергосетей» и сплошная проверка остатка 09.2026. Позднейшая проверка
перекрывает раннюю; строки без проверки помечены явно, а не выданы за отсутствие товара.</p>
<p><b>Что считать фактом.</b> Наличие, срок и цена сняты с карточек товара у продавцов.
Страница поисковой выдачи доказательством не считалась. <b>Ни одна строка не является
котировкой</b> — это витрины продавцов, а не ответы на наш запрос. Часть площадок (Radwell,
eBay, Zoro, Grainger, DO Supply, shop.solarturbines.com) закрыта от автоматического чтения,
поэтому «артикул не опознан» местами означает «не подтверждено», а не «на рынке нет».</p>
</div>"""

    parts = [
        '<div class="sec">',
        "<h1>Закупка по заявке ЛУКОЙЛ: у кого что брать</h1>",
        f'<p class="lead dim">Листы «Энергосети» и «НВН» · обновлено {E(doc.get("updated"))}</p>',
        totals, origin,
        "<h3>Покрытие по вердиктам</h3>", kpi,
        "</div>",
        '<div class="sec"><h2>1. Что делать</h2>' + todo + "</div>",
        f'<div class="sec"><h2>2. Карта закупки: у кого что брать — {len(sellers)} продавцов</h2>'
        '<p class="lead">Только продавцы с подтверждённым складом или названным сроком. '
        'Сортировка по числу наших позиций: сверху те, у кого одним письмом закрывается больше '
        'всего строк. «Склад сейчас» — позиции с безусловным наличием; остальные условные '
        '(«отгрузим, если есть»).</p>' + supplier_map(rows) + "</div>",
        f'<div class="sec"><h2>3. Изготовители узлов под шильдой OEM — {len(makers)}</h2>'
        '<p class="lead">Правило первоисточника: на узле стоит имя реального изготовителя, а не '
        'OEM. Прямой каталог изготовителя дешевле в 2–10 раз. Это список адресатов для прямых '
        'запросов.</p>' + makers_table(rows) + "</div>",
        f'<div class="sec"><h2>4. Закрывается стандартом или подбором — {len(subs)}</h2>'
        '<p class="lead">Класс C: искать оригинальный номер OEM по этим строкам не нужно. '
        'В колонке «чем закрывается» — конкретное обозначение; в примечании — что уточнить '
        'у заказчика, чтобы подобрать однозначно.</p>' + substitutes_table(rows) + "</div>",
    ]

    if gaps:
        cols = [
            ("Артикул", 11, lambda r: f'<span class="pn">{E(r["pn"])}</span>'),
            ("Бренд", 8, lambda r: E(r["man"])),
            ("Наименование", 25, lambda r: E(r["name"])),
            ("Кол-во", 5, lambda r: f'{r.get("qty", 0)} {E(r.get("unit"))}'),
            ("Наша вилка, USD/шт", 11,
             lambda r: f'{ru(r["usd_lo"], 2)} — {ru(r["usd_hi"], 2)}'),
            ("Цена продавца, USD/шт", 11, lambda r: ru(unit_price(r) or 0, 2)),
            ("Разница", 8, lambda r: r["_gap"]),
            ("Сдвиг по строке, USD", 11, lambda r: r["_shift"]),
            ("Лист", 10, lambda r: E(r["sheet"])),
        ]
        g_rows = []
        for ratio, overpriced, r, u, mid in gaps:
            rr = dict(r)
            rr["_gap"] = ("завышено в %.1f×" % ratio) if overpriced else ("занижено в %.1f×" % ratio)
            rr["_shift"] = ru(abs((mid - u) * float(r.get("qty") or 0)))
            g_rows.append(rr)
        parts.append(
            f'<div class="sec"><h2>5. Цена расходится с нашей вилкой в 2,5+ раза — {len(gaps)}</h2>'
            '<p class="lead">«Сдвиг по строке» — на сколько меняется сумма строки, если взять цену '
            'продавца вместо нашей вилки. Проверять до отправки ТКП: завышение заказчик заметит '
            'раньше нас, занижение съест маржу.</p>' + rows_table(g_rows, cols) + "</div>")

    n = 6
    for sheet in sheets:
        srows = by_sheet[sheet]
        first = True
        for v in ORDER:
            vr = [r for r in srows if r["verdict"] == v]
            if not vr:
                continue
            vr.sort(key=lambda r: -line_value(r))
            if first:  # заголовок листа живёт на той же странице, что и первая таблица
                head = (f'<h2>{n}. Построчно: лист «{E(sheet)}» — {len(srows)} позиций</h2>'
                        '<p class="lead">Разделы идут от отгружаемого к неопознанному, '
                        'внутри каждого — по убыванию стоимости строки.</p>'
                        f'<h3>{E(VERDICT_RU[v])} — {len(vr)}</h3>')
                n += 1
                first = False
            else:
                head = f'<h2>«{E(sheet)}» · {E(VERDICT_RU[v])} — {len(vr)}</h2>'
            parts.append(f'<div class="sec">{head}' + rows_table(vr, LINE_COLS, with_addr=True) + "</div>")

    return ("<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
            "<title>Закупка ЛУКОЙЛ</title>"
            f"<style>{CSS}</style></head><body>{''.join(parts)}</body></html>")


def main() -> int:
    if not DATA.exists():
        print(f"нет {DATA} — сначала gt/tools/ship_merge.py", file=sys.stderr)
        return 1
    doc = json.loads(DATA.read_text())
    HTML.parent.mkdir(parents=True, exist_ok=True)
    HTML.write_text(build_html(doc), encoding="utf-8")
    print(f"HTML: {HTML.name} {HTML.stat().st_size:,} байт")

    exe = next((c for c in CHROME if Path(c).exists()), None)
    if not exe:
        print("Chromium не найден — PDF не собран", file=sys.stderr)
        return 1
    subprocess.run(
        [exe, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
         "--run-all-compositor-stages-before-draw", "--virtual-time-budget=120000",
         f"--print-to-pdf={PDF}", HTML.as_uri()],
        check=True, capture_output=True,
    )
    print(f"PDF:  {PDF.name} {PDF.stat().st_size:,} байт")
    return 0


if __name__ == "__main__":
    sys.exit(main())
