#!/usr/bin/env python3
"""Сводит ТВЁРДЫЕ цены владельца из вложений сделки с проверкой рынка по заявке.

Зачем. На защите предъявляют выставленную цену, а не разведку. Вопрос, на который
нужен ответ по каждой строке: есть ли запас на снижение и сколько его. Запас —
это разница между выставленной ценой и найденной ценой закупки; там, где рынок
дороже выставленного, запаса нет и строка убыточна.

Вход:
  gt/data/ship_lukoil.json  заявка с проверкой наличия и ценами продавцов
  --tkp <путь>              выгрузка из вложений (gt/tools/bitrix_tkp.py, полная)
  gt/data/fx_rates.json     курсы, чтобы не складывать кроны с рублями

ВЫХОД НЕ КОММИТИТСЯ. И выгрузка, и этот документ содержат цены, выставленные
заказчику, — коммерческие данные (правило 5 CLAUDE.md, SECURITY.md). Репозиторий
публичный, поэтому по умолчанию пишем в каталог вне репозитория, а сам инструмент
проверяет, что путь вывода не внутри дерева git.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "gt/data/ship_lukoil.json"
FX = ROOT / "gt/data/fx_rates.json"
CHROME = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
]
CUR_ALIAS = {"руб": "RUB", "RUR": "RUB", "долл": "USD", "$": "USD",
             "евро": "EUR", "€": "EUR", "₽": "RUB"}


def E(x) -> str:
    return html.escape(str(x if x is not None else ""))


def ru(n) -> str:
    return f"{int(round(float(n or 0))):,}".replace(",", " ")


def money(n) -> str:
    return f"{float(n or 0):,.2f}".replace(",", " ")


def norm_key(pn: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(pn).upper())


def load_rates() -> dict:
    if not FX.exists():
        return {"USD": 1.0}
    return json.loads(FX.read_text()).get("rates", {"USD": 1.0})


def to_usd(price, cur: str, rates: dict):
    if price in (None, ""):
        return None
    c = CUR_ALIAS.get(str(cur or "").strip(), str(cur or "USD").strip().upper()) or "USD"
    r = rates.get(c)
    if not r:
        return None
    try:
        return float(price) / r
    except (TypeError, ValueError):
        return None


def our_prices(tkp_path: Path, rates: dict) -> dict:
    """Артикул → лучшая (наименьшая) выставленная цена в долларах.

    Наименьшая, а не первая: один и тот же артикул попадается в нескольких
    файлах — в исходном ТКП и в правках. Осторожная оценка запаса берёт
    меньшую выставленную цену, иначе запас окажется нарисованным.
    """
    doc = json.loads(tkp_path.read_text())
    out: dict[str, dict] = {}
    for f in doc.get("files", []):
        for p in f.get("prices", []):
            usd = to_usd(p.get("price"), p.get("currency"), rates)
            if usd is None:
                continue
            k = norm_key(p["pn"])
            prev = out.get(k)
            if prev is None or usd < prev["usd"]:
                out[k] = {"usd": usd, "raw_price": p["price"],
                          "currency": p.get("currency") or "USD",
                          "file": f.get("file_name", ""), "origin": f.get("origin", ""),
                          "sheet": p.get("sheet", ""), "row": p.get("row"),
                          "rule": p.get("class_rule", ""), "line": p.get("raw", "")}
    return out


CSS = """
@page { size: A4 landscape; margin: 9mm 8mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 7.5pt; color: #111; margin: 0; }
h1 { font-size: 17pt; margin: 0 0 1.5mm; }
h2 { font-size: 11.5pt; margin: 0 0 2mm; border-bottom: 1.4pt solid #111; padding-bottom: 1mm; }
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
.bad { font-weight: bold; }
table.k { border-collapse: collapse; margin: 0 0 3mm; width: 100%; }
.k td { padding: 1.3mm 3mm 1.3mm 0; border-bottom: 0.3pt solid #ddd; vertical-align: top; }
.k td.l { font-weight: bold; }
.big { font-size: 12pt; font-weight: bold; }
ol { margin: 0 0 3mm; padding-left: 5mm; }
li { margin-bottom: 1.4mm; line-height: 1.38; }
.warn { border-left: 2.4pt solid #111; padding-left: 3mm; margin-bottom: 3mm; }
"""


def build(rows: list, ours: dict, fx_day: str) -> str:
    rates = load_rates()
    joined = []
    for r in rows:
        o = ours.get(norm_key(r["pn"]))
        if not o:
            continue
        mk = r.get("unit_price_usd")
        mk = float(mk) if mk not in (None, "") else None
        if mk is not None and mk <= 0:
            mk = None          # ноль — заглушка витрины, запас по нему не считается
        qty = int(r.get("qty") or 0)
        room = (o["usd"] - mk) if mk is not None else None
        joined.append({"r": r, "o": o, "mk": mk, "qty": qty, "room": room,
                       "room_total": (room * qty) if room is not None else None})

    checked = [j for j in joined if j["mk"] is not None]
    under = sorted((j for j in checked if j["room"] < 0), key=lambda j: j["room_total"])
    ok = sorted((j for j in checked if j["room"] >= 0), key=lambda j: -j["room_total"])
    nomk = [j for j in joined if j["mk"] is None]
    room_sum = sum(j["room_total"] for j in ok)
    loss_sum = sum(-j["room_total"] for j in under)
    firm = Counter(j["r"].get("stock_grade", "нет") for j in joined)

    parts = [
        f"<h1>Выставленные цены против рынка: {len(joined)} позиций заявки ЛУКОЙЛ</h1>",
        '<p class="lead">Слева — цена, выставленная заказчику, снятая из приложенного к '
        'сделке файла с указанием файла, листа и строки. Справа — цена закупки, найденная '
        'проверкой у продавцов. Разница и есть запас на снижение. Где рынок дороже '
        'выставленного, запаса нет: строка убыточна при текущей цене.</p>',
        f"""<table class="k"><tbody>
<tr><td class="l">Сошлось с заявкой</td><td class="big">{len(joined)}</td>
<td class="dim">позиций, где есть и выставленная цена, и строка заявки</td></tr>
<tr><td class="l">Рынок проверен</td><td class="big">{len(checked)}</td>
<td class="dim">по остальным {len(nomk)} цену закупки найти не удалось — запас неизвестен</td></tr>
<tr><td class="l">Запас на снижение</td><td class="big">{ru(room_sum)} USD</td>
<td class="dim">сумма по {len(ok)} строкам, где выставлено дороже найденной закупки.
Это предел торга, а не прибыль: расходы, логистика и пошлины сюда не входят</td></tr>
<tr><td class="l">Строк ниже рынка</td><td class="big bad">{len(under)}</td>
<td class="dim">закупка дороже выставленного на {ru(loss_sum)} USD. Снижать по ним
нельзя — надо либо искать канал дешевле, либо пересматривать цену</td></tr>
<tr><td class="l">Твёрдое наличие среди них</td><td class="big">{firm["твёрдый"]}</td>
<td class="dim">частичное {firm["частичный"]}, условное {firm["условный"]},
без наличия {firm["нет"]}</td></tr>
</tbody></table>""",
        '<div class="warn"><h2>Как это читать на защите</h2><ol>'
        '<li><b>Запас считается к найденной цене продавца</b>, а не к нашей разведке. '
        'Разведка — подготовительный материал, её в этом документе нет.</li>'
        f'<li><b>Цены приведены к доллару</b> по справочному курсу на {E(fx_day)}. '
        'Это не курс сделки: банк даст свой, и к оплате он сдвинется.</li>'
        '<li><b>Происхождение каждой выставленной цены указано</b>: файл, лист, номер '
        'строки и правило, которым значение извлечено. Если цифра вызовет вопрос, её '
        'видно откуда проверить.</li>'
        '<li><b>Запас — не прибыль.</b> Из него ещё уйдут доставка, оформление, '
        'пошлины и риск курса.</li></ol></div>',
    ]

    cols = [
        ("Артикул", 9, lambda j: f'<span class="pn">{E(j["r"]["pn"])}</span>'),
        ("Наименование", 17, lambda j: E((j["r"].get("name") or "")[:120])),
        ("Кол-во", 4, lambda j: ru(j["qty"])),
        ("Выставлено, USD/шт", 7, lambda j: money(j["o"]["usd"])),
        ("В валюте ТКП", 7, lambda j: f'{money(j["o"]["raw_price"])} {E(j["o"]["currency"])}'),
        ("Закупка найдена, USD/шт", 7, lambda j: money(j["mk"]) if j["mk"] is not None else ""),
        ("Запас, USD/шт", 6, lambda j: ("" if j["room"] is None else
                                        (f'<span class="bad">{money(j["room"])}</span>'
                                         if j["room"] < 0 else money(j["room"])))),
        ("Запас на объём, USD", 7, lambda j: ("" if j["room_total"] is None else
                                              (f'<span class="bad">{ru(j["room_total"])}</span>'
                                               if j["room_total"] < 0 else ru(j["room_total"])))),
        ("Наличие", 5, lambda j: E(j["r"].get("stock_grade", ""))),
        ("Продавец", 14, lambda j: E(((j["r"].get("sellers") or [{}])[0].get("seller") or "")[:60])),
    ]

    def table(js: list) -> str:
        if not js:
            return '<p class="dim">Строк нет.</p>'
        th = "".join(f'<th style="width:{w}%">{E(t)}</th>' for t, w, _ in cols)
        bodies = []
        for j in js:
            tds = "".join(f"<td>{fn(j)}</td>" for _, _, fn in cols)
            o = j["o"]
            src = (f'<b>Откуда выставленная цена:</b> {E(o["origin"])}, файл '
                   f'«{E(o["file"])}», лист {E(o["sheet"])}, строка {E(o["row"])}; '
                   f'{E(o["rule"])}')
            bodies.append(f'<tbody class="p"><tr>{tds}</tr>'
                          f'<tr class="b"><td colspan="{len(cols)}">{src}</td></tr></tbody>')
        return f'<table class="t"><thead><tr>{th}</tr></thead>{"".join(bodies)}</table>'

    for n, (title, js, lead) in enumerate([
        ("Закупка ДОРОЖЕ выставленного — запаса нет", under,
         "Самое опасное на защите. Сортировка по размеру убытка на объём."),
        ("Запас на снижение есть", ok,
         "Сортировка по размеру запаса на объём: здесь есть чем торговаться."),
        ("Цена закупки не найдена — запас неизвестен", nomk,
         "Выставленная цена есть, подтверждения закупочной нет."),
    ], 1):
        if not js:
            continue
        parts.append(f'<div class="sec"><h2>{n}. {E(title)} — {len(js)} строк</h2>'
                     f'<p class="lead">{E(lead)}</p>' + table(js) + "</div>")

    return ("<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
            "<title>Выставленные цены против рынка</title>"
            f"<style>{CSS}</style></head><body>{''.join(parts)}</body></html>")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tkp", required=True, help="полная выгрузка bitrix_tkp.py")
    ap.add_argument("--out", required=True, help="куда положить PDF (ВНЕ репозитория)")
    a = ap.parse_args()

    out = Path(a.out).resolve()
    # цены заказчику в публичный репозиторий не попадают — проверяем, а не надеемся
    try:
        out.relative_to(ROOT)
    except ValueError:
        pass
    else:
        print(f"ОТКАЗ: {out} внутри репозитория, а документ содержит выставленные цены.\n"
              "Укажи путь вне дерева git.", file=sys.stderr)
        return 2

    tkp = Path(a.tkp)
    if not tkp.exists():
        print(f"нет {tkp}", file=sys.stderr)
        return 1
    rows = json.loads(DATA.read_text())["rows"]
    rates = load_rates()
    ours = our_prices(tkp, rates)
    fx_day = json.loads(FX.read_text()).get("fetched", "")[5:16] if FX.exists() else ""
    print(f"артикулов с выставленной ценой: {len(ours)}")

    html_path = out.with_suffix(".html")
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(build(rows, ours, fx_day), encoding="utf-8")
    exe = next((c for c in CHROME if Path(c).exists()), None)
    if not exe:
        print("Chromium не найден — PDF не собран", file=sys.stderr)
        return 1
    subprocess.run(
        [exe, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
         "--run-all-compositor-stages-before-draw", "--virtual-time-budget=120000",
         f"--print-to-pdf={out}", html_path.as_uri()],
        check=True, capture_output=True)
    matched = sum(1 for r in rows if norm_key(r["pn"]) in ours)
    print(f"сошлось со заявкой: {matched} позиций")
    print(f"{out} — {out.stat().st_size / 1e6:.1f} МБ (вне репозитория, не коммитится)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
