#!/usr/bin/env python3
"""Отчёт по наличию позиций ТКП ЛУКОЙЛ/Энергосети у продавцов.

Читает gt/data/ship_energoseti.json, собирает gt/docs/НАЛИЧИЕ-ТКП-ЭНЕРГОСЕТИ.html
и печатает его Chromium'ом в PDF. Вёрстка — по docs/ПРАВИЛА-PDF.md: таблица течёт
сама, страницы движок режет сам, текст в ячейках не обрезается.
"""
from __future__ import annotations

import html
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "gt/data/ship_energoseti.json"
HTML = ROOT / "gt/docs/НАЛИЧИЕ-ТКП-ЭНЕРГОСЕТИ.html"
PDF = ROOT / "gt/docs/НАЛИЧИЕ-ТКП-ЭНЕРГОСЕТИ.pdf"
CHROME = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
]

VERDICT_RU = {
    "in_stock": "на складе",
    "available_lead": "под заказ, срок назван",
    "pn_found_no_stock": "артикул живой, склада нет",
    "oem_only": "только OEM, канала нет",
    "pn_not_found": "артикул не найден",
    "not_checked": "не проверено",
}
STOCK_RU = {"yes": "да", "no": "нет", "conditional": "условно", "unknown": "н/д"}
COVERS_RU = {"full": "весь объём", "partial": "часть", "no": "нет", "unknown": "н/д"}
KIND_RU = {
    "oem": "оригинал OEM",
    "component_maker": "изготовитель узла",
    "aftermarket": "аналог",
    "unknown": "н/д",
}


def E(x) -> str:
    return html.escape(str(x if x is not None else ""))


def money(r: dict) -> str:
    """Цена за единицу продажи, как её дал продавец."""
    p = r.get("price")
    if p is None or p == "":
        return "—"
    try:
        val = float(p)
    except (TypeError, ValueError):
        return E(p)
    cur = r.get("currency") or "USD"
    s = f"{val:,.2f}".replace(",", " ").replace(".00", "")
    pack = r.get("pack_qty") or 1
    return f"{s} {E(cur)}" + (f" / уп. {pack}" if pack and pack != 1 else "")


def line_value(r: dict) -> float:
    """Стоимость строки по цене продавца, приведённой к штуке."""
    p = r.get("price")
    if p in (None, ""):
        return 0.0
    try:
        val = float(p)
    except (TypeError, ValueError):
        return 0.0
    pack = r.get("pack_qty") or 1
    try:
        pack = float(pack) or 1.0
    except (TypeError, ValueError):
        pack = 1.0
    return val / pack * float(r.get("qty") or 0)


COLS = [
    ("Артикул", 10, lambda r: f'<span class="pn">{E(r["pn"])}</span>'),
    ("Производитель", 7, lambda r: E(r["man"])),
    ("Наименование", 17, lambda r: E(r["name"])),
    ("Кол-во", 4, lambda r: f'{r.get("qty", 0)} {E(r.get("unit", ""))}'),
    ("Продавец", 13, lambda r: seller_cell(r)),
    ("Стр.", 4, lambda r: E(r.get("seller_country"))),
    ("Нал.", 4, lambda r: STOCK_RU.get(r.get("in_stock"), "н/д")),
    ("Срок отгрузки", 12, lambda r: E(r.get("lead_time")) or "—"),
    ("Цена", 8, money),
    ("Объём", 5, lambda r: COVERS_RU.get(r.get("covers_qty"), "н/д")),
    ("Примечание", 16, lambda r: E(r.get("note"))),
]


def seller_cell(r: dict) -> str:
    s = E(r.get("seller")) or "—"
    kind = KIND_RU.get(r.get("kind"), "")
    extra = f'<br><span class="dim">{kind}</span>' if kind and kind != "н/д" else ""
    return s + extra


def table(rows: list[dict]) -> str:
    if not rows:
        return '<p class="dim">Строк нет.</p>'
    th = "".join(f'<th style="width:{w}%">{E(t)}</th>' for t, w, _ in COLS)
    trs = []
    for r in rows:
        tds = "".join(f"<td>{fn(r)}</td>" for _, _, fn in COLS)
        trs.append(f"<tr>{tds}</tr>")
    return (
        f'<table class="t"><thead><tr>{th}</tr></thead>'
        f'<tbody>{"".join(trs)}</tbody></table>'
    )


