#!/usr/bin/env python3
"""Разведка цен по заявке ЛУКОЙЛ: 857 позиций, где у нас есть оценка рынка.

ЧТО ЭТО НЕ ЕСТЬ. Это НЕ цены, выставленные заказчику. Владелец выставил твёрдые
цены и будет от них снижаться; вилки он не давал. Здесь лежит другое —
gt/data/rfq_prices.json, наша рыночная разведка от 08.08.2026, где в основаниях
дословно стоит «дистрибьюторы», «аналог», «экспертная вилка». Две границы у
записи означают разброс найденных предложений, а не наше предложение.

Путать эти вещи нельзя: на защите предъявляют выставленную цену, а разведка —
подготовительный материал под неё. Выставленные цены лежат в приложенных к
сделке файлах и тянутся отдельно, gt/tools/bitrix_tkp.py.

ЗАЧЕМ ТОГДА ДОКУМЕНТ. Он отвечает на один вопрос: насколько наша разведка
сходится с живым рынком. Это и есть проверка качества подготовки:

  1. Где разведка расходится с найденной ценой продавца и на сколько.
  2. Чем разведка обоснована — поле basis печатается дословно, категория
     уверенности A/B/C стоит рядом.
  3. По скольким строкам разведка вообще не проверена рынком.
"""
from __future__ import annotations

import html
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "gt/data/ship_lukoil.json"
PRICES = ROOT / "gt/data/rfq_prices.json"
FX = ROOT / "gt/data/fx_rates.json"
HTML_OUT = ROOT / "gt/docs/РАЗВЕДКА-ЦЕН-ЛУКОЙЛ.html"
PDF_OUT = ROOT / "gt/docs/РАЗВЕДКА-ЦЕН-ЛУКОЙЛ.pdf"
CHROME = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
]

CONF_RU = {"A": "A — цена с карточки изготовителя или авторизованного канала",
           "B": "B — цена с витрины дистрибьютора",
           "C": "C — оценка по аналогу или классу изделия"}
GRADE_RU = {"твёрдый": "твёрдый", "частичный": "объём не закрыт",
            "условный": "условно «если есть»", "устаревший": "август", "нет": "нет"}


def E(x) -> str:
    return html.escape(str(x if x is not None else ""))


def ru(n) -> str:
    return f"{int(round(float(n or 0))):,}".replace(",", " ")


def money(n) -> str:
    return f"{float(n or 0):,.2f}".replace(",", " ")


def unit(r):
    u = r.get("unit_price_usd")
    if u in (None, ""):
        return None
    u = float(u)
    return None if u <= 0 else u   # ноль — заглушка витрины, а не цена


def band_state(r) -> str:
    """Где цена продавца относительно нашей вилки. Пусто, если цены нет."""
    u = unit(r)
    if u is None or not r.get("usd_lo"):
        return ""
    lo, hi = float(r["usd_lo"]), float(r["usd_hi"])
    if u > hi:
        return "выше"
    if u < lo:
        return "ниже"
    return "в вилке"


def shortfall(r) -> float:
    """Недобор на объём, если цена давалась по верху вилки. Только для «выше»."""
    if band_state(r) != "выше":
        return 0.0
    return (unit(r) - float(r["usd_hi"])) * float(r.get("qty") or 0)


CSS = """
@page { size: A4 landscape; margin: 9mm 8mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 7.5pt; color: #111; margin: 0; }
h1 { font-size: 17pt; margin: 0 0 1.5mm; }
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
.t tr.b td { color: #333; font-size: 6.9pt; padding-top: 0.3mm; padding-bottom: 1.5mm; line-height: 1.3; }
.pn { font-family: "DejaVu Sans Mono", monospace; font-weight: bold; }
.up { font-weight: bold; }
table.k { border-collapse: collapse; margin: 0 0 3mm; width: 100%; }
.k td { padding: 1.3mm 3mm 1.3mm 0; border-bottom: 0.3pt solid #ddd; vertical-align: top; }
.k td.l { font-weight: bold; }
.big { font-size: 12pt; font-weight: bold; }
ol, ul { margin: 0 0 3mm; padding-left: 5mm; }
li { margin-bottom: 1.4mm; line-height: 1.38; }
.warn { border-left: 2.4pt solid #111; padding-left: 3mm; margin-bottom: 3mm; }
"""

COLS = [
    ("Артикул", 9, lambda r: f'<span class="pn">{E(r["pn"])}</span>'),
    ("Наименование", 18, lambda r: E((r.get("name") or "")[:130])),
    ("Кол-во", 4, lambda r: f'{ru(r.get("qty"))}'),
    ("Разведка, USD/шт", 7,
     lambda r: f'{ru(r.get("usd_lo"))} – {ru(r.get("usd_hi"))}'),
    ("Увер.", 3, lambda r: E(r.get("conf") or "")),
    ("Цена продавца, USD/шт", 7,
     lambda r: (f'<span class="up">{money(unit(r))}</span>' if band_state(r) == "выше"
                else (money(unit(r)) if unit(r) is not None else ""))),
    ("Рынок к разведке", 6, lambda r: E(band_state(r))),
    ("Ошибка разведки, USD", 6,
     lambda r: (f'<span class="up">{ru(shortfall(r))}</span>' if shortfall(r) else "")),
    ("Наличие", 5, lambda r: E(GRADE_RU.get(r.get("stock_grade", "нет"), ""))),
    ("Остаток", 6, lambda r: E((r.get("stock_qty") or "")[:60])),
    ("Продавец", 13, lambda r: E(((r.get("sellers") or [{}])[0].get("seller") or "")[:60])),
]


