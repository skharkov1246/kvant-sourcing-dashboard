#!/usr/bin/env python3
"""Бюджет заказчика против нашей закупки — построчно.

ЗАЧЕМ. До 21.09.2026 у нас была только одна сторона сделки: чего стоит купить.
Вторая сторона — сколько заказчик готов заплатить — лежала в его же файле
потребности («Цена без НДС с транспортными расходами, руб» и «СУММА, руб») и ни
в один наш счёт не входила. Без неё нельзя ответить на вопрос, который решает
судьбу строки: сходится ли она вообще. Первый прогон по файлу от 26.08:
1 479 строк с ценой, 7 678 662 098 руб = 91 027 865 USD, и все 1 479 сошлись с
нашей сводкой заявки по каталожному номеру — то есть соединять есть что.

ЧТО СЧИТАЕТСЯ И ЧЕГО НЕ СЧИТАЕТСЯ. Считается: бюджет строки, наша оценка
закупки (вилка) или найденная цена, и разница. НЕ считается маржа там, где
наша сторона — экспертная вилка: вилка это мнение, и выдавать разницу с мнением
за прибыль нельзя. Сумма закупки по найденной цене — верхняя граница при
допущении, что весь объём купится по ней; у строк, где продавец не подтвердил
количество (covers_qty не full), это допущение не проверено, и в документе
такая строка помечена.

ГДЕ ЧТО ЛЕЖИТ. Файл бюджета — снаружи репозитория: это коммерческий документ
заказчика. Построчный документ тоже пишется наружу, инструмент отказывается
писать его внутрь дерева git. В репозиторий идут только агрегаты (счётчики), их
читает лист решений gt/tools/ship_decisions.py.

    python gt/tools/budget_join.py --budget /root/.../потребность.xlsx \\
        --pdf /tmp/pdf/БЮДЖЕТ-ПРОТИВ-ЗАКУПКИ.pdf --counters gt/data/budget_join_stats.json
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pnkey import key  # noqa: E402
from verdicts import vkey  # noqa: E402

ASK = ROOT / "gt/data/ship_lukoil.json"
RV = ROOT / "gt/data/ship_reverify.json"
FX = ROOT / "gt/data/fx_rates.json"
CHROME = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/opt/pw-browsers/chromium/chrome-linux/chrome",
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
]

CSS = """
@page { size: A4 landscape; margin: 9mm 8mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 8pt; color: #111; margin: 0; }
h1 { font-size: 15pt; margin: 0 0 1mm; }
h2 { font-size: 11pt; margin: 5mm 0 2mm; border-bottom: 1.3pt solid #111;
     padding-bottom: 0.8mm; page-break-after: avoid; }
p { margin: 0 0 2mm; line-height: 1.45; }
.dim { color: #666; }
table { width: 100%; border-collapse: collapse; margin-bottom: 3mm; table-layout: fixed; }
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th { background: #111; color: #fff; text-align: left; padding: 1.2mm 1.5mm; font-size: 7.2pt; }
td { padding: 1.2mm 1.5mm; vertical-align: top; word-wrap: break-word;
     overflow-wrap: anywhere; border-bottom: 0.3pt solid #ddd; font-size: 7.2pt; }
td.n, th.n { text-align: right; white-space: nowrap; }
.stop { background: #fbeeee; }
.hold { background: #fdf6e3; }
.take { background: #eef7ee; }
.box { border: 0.8pt solid #111; padding: 2.5mm; margin-bottom: 3mm; page-break-inside: avoid; }
ol, ul { margin: 0 0 2mm; padding-left: 5mm; }
li { margin-bottom: 1.4mm; line-height: 1.45; }
"""

# Заголовки ищем по смыслу, а не по номеру столбца: файл заказчика приходит
# в новой редакции каждый раз, и столбцы в нём переезжают.
H_NUM = re.compile(r"№\s*п/п", re.I)
H_PN = re.compile(r"каталожн", re.I)
H_QTY = re.compile(r"количеств", re.I)
H_PRICE = re.compile(r"цена", re.I)
H_SUM = re.compile(r"сумма", re.I)
H_NAME = re.compile(r"наименование\s+ТРУ", re.I)
H_MAN = re.compile(r"производител", re.I)


def E(x) -> str:
    return html.escape(str(x if x is not None else ""))


def ru(n) -> str:
    return f"{int(round(float(n or 0))):,}".replace(",", " ")


def money(n) -> str:
    return f"{float(n or 0):,.2f}".replace(",", " ").replace(".", ",")


def fx_table() -> dict[str, float]:
    """Курсы «единиц валюты за один USD». USD добавляем сами: в файле его нет."""
    d = json.loads(FX.read_text(encoding="utf-8"))
    src = d.get("rates") if isinstance(d.get("rates"), dict) else d
    out = {"USD": 1.0}
    for k, v in src.items():
        if isinstance(v, (int, float)) and v:
            out[str(k).upper()] = float(v)
    out.setdefault("RUR", out.get("RUB", 0) or 1.0)
    return out


def to_usd(price, cur, fx: dict[str, float]):
    """Цена в USD. Нет курса — возвращаем None, а не «примерно»: это разные вещи."""
    if not isinstance(price, (int, float)) or not price:
        return None
    r = fx.get(str(cur or "USD").strip().upper())
    return float(price) / r if r else None


def found_price_usd(ask_row: dict, rv_row: dict, fx: dict[str, float]):
    """НАЙДЕННАЯ цена строки в USD, из двух источников.

    Перепроверка идёт первой: её цена перечитана на странице продавца сейчас.
    Второй источник — поле price самой сводки заявки: цена, прочитанная у
    продавца при первом обходе, со своей валютой. Замер 21.09.2026 показал,
    чего стоило её игнорировать: 101 строка числилась в разряде «у нас нет
    ничего», хотя цена по ней записана и продавец назван, а у 63 из них
    покрытие количества стоит full. Курсы всех встретившихся валют (GBP, KRW,
    EUR, MXN, RUB, CZK) в gt/data/fx_rates.json есть; если курса нет, строка
    не получает цену вовсе — «примерно» здесь хуже, чем ничего.
    """
    lo = rv_row.get("price_low")
    if isinstance(lo, (int, float)) and lo:
        return float(lo), "перепроверка"
    u = to_usd(ask_row.get("price"), ask_row.get("currency"), fx)
    if u is not None:
        return u, "обход заявки"
    return None, ""


def rub_per_usd() -> float:
    d = json.loads(FX.read_text(encoding="utf-8"))
    src = d.get("rates") if isinstance(d.get("rates"), dict) else d
    for k, v in src.items():
        if isinstance(v, (int, float)) and str(k).upper() in ("RUB", "RUR"):
            return float(v)
    raise SystemExit("в gt/data/fx_rates.json нет курса рубля")


def read_budget(path: Path) -> tuple[list[dict], dict]:
    """Строки файла заказчика. Столбцы ищутся по заголовкам, шапка — по ним же."""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out, meta = [], {}
    for ws in wb.worksheets:
        col: dict[str, int] = {}
        head_row = 0
        for i, row in enumerate(ws.iter_rows(min_row=1, max_row=30, values_only=True), 1):
            cells = [("" if c is None else str(c)) for c in row]
            if not any(H_NUM.search(c) for c in cells):
                continue
            for j, c in enumerate(cells):
                for name, rx in (("num", H_NUM), ("pn", H_PN), ("qty", H_QTY),
                                 ("price", H_PRICE), ("sum", H_SUM), ("name", H_NAME),
                                 ("man", H_MAN)):
                    if rx.search(c) and name not in col:
                        col[name] = j
            head_row = i
            break
        if not head_row or not {"num", "pn", "qty", "price"} <= set(col):
            continue
        meta.setdefault("sheets", []).append({"sheet": ws.title, "header_row": head_row,
                                              "columns": dict(col)})
        for row in ws.iter_rows(min_row=head_row + 1, values_only=True):
            def g(name):
                j = col.get(name)
                return row[j] if j is not None and j < len(row) else None
            if g("num") in (None, "") or g("pn") in (None, ""):
                continue
            try:
                qty = float(g("qty"))
                price = float(g("price"))
            except (TypeError, ValueError):
                continue
            try:
                total = float(g("sum"))
            except (TypeError, ValueError):
                total = price * qty
            out.append({
                "sheet": ws.title,
                "num": str(g("num")).strip().split(".")[0],
                "pn": str(g("pn")).strip(),
                "name": str(g("name") or "").strip(),
                "man": str(g("man") or "").strip(),
                "qty": qty, "rub": price, "sum_rub": total,
                "k": key(g("pn")),
            })
    return out, meta


def join(rows: list[dict], rub: float) -> list[dict]:
    fx = fx_table()
    ask, rv = {}, {}
    for r in json.loads(ASK.read_text(encoding="utf-8"))["rows"]:
        ask.setdefault(key(r.get("pn")), r)
    for r in json.loads(RV.read_text(encoding="utf-8"))["rows"]:
        rv.setdefault(key(r.get("pn")), r)
    out = []
    for b in rows:
        a = ask.get(b["k"]) or {}
        x = rv.get(b["k"]) or {}
        lo, hi = a.get("usd_lo"), a.get("usd_hi")
        found, found_src = found_price_usd(a, x, fx)
        bud_usd = b["rub"] / rub
        # Разряд готовности НАШЕЙ стороны: на чём мы вообще стоим по этой строке.
        if found is not None:
            grade = "цена найдена"
        elif lo not in (None, "") and hi not in (None, ""):
            grade = "только вилка"
        else:
            grade = "ничего"
        # Сходится ли строка. Сравниваем с НАЙДЕННОЙ ценой, если она есть, —
        # вилка это мнение, и объявлять по ней убыток нельзя.
        if found is not None:
            fit = "убыток" if bud_usd < found else "сходится"
        elif grade == "только вилка" and bud_usd < float(lo):
            fit = "бюджет ниже нашей оценки"
        else:
            fit = "не определено"
        out.append({**b, "budget_usd": bud_usd, "budget_sum_usd": b["sum_rub"] / rub,
                    "our_lo": lo, "our_hi": hi, "found": found, "found_src": found_src,
                    "seller": str(a.get("seller") or "")[:80],
                    "covers_qty": x.get("covers_qty") or a.get("covers_qty"), "verdict": vkey(x) if x else "",
                    "grade": grade, "fit": fit, "in_ask": bool(a),
                    "qty_ours": a.get("qty"), "channel": str(x.get("channel") or "")[:300],
                    "contacts": str(x.get("contacts") or "")[:300]})
    return out


def counters(j: list[dict], rub: float, meta: dict, src: Path) -> dict:
    def usd(rows):
        return round(sum(r["budget_sum_usd"] for r in rows), 2)

    grades = {}
    for g in ("цена найдена", "только вилка", "ничего"):
        rows = [r for r in j if r["grade"] == g]
        grades[g] = {"rows": len(rows), "budget_usd": usd(rows)}
    loss = [r for r in j if r["fit"] == "убыток"]
    short = sum((r["found"] - r["budget_usd"]) * r["qty"] for r in loss)
    covered = [r for r in loss if str(r.get("covers_qty") or "").strip().lower().startswith("full")]
    return {
        "updated": date.today().isoformat(),
        "source": f"Файл потребности заказчика «{src.name}» (вне репозитория) сведён с "
                  f"gt/data/ship_lukoil.json и gt/data/ship_reverify.json. "
                  f"Считает gt/tools/budget_join.py.",
        "what_it_is": "Только агрегаты. Построчная таблица бюджета — коммерческий документ "
                      "заказчика, она остаётся вне репозитория вместе с самим файлом.",
        "rate_rub_per_usd": rub,
        "sheets": meta.get("sheets", []),
        "rows_with_price": len(j),
        "rows_joined_to_ask": sum(1 for r in j if r["in_ask"]),
        "budget_total_usd": usd(j),
        "budget_by_our_readiness": grades,
        "found_price_by_source": {
            src: sum(1 for r in j if r["grade"] == "цена найдена" and r.get("found_src") == src)
            for src in ("перепроверка", "обход заявки")
        },
        "why_two_price_sources": "Цена перепроверки перечитана на странице продавца сейчас; "
                                 "цена обхода заявки прочитана при первом проходе и записана "
                                 "со своей валютой. Игнорирование второго источника держало "
                                 "101 строку в разряде «у нас нет ничего», хотя цена по ним "
                                 "записана и продавец назван.",
        "rows_budget_below_found_price": len(loss),
        "shortfall_usd_if_whole_volume": round(short, 2),
        "of_them_seller_confirmed_volume": len(covered),
        "what_shortfall_means": "Верхняя граница: допущение, что весь объём купится по "
                                "найденной цене. У строк, где продавец не подтвердил "
                                "количество, это допущение не проверено — поэтому отдельно "
                                "названо, у скольких подтверждение есть.",
        "why_no_margin_here": "Маржа по строкам, где наша сторона — экспертная вилка, не "
                              "считается: вилка это мнение, и разница с мнением прибылью не "
                              "является.",
        "qty_mismatch_rows": sum(1 for r in j if r["qty_ours"] not in (None, "")
                                 and float(r["qty_ours"]) != float(r["qty"])),
        "why_qty_mismatch": "Наша сводка сведена ПО НОМЕРУ, а файл заказчика — построчно: одна "
                            "и та же позиция может идти в нём дважды на две машины. Для "
                            "бюджета берётся количество строки файла.",
    }



def proposal(r: dict) -> str:
    """Что делать со строкой. Разряд задаётся тем, на чём стоит НАША сторона."""
    b = r["budget_usd"]
    if r["found"] is not None:
        if r["fit"] == "убыток":
            return (f"НЕ подавать по этой цене: закупка {money(r['found'])} USD/шт против "
                    f"бюджета {money(b)}. Пересогласовать цену строки или искать другое "
                    f"исполнение.")
        cov = str(r.get("covers_qty") or "").strip().lower().startswith("full")
        s = (f"Закупка найдена: {money(r['found'])} USD/шт при бюджете {money(b)} — "
             f"запас {ru((b - r['found']) * r['qty'])} USD на объём.")
        return s + (" Объём продавцом подтверждён, строку можно закладывать."
                    if cov else
                    " Объём НЕ подтверждён: нужен твёрдый оффер с остатком числом, "
                    "до него строка в сумму закупки не идёт.")
    if r["grade"] == "только вилка":
        hi, lo = float(r["our_hi"]), float(r["our_lo"])
        if b >= hi:
            k = b / hi if hi else 0
            return (f"Цены нет, наша оценка {money(lo)}–{money(hi)} USD/шт. Бюджет выше "
                    f"верхней границы оценки в {k:.0f} раз — запас есть, нужен твёрдый "
                    f"оффер по каналу из перепроверки.")
        return (f"Цены нет, наша оценка {money(lo)}–{money(hi)} USD/шт, а бюджет "
                f"{money(b)} НИЖЕ нижней границы. Либо в заявке другое изделие, либо "
                f"строка убыточна — вопрос заказчику до подачи цены.")
    return (f"Ни цены, ни оценки. Бюджет {money(b)} USD/шт ничем не подкреплён: запрос "
            f"изготовителю ({r['man'] or 'изготовитель в файле не указан'}) и в сервисную "
            f"сеть — цену по этой строке мы пока не знаем вовсе.")


def table(rows: list[dict], cols: list[tuple[str, str]]) -> str:
    h = ["<table><colgroup>"]
    h += [f"<col style='width:{w}'>" for _t, w in cols]
    h.append("</colgroup><thead><tr>")
    h += [f"<th{' class=n' if w.endswith('mm') and t[0] in '0123456789' else ''}>{E(t)}</th>"
          for t, w in cols]
    h.append("</tr></thead><tbody>")
    return "".join(h)


def build(j: list[dict], c: dict, src: Path, want: list[str]) -> str:
    a = []
    add = a.append
    add(f"<!doctype html><meta charset='utf-8'><title>Бюджет против закупки</title>"
        f"<style>{CSS}</style>")
    add("<h1>Бюджет заказчика против нашей закупки — построчно</h1>")
    add(f"<p class='dim'>Источник бюджета: «{E(src.name)}», лист(ы) "
        f"{E(', '.join(s['sheet'] for s in c.get('sheets') or []))}. Курс "
        f"{money(c['rate_rub_per_usd'])} руб/USD из gt/data/fx_rates.json. Наша сторона — "
        f"gt/data/ship_lukoil.json и gt/data/ship_reverify.json. Собрано "
        f"{E(c['updated'])} инструментом gt/tools/budget_join.py; ни одно число не набрано "
        f"руками.</p>")

    add("<div class='box'>")
    add(f"<p><b>Бюджет заказчика: {ru(c['budget_total_usd'])} USD</b> по "
        f"{ru(c['rows_with_price'])} строкам с ценой. Сошлось с нашей сводкой заявки по "
        f"каталожному номеру: {ru(c['rows_joined_to_ask'])}.</p>")
    add("<p>Разложение по тому, на чём стоит НАША сторона:</p><ul>")
    for g, v in c["budget_by_our_readiness"].items():
        add(f"<li><b>{E(g)}</b> — {ru(v['rows'])} строк на {ru(v['budget_usd'])} USD бюджета</li>")
    add("</ul>")
    add(f"<p><b>Строк, где бюджет ниже найденной цены закупки: "
        f"{ru(c['rows_budget_below_found_price'])}.</b> Если брать весь объём по найденной "
        f"цене, не хватает {ru(c['shortfall_usd_if_whole_volume'])} USD. Это верхняя "
        f"граница: продавец подтвердил количество только у "
        f"{ru(c['of_them_seller_confirmed_volume'])} из них.</p>")
    add(f"<p class='dim'>{E(c['why_no_margin_here'])} {E(c['why_qty_mismatch'])}</p>")
    add("</div>")

    if want:
        d = {r["num"]: r for r in j}
        sel = [d[n] for n in want if n in d]
        miss = [n for n in want if n not in d]
        tot = sum(r["budget_sum_usd"] for r in sel)
        add("<h2>Спрошенные строки: бюджет по каждой</h2>")
        add(f"<p>Запрошено {len(want)} строк, найдено в файле бюджета {len(sel)}"
            + (f"; нет в файле: {E(', '.join(miss))}" if miss else "")
            + f". Их бюджет — <b>{ru(tot)} USD</b>. "
            + "Из них " + "; ".join(
                f"«{g}» — {sum(1 for r in sel if r['grade'] == g)} строк на "
                f"{ru(sum(r['budget_sum_usd'] for r in sel if r['grade'] == g))} USD"
                for g in ("цена найдена", "только вилка", "ничего")) + ".</p>")
        cols0 = [("№", "9mm"), ("каталожный номер", "28mm"), ("кол", "8mm"),
                 ("бюджет USD/шт", "18mm"), ("бюджет строки, USD", "21mm"),
                 ("наша сторона, USD/шт", "24mm"), ("что делать", "auto")]
        add(table(sel, cols0))
        for r in sel:
            if r["found"] is not None:
                ours = money(r["found"]) + " найдено"
                cls = "stop" if r["fit"] == "убыток" else "take"
            elif r["grade"] == "только вилка":
                ours = f"{money(r['our_lo'])} – {money(r['our_hi'])} оценка"
                cls = "hold"
            else:
                ours = "нет"
                cls = ""
            add(f"<tr class='{cls}'><td>{E(r['num'])}</td><td>{E(r['pn'])}</td>"
                f"<td class='n'>{ru(r['qty'])}</td><td class='n'>{money(r['budget_usd'])}</td>"
                f"<td class='n'>{ru(r['budget_sum_usd'])}</td><td class='n'>{ours}</td>"
                f"<td>{E(proposal(r))}</td></tr>")
        add("</tbody></table>")
        add("<p class='dim'>Наименование и изготовитель по этим строкам — в разделе 3 "
            "ниже, там же вся заявка.</p>")

    loss = sorted([r for r in j if r["fit"] == "убыток"],
                  key=lambda r: -(r["found"] - r["budget_usd"]) * r["qty"])
    add("<h2>1. Бюджет ниже найденной цены закупки — строка не сходится</h2>")
    add("<p>Единственный разряд, где сравнение идёт с ЧИСЛОМ, а не с мнением: по этим "
        "строкам цена закупки найдена и прочитана на странице продавца. Цены заказчику "
        "твёрдые, торг идёт вниз — значит такая строка теряет деньги с первого дня.</p>")
    cols = [("№", "10mm"), ("каталожный номер", "34mm"), ("кол-во", "12mm"),
            ("бюджет USD/шт", "20mm"), ("закупка USD/шт", "20mm"),
            ("не хватает, USD", "22mm"), ("покрытие объёма", "34mm"), ("наименование", "auto")]
    add(table(loss, cols))
    for r in loss:
        gap = (r["found"] - r["budget_usd"]) * r["qty"]
        add(f"<tr class='stop'><td>{E(r['num'])}</td><td>{E(r['pn'])}</td>"
            f"<td class='n'>{ru(r['qty'])}</td><td class='n'>{money(r['budget_usd'])}</td>"
            f"<td class='n'>{money(r['found'])}</td><td class='n'>{ru(gap)}</td>"
            f"<td>{E(str(r.get('covers_qty') or '—')[:120])}</td>"
            f"<td>{E(r['name'][:110])}</td></tr>")
    add("</tbody></table>")

    nothing = sorted([r for r in j if r["grade"] == "ничего"],
                     key=lambda r: -r["budget_sum_usd"])[:60]
    add("<h2>2. Самый крупный бюджет там, где у нас нет ничего</h2>")
    add("<p>Ни найденной цены, ни даже вилки. Это не отсутствие рынка, а отсутствие нашей "
        "работы по строке; порядок — по деньгам бюджета, первые шестьдесят.</p>")
    cols2 = [("№", "10mm"), ("каталожный номер", "34mm"), ("кол-во", "12mm"),
             ("бюджет USD/шт", "20mm"), ("бюджет строки, USD", "24mm"),
             ("изготовитель", "30mm"), ("наименование", "auto")]
    add(table(nothing, cols2))
    for r in nothing:
        add(f"<tr class='hold'><td>{E(r['num'])}</td><td>{E(r['pn'])}</td>"
            f"<td class='n'>{ru(r['qty'])}</td><td class='n'>{money(r['budget_usd'])}</td>"
            f"<td class='n'>{ru(r['budget_sum_usd'])}</td><td>{E(r['man'][:40])}</td>"
            f"<td>{E(r['name'][:110])}</td></tr>")
    add("</tbody></table>")

    add("<h2>3. Все строки: бюджет, наша сторона, разница</h2>")
    add("<p>Разница печатается только там, где есть найденная цена. Где наша сторона — "
        "вилка, стоит сама вилка и пометка «мнение»: сравнивать бюджет с мнением можно "
        "для ориентира, но не для решения.</p>")
    cols3 = [("№", "9mm"), ("каталожный номер", "30mm"), ("кол", "9mm"),
             ("бюджет USD/шт", "18mm"), ("бюджет строки", "20mm"),
             ("наша закупка USD/шт", "24mm"), ("на чём стоит", "18mm"),
             ("разница на объём, USD", "22mm"), ("наименование", "auto")]
    add(table(j, cols3))
    for r in sorted(j, key=lambda z: -z["budget_sum_usd"]):
        if r["found"] is not None:
            ours = money(r["found"])
            basis = "найдено"
            diff = ru((r["budget_usd"] - r["found"]) * r["qty"])
            cls = "stop" if r["fit"] == "убыток" else "take"
        elif r["grade"] == "только вилка":
            ours = f"{money(r['our_lo'])} – {money(r['our_hi'])}"
            basis = "мнение"
            diff = "—"
            cls = "hold"
        else:
            ours = "—"
            basis = "нет"
            diff = "—"
            cls = ""
        add(f"<tr class='{cls}'><td>{E(r['num'])}</td><td>{E(r['pn'])}</td>"
            f"<td class='n'>{ru(r['qty'])}</td><td class='n'>{money(r['budget_usd'])}</td>"
            f"<td class='n'>{ru(r['budget_sum_usd'])}</td><td class='n'>{ours}</td>"
            f"<td>{basis}</td><td class='n'>{diff}</td><td>{E(r['name'][:90])}</td></tr>")
    add("</tbody></table>")
    add(f"<p class='dim'>Собирает gt/tools/budget_join.py. Агрегаты — "
        f"gt/data/budget_join_stats.json; построчная таблица в репозиторий не кладётся: "
        f"это коммерческий документ заказчика.</p>")
    return "".join(a)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", required=True, help="файл потребности заказчика (.xlsx)")
    ap.add_argument("--pdf", default="", help="куда положить построчный документ (ВНЕ репозитория)")
    ap.add_argument("--rows", default="", help="№ п/п через запятую: построчный ответ по названным строкам первой секцией")
    ap.add_argument("--counters", default="", help="куда выписать агрегаты (можно в репозиторий)")
    a = ap.parse_args()

    src = Path(a.budget)
    if not src.exists():
        print(f"нет файла {src}", file=sys.stderr)
        return 1
    rub = rub_per_usd()
    rows, meta = read_budget(src)
    if not rows:
        print("в файле не нашлось ни одной строки с номером и ценой: проверьте заголовки",
              file=sys.stderr)
        return 1
    j = join(rows, rub)
    c = counters(j, rub, meta, src)
    print(f"строк с ценой: {c['rows_with_price']}; сошлось с заявкой: {c['rows_joined_to_ask']}")
    print(f"бюджет заказчика: {ru(c['budget_total_usd'])} USD")
    for g, v in c["budget_by_our_readiness"].items():
        print(f"  {g:<14} {v['rows']:>5} строк | {ru(v['budget_usd']):>12} USD")
    print(f"бюджет ниже найденной закупки: {c['rows_budget_below_found_price']} строк, "
          f"не хватает {ru(c['shortfall_usd_if_whole_volume'])} USD "
          f"(объём подтверждён у {c['of_them_seller_confirmed_volume']})")

    if a.counters:
        p = Path(a.counters)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(c, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"\n{p}")
    if a.pdf:
        out = Path(a.pdf).resolve()
        try:
            out.relative_to(ROOT)
        except ValueError:
            pass
        else:
            print(f"ОТКАЗ: {out} внутри репозитория, а документ содержит бюджет заказчика "
                  f"построчно.", file=sys.stderr)
            return 2
        htm = out.with_suffix(".html")
        out.parent.mkdir(parents=True, exist_ok=True)
        want = [x.strip() for x in a.rows.split(",") if x.strip()]
        htm.write_text(build(j, c, src, want), encoding="utf-8")
        exe = next((x for x in CHROME if Path(x).exists()), None)
        if not exe:
            print("Chromium не найден — PDF не собран", file=sys.stderr)
            return 1
        subprocess.run(
            [exe, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
             "--run-all-compositor-stages-before-draw", "--virtual-time-budget=180000",
             f"--print-to-pdf={out}", htm.as_uri()], check=True, capture_output=True)
        print(f"{out} — {out.stat().st_size / 1e6:.2f} МБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