CSS = """
@page { size: A4 landscape; margin: 9mm 8mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 7.4pt; color: #111; margin: 0; }
h1 { font-size: 16pt; margin: 0 0 2mm; }
h2 { font-size: 11pt; margin: 0 0 2mm; border-bottom: 1.4pt solid #111; padding-bottom: 1mm; }
h3 { font-size: 9pt; margin: 3mm 0 1.5mm; }
p  { margin: 0 0 2mm; line-height: 1.35; }
.sec { page-break-before: always; }
.sec:first-child { page-break-before: auto; }
.lead { font-size: 8.4pt; }
.dim { color: #666; }
table.t { width: 100%; border-collapse: collapse; table-layout: fixed; margin-bottom: 3mm; }
.t thead { display: table-header-group; }
.t tr { page-break-inside: avoid; }
.t th { background: #111; color: #fff; text-align: left; padding: 1.2mm 1.4mm; font-size: 7pt; }
.t td { padding: 1.2mm 1.4mm; border-bottom: 0.3pt solid #ccc;
        word-wrap: break-word; overflow-wrap: anywhere; vertical-align: top; }
.t tbody tr:nth-child(even) td { background: #f5f5f5; }
.pn { font-family: "DejaVu Sans Mono", monospace; font-weight: bold; }
table.k { border-collapse: collapse; margin: 0 0 4mm; }
.k td { padding: 1.4mm 4mm 1.4mm 0; border-bottom: 0.3pt solid #ddd; vertical-align: top; }
.k td:first-child { font-weight: bold; white-space: nowrap; }
.big { font-size: 13pt; font-weight: bold; }
ol, ul { margin: 0 0 3mm; padding-left: 5mm; }
li { margin-bottom: 1.4mm; line-height: 1.35; }
.warn { border-left: 2pt solid #111; padding-left: 3mm; margin-bottom: 3mm; }
"""


