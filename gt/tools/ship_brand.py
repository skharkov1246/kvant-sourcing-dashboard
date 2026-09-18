#!/usr/bin/env python3
"""Выкладка по одному бренду заявки ЛУКОЙЛ: Siemens или Solar.

Заявка на 1642 позиции нечитаема целиком, а работают по ней по машинам: сначала
турбины SGT-400, потом Taurus. Этот сборщик режет выкладку по бренду и в каждой
половине честно разделяет то, что можно отгружать, от того, что ещё не опознано.

Отдельно считает «расшифровано, но не влито» — строки, по которым разбор уже дал
стандарт или изготовителя узла, но датасет ещё держит их как неопознанные.
"""
from __future__ import annotations

import html
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "gt/data/ship_lukoil.json"
DECODED = ROOT / "gt/data/ship_decoded.json"
OUT = ROOT / "gt/docs"
CHROME = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
]

BRANDS = {"Siemens": "СИМЕНС-SGT-400", "Solar": "СОЛАР-TAURUS"}
VERDICT_RU = {
    "in_stock": "на складе продавца",
    "available_lead": "под заказ, срок назван",
    "pn_found_no_stock": "артикул живой, склад не подтверждён",
    "oem_only": "только OEM, канала нет",
    "pn_not_found": "артикул не опознан",
    "not_checked": "не проверялось",
}
ORDER = ["in_stock", "available_lead", "pn_found_no_stock", "oem_only", "pn_not_found"]
GRADE_RU = {"твёрдый": "твёрдый", "частичный": "объём не закрыт",
            "условный": "условно «если есть»", "устаревший": "август", "нет": ""}


def E(x) -> str:
    return html.escape(str(x if x is not None else ""))


def ru(n) -> str:
    return f"{int(round(float(n or 0))):,}".replace(",", " ")


CSS = """
@page { size: A4 landscape; margin: 9mm 8mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 7.6pt; color: #111; margin: 0; }
h1 { font-size: 17pt; margin: 0 0 1mm; }
h2 { font-size: 11.5pt; margin: 0 0 2mm; border-bottom: 1.4pt solid #111; padding-bottom: 1mm; }
h3 { font-size: 9pt; margin: 4mm 0 1.5mm; }
p { margin: 0 0 2mm; line-height: 1.38; }
.sec { page-break-before: always; }
.sec:first-child { page-break-before: auto; }
.lead { font-size: 8.5pt; }
.dim { color: #666; }
table.t { width: 100%; border-collapse: collapse; table-layout: fixed; margin-bottom: 3mm; }
.t thead { display: table-header-group; }
.t tbody.p { page-break-inside: avoid; }
.t th { background: #111; color: #fff; text-align: left; padding: 1.2mm 1.4mm; font-size: 7pt; }
.t td { padding: 1.2mm 1.4mm; word-wrap: break-word; overflow-wrap: anywhere; vertical-align: top; }
.t tbody.p:nth-of-type(even) td { background: #f6f6f6; }
.t tr.n td { color: #444; font-size: 6.9pt; padding-top: 0.3mm; padding-bottom: 1.5mm; line-height: 1.3; }
.pn { font-family: "DejaVu Sans Mono", monospace; font-weight: bold; }
table.k { border-collapse: collapse; margin: 0 0 3mm; width: 100%; }
.k td { padding: 1.3mm 3mm 1.3mm 0; border-bottom: 0.3pt solid #ddd; vertical-align: top; }
.k td.l { font-weight: bold; }
.big { font-size: 12pt; font-weight: bold; }
ol, ul { margin: 0 0 3mm; padding-left: 5mm; }
li { margin-bottom: 1.4mm; line-height: 1.38; }
.warn { border-left: 2.4pt solid #111; padding-left: 3mm; margin-bottom: 3mm; }
"""


def unit_usd(r):
    u = r.get("unit_price_usd")
    return None if u in (None, "") else float(u)


def line_usd(r) -> float:
    """Стоимость строки. Только при подтверждённом объёме — правило выкладки."""
    if r.get("covers_qty") != "full":
        return 0.0
    u = unit_usd(r)
    return 0.0 if u is None else u * float(r.get("qty") or 0)