def table(rows: list, basis: dict) -> str:
    if not rows:
        return '<p class="dim">Строк нет.</p>'
    th = "".join(f'<th style="width:{w}%">{E(t)}</th>' for t, w, _ in COLS)
    bodies = []
    for r in rows:
        tds = "".join(f"<td>{fn(r)}</td>" for _, _, fn in COLS)
        b = (basis.get(r["pn"]) or "").strip()
        extra = ""
        if b:
            extra = (f'<tr class="b"><td colspan="{len(COLS)}">'
                     f'<b>Основание оценки:</b> {E(b[:400])}</td></tr>')
        bodies.append(f'<tbody class="p"><tr>{tds}</tr>{extra}</tbody>')
    return f'<table class="t"><thead><tr>{th}</tr></thead>{"".join(bodies)}</table>'


def build(rows: list, basis: dict, fx_day: str) -> str:
    g = [r for r in rows if r.get("usd_lo") is not None]
    qty = sum(int(r.get("qty") or 0) for r in g)
    lo_tot = sum(float(r["usd_lo"]) * int(r.get("qty") or 0) for r in g)
    hi_tot = sum(float(r["usd_hi"]) * int(r.get("qty") or 0) for r in g)
    conf = Counter(r.get("conf") or "—" for r in g)
    grade = Counter(r.get("stock_grade", "нет") for r in g)

    withp = [r for r in g if unit(r) is not None]
    st = Counter(band_state(r) for r in withp)
    over = sorted((r for r in g if band_state(r) == "выше"),
                  key=lambda r: -shortfall(r))
    under = sorted((r for r in g if band_state(r) == "ниже"),
                   key=lambda r: -(int(r.get("qty") or 0)))
    inband = [r for r in g if band_state(r) == "в вилке"]
    nocheck = [r for r in g if unit(r) is None]
    sf = sum(shortfall(r) for r in over)

    all_firm = [r for r in rows if r.get("stock_grade") == "твёрдый"]
    firm_priced = [r for r in all_firm if r.get("usd_lo") is not None]

    parts = [
        f"<h1>Разведка цен по заявке ЛУКОЙЛ: {len(g)} позиций с нашей оценкой рынка</h1>",
        '<p class="lead"><b>Это не цены, выставленные заказчику.</b> Выставленные цены '
        'твёрдые и лежат в приложенных к сделке файлах. Здесь — наша рыночная разведка '
        'от 08.08.2026: две границы у записи означают разброс найденных предложений, а не '
        'наше предложение. Документ отвечает на один вопрос: сходится ли разведка с живым '
        'рынком, то есть годна ли она как основание. Полная заявка — 1 642 позиции.</p>',
        f"""<table class="k"><tbody>
<tr><td class="l">Позиций с разведкой</td><td class="big">{len(g)}</td>
<td class="dim">{ru(qty)} шт. Уверенность: A — {conf["A"]}, B — {conf["B"]}, C — {conf["C"]}</td></tr>
<tr><td class="l">Оценка закупки по низу разведки</td><td class="big">{ru(lo_tot)} USD</td>
<td class="dim">сумма нижних границ на заявленные количества</td></tr>
<tr><td class="l">Оценка закупки по верху разведки</td><td class="big">{ru(hi_tot)} USD</td>
<td class="dim">разброс между низом и верхом — {hi_tot / lo_tot:.1f} раза. Разведка
такой ширины годится для ориентира, но обоснованием цены быть не может</td></tr>
<tr><td class="l">Цена продавца проверена</td><td class="big">{len(withp)}</td>
<td class="dim">из {len(g)} строк. По остальным {len(nocheck)} оценка рынком
не подтверждена ничем</td></tr>
<tr><td class="l">Рынок ВЫШЕ верха разведки</td><td class="big">{st["выше"]}</td>
<td class="dim">разведка занижена. Если бы цена строилась по её верху, закупка вышла бы
дороже расчёта на {ru(sf)} USD — это мера ошибки разведки, а не недобор по сделке</td></tr>
<tr><td class="l">Рынок ниже низа разведки</td><td class="big">{st["ниже"]}</td>
<td class="dim">разведка завышена: рынок оказался дешевле оценки</td></tr>
<tr><td class="l">Рынок попал в разведку</td><td class="big">{st["в вилке"]}</td>
<td class="dim">только по этим строкам оценка подтверждена рынком</td></tr>
<tr><td class="l">Твёрдое наличие среди них</td><td class="big">{grade["твёрдый"]}</td>
<td class="dim">частичное — {grade["частичный"]}, условное — {grade["условный"]},
без наличия — {grade["нет"]}</td></tr>
</tbody></table>""",
        '<div class="warn"><h3>Расхождение, которое надо знать до защиты</h3>'
        f'<p>Разведка сделана по <b>{len(g)}</b> позициям. Твёрдое наличие подтверждено по '
        f'<b>{len(all_firm)}</b> позициям всей заявки. Пересекаются они только в '
        f'<b>{len(firm_priced)}</b>. То есть оценка и подтверждённый склад стоят на разных '
        f'строках: {len(all_firm) - len(firm_priced)} твёрдых строк идут без оценки, '
        f'а {grade["нет"]} оценённых строк не имеют подтверждённого наличия вовсе.</p>'
        '<p>Практический вывод: закрытой считается позиция, попавшая в пересечение, '
        'а не любая из двух цифр по отдельности.</p></div>',
        "<h2>Как читать документ</h2><ol>"
        "<li><b>Уверенность A/B/C</b> — чем обоснована наша цена. C значит оценка по "
        "аналогу: такую цифру защищать нечем, кроме класса изделия.</li>"
        "<li><b>«Основание нашей цены»</b> под каждой строкой — дословно то, что было "
        "записано при оценке. Именно это придётся предъявлять.</li>"
        f"<li><b>Цены приведены к доллару</b> по курсу на {E(fx_day)} из "
        "<span class=\"pn\">gt/data/fx_rates.json</span>. Это справочный курс, не курс "
        "сделки — рядом с любой суммой оговорка обязательна.</li>"
        "<li><b>Ошибка разведки на объём</b> считается только там, где цена продавца выше "
        "верха оценки: разница, умноженная на количество. Это мера промаха оценки, "
        "а не потеря по сделке — выставленная цена сюда не входит.</li>"
        "<li><b>Чего здесь нет:</b> цен, выставленных заказчику. Они тянутся из "
        "приложенных к сделке файлов отдельным прогоном "
        "(<span class=\"pn\">gt/tools/bitrix_tkp.py</span>) и лягут рядом с этими "
        "столбцами, когда выгрузка пройдёт.</li></ol>",
    ]

    sections = [
        ("Рынок ВЫШЕ верха разведки — оценка занижена", over,
         "Здесь разведка недооценила закупку. Сортировка по размеру ошибки на объём, "
         "а не по кратности: строка с малой кратностью и большим количеством опаснее."),
        ("Рынок попал в разведку — оценка подтверждена", inband,
         "Единственный раздел, где оценка подтверждена живой ценой."),
        ("Рынок ниже низа разведки — оценка завышена", under,
         "Здесь рынок дешевле оценки. Но проверьте цену "
         "продавца глазами: на брокерских витринах ноль и цена в один доллар — "
         "заглушки, а не цена."),
        ("Разведка рынком не проверена", nocheck,
         "Цену продавца по этим строкам найти не удалось. Оценка держится только "
         "на основании, записанном при разведке."),
    ]
    n = 1
    for title, rws, lead in sections:
        if not rws:
            continue
        q = sum(int(r.get("qty") or 0) for r in rws)
        parts.append(
            f'<div class="sec"><h2>{n}. {E(title)} — {len(rws)} строк, {ru(q)} шт</h2>'
            f'<p class="lead">{E(lead)}</p>' + table(rws, basis) + "</div>")
        n += 1

    return ("<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
            "<title>Разведка цен ЛУКОЙЛ</title>"
            f"<style>{CSS}</style></head><body>{''.join(parts)}</body></html>")