def build_html(doc: dict) -> str:
    rows = doc["rows"]
    by = {}
    for r in rows:
        by.setdefault(r["verdict"], []).append(r)
    for v in by.values():
        v.sort(key=lambda r: -line_value(r))

    stock = by.get("in_stock", [])
    lead = by.get("available_lead", [])
    nostock = by.get("pn_found_no_stock", [])
    oem = by.get("oem_only", [])
    absent = by.get("pn_not_found", [])
    missing = by.get("not_checked", [])

    checked = len(rows) - len(missing)
    full = [r for r in stock if r.get("covers_qty") == "full"]
    stock_val = sum(line_value(r) for r in stock)
    lead_val = sum(line_value(r) for r in lead)

    def sec(title: str, rows_: list[dict], intro: str = "") -> str:
        return (
            f'<div class="sec"><h2>{E(title)} — {len(rows_)}</h2>'
            + (f'<p class="lead">{intro}</p>' if intro else "")
            + table(rows_)
            + "</div>"
        )

    kpi = f"""
<table class="k">
<tr><td>Проверено строк</td><td class="big">{checked}</td>
    <td class="dim">из {len(rows)} непроверенного остатка ТКП «Энергосети»</td></tr>
<tr><td>На складе у продавца</td><td class="big">{len(stock)}</td>
    <td class="dim">из них закрывают весь заявленный объём — {len(full)}</td></tr>
<tr><td>Под заказ со сроком</td><td class="big">{len(lead)}</td>
    <td class="dim">продавец назвал срок отгрузки</td></tr>
<tr><td>Артикул живой, склада нет</td><td class="big">{len(nostock)}</td>
    <td class="dim">цена и канал есть, остаток не подтверждён</td></tr>
<tr><td>Только OEM, канала нет</td><td class="big">{len(oem)}</td>
    <td class="dim">закрывается заводом или реверсом</td></tr>
<tr><td>Артикул не найден</td><td class="big">{len(absent)}</td>
    <td class="dim">нужен разбор номера или данные от заказчика</td></tr>
<tr><td>Закупка по складским строкам</td><td class="big">{stock_val:,.0f} USD</td>
    <td class="dim">по ценам карточек продавцов, на заявленные количества</td></tr>
<tr><td>Закупка по строкам под заказ</td><td class="big">{lead_val:,.0f} USD</td>
    <td class="dim">там же, где продавец назвал цену</td></tr>
</table>
""".replace(",", " ")

    todo = """
<h2>Что делать по результатам</h2>
<ol>
<li><b>Складские строки — в твёрдый оффер сегодня.</b> По каждой запросить у продавца:
    остаток числом на сегодня, срок отгрузки под наше количество, цену за весь объём,
    срок действия оффера, Инкотермс, условия возврата. Карточка сайта — это витрина,
    а не бронь: до письменного подтверждения остатка строка не считается отгружаемой.</li>
<li><b>«Условно на складе» приравнять к отсутствию.</b> Формулировка «ships next business
    day if in stock» без числа остатка — это условие, а не наличие. Такие строки идут
    в запрос вместе со строками «склада нет».</li>
<li><b>Количество решает больше, чем срок на карточке.</b> Строка в сотни штук складом
    мира не закрывается: срок считать от производственного цикла (35 рабочих дней у
    китайского изготовителя плюс логистика), а не от «отгрузка за 3 дня» у дистрибьютора.</li>
<li><b>Класс C — не искать, а подбирать.</b> Крепёж, о-кольца, шайбы, штуцеры под
    брендом OEM стоят кратно дороже того же метиза по стандарту. Там, где в примечании
    стоит подбор по размеру, нужно получить от заказчика типоразмер, класс прочности и
    материал — и закрывать позицию метизным поставщиком, а не каталогом OEM.</li>
<li><b>Найденные изготовители узлов — главный рычаг по цене.</b> Где в примечании назван
    реальный изготовитель под брендом OEM с кросс-номером, запрашивать его прямой канал:
    по нашей же практике разница 2–10 раз.</li>
<li><b>Ненайденные артикулы не гонять повторно поиском.</b> Это работа на расшифровку:
    номер-цепочка, кириллический суффикс учётной системы заказчика, снятая с производства
    позиция с заменой. Каждая расшифровка меняет вердикт, а не формулировку.</li>
<li><b>Запрашивать пакетами по бренду, а не сплошным файлом.</b> Сплошная рассылка на
    тысячу строк дала ноль ответов за тринадцать попыток; узкий запрос по знакомому
    бренду отвечается в двух третях случаев.</li>
</ol>
"""

    origin = f"""
<div class="warn">
<p><b>Откуда список.</b> Выгрузки предыдущей сессии (<code>ship/*.json</code>) не сохранились,
доступа к Битриксу в этой сессии нет. Список собран заново: лист «Энергосети» из
<code>gt/data/rfq_demand.json</code> — {len(rows)} артикулов, по которым у нас есть
ценовая вилка и по которым ранее не было проверки наличия
(<code>gt/data/rfq_prices.json</code>, раздел <code>checks</code>).</p>
<p><b>Что считать фактом.</b> Наличие, срок и цена взяты с карточек товара у продавцов.
Страница поисковой выдачи доказательством не считалась. Ни одна строка не является
котировкой: это витрины продавцов, а не ответы на наш запрос.</p>
</div>
"""

    parts = [
        '<div class="sec">',
        "<h1>Наличие у продавцов: непроверенный остаток ТКП «Энергосети»</h1>",
        f'<p class="lead dim">ЛУКОЙЛ · смарт-процесс СП-166 · обновлено {E(doc.get("updated"))}</p>',
        kpi,
        origin,
        todo,
        "</div>",
        sec("Отгружаемо со склада продавца", stock,
            "Приоритетный транш: остаток на витрине подтверждён, срок отгрузки назван. "
            "Проверять числом остатка в ответе на запрос."),
        sec("Под заказ, срок назван продавцом", lead,
            "Канал есть, склада нет. Для строк в сотни штук срок считать от производства."),
        sec("Артикул живой, склад не подтверждён", nostock,
            "Цена и продавец найдены, остаток не подтверждён или указан условно."),
        sec("Только OEM — канала нет", oem,
            "Закрывается заводом-изготовителем, реверсом или отказом от строки."),
        sec("Артикул не найден", absent,
            "Нужна расшифровка номера или уточнение от заказчика — поиском больше не берутся."),
    ]
    if missing:
        parts.append(sec("Строки без ответа проверки", missing,
                         "Агент не вернул строку — требуется повторный прогон."))

    return (
        "<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
        "<title>Наличие ТКП Энергосети</title>"
        f"<style>{CSS}</style></head><body>{''.join(parts)}</body></html>"
    )


def main() -> int:
    if not DATA.exists():
        print(f"нет {DATA}", file=sys.stderr)
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
         "--run-all-compositor-stages-before-draw", "--virtual-time-budget=60000",
         f"--print-to-pdf={PDF}", HTML.as_uri()],
        check=True, capture_output=True,
    )
    print(f"PDF:  {PDF.name} {PDF.stat().st_size:,} байт")
    return 0


if __name__ == "__main__":
    sys.exit(main())