def addressee(r) -> str:
    out = []
    for sl in (r.get("sellers") or [])[:2]:
        bits = [f'<b>{E(sl["seller"])}</b>']
        if sl.get("country"):
            bits.append(E(sl["country"]))
        for em in (sl.get("emails") or [])[:1]:
            bits.append(f'<span class="pn">{E(em)}</span>')
        out.append(" · ".join(bits))
    if not out:
        cl = [E(sl["seller"]) for sl in (r.get("cluster_sellers") or [])[:2]]
        if cl:
            return ('<span class="dim">по классу товара: </span>' + " ⁄ ".join(cl))
        return '<span class="dim">адресата нет</span>'
    return " ⁄ ".join(out)


COLS = [
    ("Артикул", 11, lambda r: f'<span class="pn">{E(r["pn"])}</span>'),
    ("Наименование", 22, lambda r: E((r.get("name") or "")[:150])),
    ("Кол-во", 5, lambda r: f'{ru(r.get("qty"))} {E(r.get("unit") or "шт")}'),
    ("Наличие", 7, lambda r: E(GRADE_RU.get(r.get("stock_grade", ""), ""))),
    ("Остаток", 8, lambda r: E(str(r.get("stock_qty") or ""))),
    ("Срок", 10, lambda r: E(r.get("lead_time") or "")),
    ("Цена, USD/шт", 6, lambda r: (f'{unit_usd(r):,.2f}'.replace(",", " ")
                                   if unit_usd(r) is not None else "")),
    ("Наша вилка", 6, lambda r: (f'{ru(r.get("usd_lo"))}–{ru(r.get("usd_hi"))}'
                                 if r.get("usd_lo") else "")),
    ("Кому писать", 25, lambda r: addressee(r)),
]


def table(rows: list) -> str:
    if not rows:
        return '<p class="dim">Строк нет.</p>'
    th = "".join(f'<th style="width:{w}%">{E(t)}</th>' for t, w, _ in COLS)
    bodies = []
    for r in rows:
        tds = "".join(f"<td>{fn(r)}</td>" for _, _, fn in COLS)
        note = (r.get("note") or "").strip()
        extra = ""
        if note:
            extra = (f'<tr class="n"><td colspan="{len(COLS)}">{E(note[:420])}</td></tr>')
        bodies.append(f'<tbody class="p"><tr>{tds}</tr>{extra}</tbody>')
    return f'<table class="t"><thead><tr>{th}</tr></thead>{"".join(bodies)}</table>'