def main() -> int:
    if not DATA.exists():
        print(f"нет {DATA} — сначала gt/tools/ship_merge.py", file=sys.stderr)
        return 1
    doc = json.loads(DATA.read_text())
    rows = doc["rows"] if isinstance(doc, dict) else doc
    basis = {p["pn"]: p.get("basis", "") for p in json.loads(PRICES.read_text())["prices"]}
    fx_day = ""
    if FX.exists():
        fx_day = json.loads(FX.read_text()).get("fetched", "")[5:16]

    HTML_OUT.write_text(build(rows, basis, fx_day), encoding="utf-8")
    exe = next((c for c in CHROME if Path(c).exists()), None)
    if not exe:
        print("Chromium не найден — PDF не собран", file=sys.stderr)
        return 1
    subprocess.run(
        [exe, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
         "--run-all-compositor-stages-before-draw", "--virtual-time-budget=120000",
         f"--print-to-pdf={PDF_OUT}", HTML_OUT.as_uri()],
        check=True, capture_output=True)

    g = [r for r in rows if r.get("usd_lo") is not None]
    over = [r for r in g if band_state(r) == "выше"]
    print(f"позиций с разведкой: {len(g)}")
    print(f"разведка занижена: {len(over)} строк, ошибка на объём "
          f"{sum(shortfall(r) for r in over):,.0f} USD".replace(",", " "))
    print(f"{PDF_OUT.name}: {PDF_OUT.stat().st_size / 1e6:.1f} МБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