def build(brand: str, rows: list, decoded: dict) -> str:
    g = [r for r in rows if (r.get("man") or "") == brand]
    qty = sum(int(r.get("qty") or 0) for r in g)
    grade = Counter(r.get("stock_grade", "нет") for r in g)
    verd = defaultdict(list)
    for r in g:
        verd[r["verdict"]].append(r)
    firm_val = sum(line_usd(r) for r in g if r.get("stock_grade") == "твёрдый")
    noaddr = [r for r in g if not (r.get("sellers") or [])]
    dec = [r for r in g if r["pn"] in decoded]
    dec_qty = sum(int(r.get("qty") or 0) for r in dec)

    parts = [
        f"<h1>Заявка ЛУКОЙЛ: {E(brand)} — {len(g)} позиций, {ru(qty)} шт</h1>",
        '<p class="lead">Срез полной выкладки по одному бренду. Проверены все строки: '
        '«не проверялось» — ноль. Открытыми остались не непроверенные, а '
        '<b>неопознанные</b>: артикул проверен, канал под него не найден.</p>',
        f"""<table class="k"><tbody>
<tr><td class="l">Твёрдое наличие</td><td class="big">{grade["твёрдый"]}</td>
<td class="dim">продавец назвал остаток числом и подтвердил весь объём. Только эти
строки идут в план отгрузки</td></tr>
<tr><td class="l">Наличие есть, объём не закрыт</td><td class="big">{grade["частичный"]}</td>
<td class="dim">нужен добор у второго и третьего продавца</td></tr>
<tr><td class="l">Условное «отгрузим, если есть»</td><td class="big">{grade["условный"]}</td>
<td class="dim">формулировка витрины без числа остатка. Наличием не является</td></tr>
<tr><td class="l">Закупка по твёрдым строкам</td><td class="big">{ru(firm_val)} USD</td>
<td class="dim">только там, где подтверждён весь объём; цены приведены к доллару
по курсу из gt/data/fx_rates.json — это не курс сделки</td></tr>
<tr><td class="l">Артикул не опознан</td><td class="big">{len(verd["pn_not_found"])}</td>
<td class="dim">{ru(sum(int(r.get("qty") or 0) for r in verd["pn_not_found"]))} шт —
главный незакрытый объём</td></tr>
<tr><td class="l">Только OEM, канала нет</td><td class="big">{len(verd["oem_only"])}</td>
<td class="dim">{ru(sum(int(r.get("qty") or 0) for r in verd["oem_only"]))} шт;
покупается через фирменный канал по контракту</td></tr>
<tr><td class="l">Без адресата вообще</td><td class="big">{len(noaddr)}</td>
<td class="dim">{ru(sum(int(r.get("qty") or 0) for r in noaddr))} шт — писать некому,
нужен чертёж или шильдик</td></tr>
</tbody></table>""",
    ]

    if dec:
        parts.append(
            '<div class="warn"><h3>Расшифровано разбором, но в датасет ещё не влито — '
            f'{len(dec)} строк, {ru(dec_qty)} шт</h3>'
            '<p>Эти позиции лежат ниже как «не опознан», хотя разбор уже дал стандарт или '
            'изготовителя узла. Влитие уберёт их из незакрытого объёма.</p><ul>' +
            "".join(f'<li><span class="pn">{E(r["pn"])}</span> — {ru(r.get("qty"))} шт: '
                    f'{E(decoded[r["pn"]])}</li>'
                    for r in sorted(dec, key=lambda x: -(int(x.get("qty") or 0)))) +
            "</ul></div>")

    n = 1
    for v in ORDER:
        vr = verd.get(v) or []
        if not vr:
            continue
        vr.sort(key=lambda r: -(int(r.get("qty") or 0)))
        qv = sum(int(r.get("qty") or 0) for r in vr)
        head = (f'<h2>{n}. {E(VERDICT_RU[v])} — {len(vr)} позиций, {ru(qv)} шт</h2>'
                '<p class="lead">По убыванию количества.</p>')
        n += 1
        parts.append(f'<div class="sec">{head}{table(vr)}</div>')

    return ("<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
            f"<title>ЛУКОЙЛ — {E(brand)}</title>"
            f"<style>{CSS}</style></head><body>{''.join(parts)}</body></html>")


def main() -> int:
    if not DATA.exists():
        print(f"нет {DATA} — сначала gt/tools/ship_merge.py", file=sys.stderr)
        return 1
    doc = json.loads(DATA.read_text())
    rows = doc["rows"] if isinstance(doc, dict) else doc
    decoded = {}
    if DECODED.exists():
        decoded = {k: v["spec"] for k, v in json.loads(DECODED.read_text())["rows"].items()}

    exe = next((c for c in CHROME if Path(c).exists()), None)
    if not exe:
        print("Chromium не найден — PDF не собран", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    for brand, slug in BRANDS.items():
        h = OUT / f"ЛУКОЙЛ-{slug}.html"
        p = OUT / f"ЛУКОЙЛ-{slug}.pdf"
        h.write_text(build(brand, rows, decoded), encoding="utf-8")
        subprocess.run(
            [exe, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
             "--run-all-compositor-stages-before-draw", "--virtual-time-budget=120000",
             f"--print-to-pdf={p}", h.as_uri()],
            check=True, capture_output=True)
        g = [r for r in rows if (r.get("man") or "") == brand]
        print(f"{p.name}: {len(g)} позиций, {p.stat().st_size / 1e6:.1f} МБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
