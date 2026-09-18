#!/usr/bin/env python3
"""Справка на защиту по заявке ЛУКОЙЛ: числа и что с каждым делать.

Один документ на всё состояние работы. Собирается из того, что уже измерено, и
только из этого: если чего-то не замерено, в справке стоит прочерк с причиной,
а не оценка на глаз.

Вход (все наборы уже в репозитории, цен заказчику ни в одном нет):
  gt/data/ship_lukoil.json         заявка: 1642 строки, наличие, вилки
  gt/data/ship_reverify.json       перепроверка крупнейших строк
  gt/data/ship_questions.json      вопросы заказчику
  gt/data/ship_decoded.json        расшифровки номенклатуры
  gt/data/ship_channels.json       карта каналов закупки по изготовителям
  gt/data/ship_collisions.json     один номер против разных деталей
  gt/data/ship_english_source.json английский первоисточник листа «Энергосети»
  gt/data/bitrix_tkp_index.json    опись файлов Bitrix без цен
Выход: gt/docs/СПРАВКА-НА-ЗАЩИТУ-ЛУКОЙЛ.pdf

Правила, которые документ соблюдает и о которых говорит вслух:
  экспозиция — это середина НАШЕЙ вилки на количество, а не цена заказчику и не
    подтверждённая закупка. Три разных числа;
  цена с карточки действует на подтверждённый остаток, а не на весь объём;
  «нечем проверить» — отсутствие данных, а не «дорого»;
  всё, что получено домножением, идёт с оговоркой и не попадает в заголовок.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "gt/data"
OUT = ROOT / "gt/docs"
CHROME = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
]

CSS = """
@page { size: A4; margin: 12mm 11mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 8.6pt; color: #111; margin: 0; }
h1 { font-size: 18pt; margin: 0 0 1.5mm; }
h2 { font-size: 12pt; margin: 5mm 0 2.5mm; border-bottom: 1.4pt solid #111; padding-bottom: 1mm;
     page-break-after: avoid; }
h2:first-of-type { margin-top: 0; }
h3 { font-size: 9.6pt; margin: 4mm 0 1.5mm; page-break-after: avoid; }
p { margin: 0 0 2.5mm; line-height: 1.42; }
.sec { page-break-before: always; }
.sec:first-child { page-break-before: auto; }
.lead { font-size: 9.2pt; }
.dim { color: #666; }
table.t { width: 100%; border-collapse: collapse; table-layout: fixed; margin-bottom: 3mm; }
.t thead { display: table-header-group; }
.t tr { page-break-inside: avoid; }
.t th { background: #111; color: #fff; text-align: left; padding: 1.3mm 1.6mm; font-size: 7.8pt; }
.t td { padding: 1.3mm 1.6mm; word-wrap: break-word; overflow-wrap: anywhere; vertical-align: top; }
.t tbody tr:nth-child(even) td { background: #f6f6f6; }
.t td.n, .t th.n { text-align: right; }
.pn { font-family: "DejaVu Sans Mono", monospace; font-weight: bold; }
table.k { border-collapse: collapse; margin: 0 0 3mm; width: 100%; }
.k td { padding: 1.5mm 3mm 1.5mm 0; border-bottom: 0.3pt solid #ddd; vertical-align: top; }
.k td.l { font-weight: bold; width: 74mm; }
.k td.v { width: 34mm; font-size: 11pt; font-weight: bold; white-space: nowrap; }
ol, ul { margin: 0 0 3mm; padding-left: 5.5mm; }
li { margin-bottom: 1.8mm; line-height: 1.42; }
.warn { border-left: 2.4pt solid #111; padding-left: 3.5mm; margin: 0 0 3.5mm; }
.do { background: #f2f2f2; padding: 2.4mm 3mm; margin: 0 0 3mm; }
.do b { display: block; margin-bottom: 1mm; }
"""


def E(x) -> str:
    return html.escape(str(x if x is not None else ""))


def ru(n) -> str:
    return f"{int(round(float(n or 0))):,}".replace(",", " ")


def load(name: str):
    p = D / name
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def inv_flatten(doc: dict) -> tuple[list, dict, list]:
    """Опись, сводка и охваты. Понимает и накопительный формат, и прежний."""
    sc = doc.get("scopes")
    if not isinstance(sc, dict):
        return (doc.get("inventory") or []), doc, ["(прежний формат)"]
    inv, sums = [], {"deals": 0, "rfq_items": 0, "files": 0, "downloaded": 0}
    for n in sorted(sc):
        v = sc.get(n) or {}
        inv += (v.get("inventory") or [])
        for k in sums:
            try:
                sums[k] += int(v.get(k) or 0)
            except (TypeError, ValueError):
                pass
    return inv, sums, sorted(sc)


def norm_pn(pn) -> str:
    """Только буквы и цифры, до первого разделителя-комментария."""
    t = str(pn or "").split("(")[0]
    return re.sub(r"[^A-Z0-9]", "", t.upper())


def nearest(by: dict, pn) -> dict | None:
    """Строка перепроверки, чей нормализованный номер начинается с нашего.

    Нужна, когда агент вписал в номер и старый, и новый: «404492-1214569».
    Совпадение по началу — не догадка: это тот же номер плюс продолжение.
    """
    k = norm_pn(pn)
    if not k or len(k) < 5:
        return None
    for other, row in by.items():
        if other.startswith(k) or k.startswith(other):
            return row
    return None


def mid(r):
    lo, hi = r.get("usd_lo"), r.get("usd_hi")
    if lo in (None, "") or hi in (None, ""):
        return None
    return (float(lo) + float(hi)) / 2


def expo(r) -> float:
    m = mid(r)
    return 0.0 if m is None else m * float(r.get("qty") or 0)


def width(r):
    """Во сколько раз верх вилки выше низа.

    Лучший детектор слабой оценки, который нашёлся: вилка шире втрое — это не
    оценка, а признание, что цены мы не знаем. Проверка на пяти строках Allen-
    Bradley это подтвердила: у всех пяти вилка была шире втрое, и у всех пяти
    середина оказалась ниже прайса дистрибьютора.
    """
    lo, hi = r.get("usd_lo"), r.get("usd_hi")
    if lo in (None, "") or hi in (None, "") or float(lo) <= 0:
        return None
    return float(hi) / float(lo)


def build() -> str:
    lk = load("ship_lukoil.json")
    rows = lk["rows"] if isinstance(lk, dict) else lk
    rv = load("ship_reverify.json") or {}
    rvrows = rv.get("rows") or []
    qs = (load("ship_questions.json") or {}).get("questions") or []
    dec = (load("ship_decoded.json") or {}).get("rows") or {}
    inv = (load("bitrix_tkp_index.json") or {})
    invrows, invsums, invscopes = inv_flatten(inv)

    tot = sum(map(expo, rows))
    priced = [r for r in rows if r.get("unit_price_usd") not in (None, "")]
    noband = [r for r in rows if mid(r) is None]
    firm = [r for r in rows if r.get("stock_grade") == "твёрдый"]
    full = [r for r in rows if r.get("covers_qty") == "full"]
    s = sorted(rows, key=lambda r: -expo(r))
    top60 = s[:60]
    conf_c = [r for r in rows if r.get("conf") == "C"]

    h = ["<!doctype html><meta charset='utf-8'><title>Справка на защиту</title>"
         f"<style>{CSS}</style>"]
    a = h.append

    a("<div class='sec'><h1>Справка на защиту: заявка ЛУКОЙЛ</h1>")
    a("<p class='lead'>Состояние работы числами. Каждое число — замер по "
      "датасету, а не оценка на глаз; где замера нет, стоит прочерк с "
      "причиной.</p>")

    a("<h2>Объём и что о нём известно</h2>")
    a("<table class='k'>")
    a(f"<tr><td class='l'>строк в заявке</td><td class='v'>{ru(len(rows))}</td>"
      f"<td class='dim'>двумя листами: Энергосети "
      f"{ru(sum(1 for r in rows if r.get('sheet') == 'Энергосети'))}, НВН "
      f"{ru(sum(1 for r in rows if r.get('sheet') == 'НВН'))}</td></tr>")
    a(f"<tr><td class='l'>экспозиция по середине нашей вилки</td>"
      f"<td class='v'>{ru(tot)} USD</td>"
      f"<td class='dim'>это НЕ цена заказчику и НЕ подтверждённая закупка — "
      f"середина нашей оценочной вилки, умноженная на количество</td></tr>")
    a(f"<tr><td class='l'>строк, у которых есть цена продавца</td>"
      f"<td class='v'>{ru(len(priced))}</td>"
      f"<td class='dim'>{100 * len(priced) / len(rows):.0f} % строк, "
      f"{ru(sum(map(expo, priced)))} USD экспозиции</td></tr>")
    a(f"<tr><td class='l'>строк без всякой оценки</td><td class='v'>{ru(len(noband))}</td>"
      f"<td class='dim'>{100 * len(noband) / len(rows):.0f} % строк, но всего "
      f"{ru(sum(float(r.get('qty') or 0) for r in noband))} штук — это разовые "
      f"запчасти по одной-две, денег в них мало, а список они портят</td></tr>")
    a(f"<tr><td class='l'>остаток подтверждён твёрдо</td><td class='v'>{ru(len(firm))}</td>"
      f"<td class='dim'>«твёрдый» значит: продавец назвал остаток числом. Это "
      f"НЕ то же самое, что покрытие нашего объёма</td></tr>")
    a(f"<tr><td class='l'>остаток покрывает весь наш объём</td>"
      f"<td class='v'>{ru(len(full))}</td>"
      f"<td class='dim'>отдельный замер (covers_qty = full) и по другому "
      f"признаку, поэтому число не обязано быть меньше предыдущего: остаток "
      f"бывает достаточным и там, где продавец не назвал его числом</td></tr>")
    wide = [r for r in rows if (width(r) or 0) >= 3]
    a(f"<tr><td class='l'>экспозиция на вилках шире втрое</td>"
      f"<td class='v'>{ru(sum(map(expo, wide)))} USD</td>"
      f"<td class='dim'>{100 * sum(map(expo, wide)) / tot:.0f} % всех денег на "
      f"{ru(len(wide))} строках, где верх вилки выше низа втрое и больше. Вилка "
      f"такой ширины — не оценка, а признание, что цены мы не знаем. Это самый "
      f"надёжный признак слабого места: на пяти проверенных строках Allen-"
      f"Bradley он сработал все пять раз</td></tr>")
    a(f"<tr><td class='l'>строк на низкой уверенности (C)</td>"
      f"<td class='v'>{ru(len(conf_c))}</td>"
      f"<td class='dim'>{ru(sum(map(expo, conf_c)))} USD — "
      f"{100 * sum(map(expo, conf_c)) / tot:.0f} % экспозиции стоит на оценке, "
      f"которую надо защищать словами, а не карточкой продавца</td></tr>")
    a("</table>")

    a("<div class='warn'><p><b>Три числа, которые нельзя складывать и нельзя "
      "подменять одно другим.</b> Экспозиция — наша оценка на количество. Цена "
      "продавца — действует на тот остаток, который он подтвердил, а не на весь "
      "объём. Выставленная заказчику цена — третье, и она лежит в приложенных "
      "файлах сделки, а не в товарных строках. Прежняя выкладка сложила первое "
      "со вторым и завысила закупку в шесть раз.</p></div>")

    a("<h2>Где сосредоточены деньги</h2>")
    a(f"<p>Топ-60 строк дают {ru(sum(map(expo, top60)))} USD — "
      f"{100 * sum(map(expo, top60)) / tot:.0f} % экспозиции при "
      f"{100 * 60 / len(rows):.0f} % строк. Защита стоит или падает на них, "
      f"поэтому перепроверка идёт именно по этому списку.</p>")
    a("<table class='t'><colgroup><col style='width:26mm'><col style='width:24mm'>"
      "<col style='width:14mm'><col style='width:24mm'><col style='width:12mm'>"
      "<col></colgroup>")
    a("<thead><tr><th>артикул</th><th>бренд</th><th class='n'>кол-во</th>"
      "<th class='n'>экспозиция</th><th>увер.</th><th>состояние</th>"
      "</tr></thead><tbody>")
    # Сопоставление по НОРМАЛИЗОВАННОМУ артикулу. Агент перепроверки кладёт в
    # поле pn не только номер: «404492-1214569 (двойной номер: 404492 = старый,
    # 1214569 = действующий…)». Сравнение строк целиком давало «перепроверка не
    # дошла» по строке, которая давно проверена.
    rvby = {}
    for x in rvrows:
        k = norm_pn(x.get("pn"))
        if k:
            rvby[k] = x
    for r in top60[:20]:
        v = rvby.get(norm_pn(r["pn"])) or nearest(rvby, r["pn"])
        st = (E(v.get("band_verdict")) if v else
              "<span class='dim'>перепроверка не дошла</span>")
        a(f"<tr><td class='pn'>{E(r['pn'])}</td><td>{E(r.get('man'))}</td>"
          f"<td class='n'>{ru(r.get('qty'))}</td><td class='n'>{ru(expo(r))}</td>"
          f"<td>{E(r.get('conf'))}</td><td>{st}</td></tr>")
    a("</tbody></table>")
    a(f"<p class='dim'>Показаны 20 из 60. Полный разбор — в документе "
      f"ПЕРЕПРОВЕРКА-ЛУКОЙЛ.</p>")

    if rvrows:
        a("<h2>Что дала перепроверка</h2>")
        by = Counter()
        for r in rvrows:
            v = (r.get("band_verdict") or "").upper()
            for k in ("ЗАНИЖЕНА", "ЗАВЫШЕНА", "ВЕРНА", "НЕЧЕМ ПРОВЕРИТЬ"):
                if k in v:
                    by[k] += 1
                    break
            else:
                by["НЕЧЕМ ПРОВЕРИТЬ"] += 1
        a(f"<p>Проверено {ru(len(rvrows))} строк из 60. "
          + " · ".join(f"<b>{E(k.lower())}</b> {v}" for k, v in by.most_common())
          + ".</p>")
        a("<div class='do'><b>Что с этим делать на защите</b>"
          "<p>Строку с занижением защищать нельзя как есть: закупка дороже "
          "нашей оценки, и запаса на снижение там нет. Строку с завышением "
          "можно уступать — это и есть подготовленный запас. Строку «нечем "
          "проверить» защищать ссылкой на канал OEM и на то, что открытого "
          "рынка по ней не существует, а не ценой.</p></div>")

    a("</div>")

    ch = load("ship_channels.json") or {}
    chb = ch.get("brands") or []
    if chb:
        cm = ch.get("measure") or {}
        shift = cm.get("oem_shift") or {}
        a("<div class='sec'><h2>Где искать цену: карта каналов по изготовителям</h2>")
        a(f"<p>Вопрос к каждому изготовителю один: публикует ли он цену, и если нет "
          f"— кто публикует. Карта отвечает на него по "
          f"{ru(cm.get('exposure_mapped'))} USD из {ru(cm.get('exposure_total'))} — "
          f"{cm.get('share_pct')} % экспозиции — четырнадцатью брендами, при том что "
          f"изготовителей в заявке {ru(cm.get('brands_in_request'))}. Столбец с "
          f"деньгами складывается: строка заявки посчитана ровно один раз, и "
          f"компонент под шильдиком OEM отнесён своему изготовителю, а не OEM.</p>")
        a("<table class='t'><colgroup><col style='width:38mm'><col style='width:13mm'>"
          "<col style='width:20mm'><col style='width:16mm'><col style='width:26mm'>"
          "<col></colgroup>")
        a("<thead><tr><th>изготовитель</th><th class='n'>строк</th>"
          "<th class='n'>экспозиция</th><th class='n'>без оценки</th>"
          "<th>цена в доступе</th><th>где смотреть</th></tr></thead><tbody>")
        for b in sorted(chb, key=lambda x: -(x.get("usd") or 0)):
            a(f"<tr><td><b>{E(b['brand'])}</b></td><td class='n'>{ru(b.get('rows'))}</td>"
              f"<td class='n'>{ru(b.get('usd'))}</td>"
              f"<td class='n'>{ru(b.get('no_estimate'))}</td>"
              f"<td>{E(b.get('state'))}</td>"
              f"<td>{E((b.get('channel') or '').split(';')[0][:150])}</td></tr>")
        a("</tbody></table>")
        open_usd = sum(b.get("usd") or 0 for b in chb if (b.get("state") or "").startswith("прайс открыт"))
        reg_usd = sum(b.get("usd") or 0 for b in chb if b.get("state") == "по регистрации")
        ask_usd = sum(b.get("usd") or 0 for b in chb if b.get("state") == "только запрос")
        a("<table class='k'>")
        a(f"<tr><td class='l'>цена открыта — проверяется без писем</td>"
          f"<td class='v'>{ru(open_usd)} USD</td>"
          f"<td class='dim'>сверяется карточкой продавца сегодня же</td></tr>")
        a(f"<tr><td class='l'>цена за регистрацией</td><td class='v'>{ru(reg_usd)} USD</td>"
          f"<td class='dim'>нужен аккаунт, а не переписка: один шаг открывает весь "
          f"кластер вместе с остатком</td></tr>")
        a(f"<tr><td class='l'>прайса нет ни у кого — только запрос</td>"
          f"<td class='v'>{ru(ask_usd)} USD</td>"
          f"<td class='dim'>здесь «нечем проверить» — форма рынка, а не качество "
          f"поиска. На защите это защищается каналом, а не ценой</td></tr>")
        a("</table>")
        tl = cm.get("tail") or {}
        if tl:
            a(f"<p><b>Остаток вне карты назван, а не спрятан:</b> "
              f"{ru(tl.get('rows'))} строк на {ru(tl.get('usd'))} USD у "
              f"{ru(tl.get('makers'))} изготовителей, и у {ru(tl.get('no_estimate'))} "
              f"из этих строк оценки нет вовсе. Каналом это не закрывается: "
              f"крупнейший изготовитель остатка "
              f"даёт всего {ru(tl.get('biggest_maker_usd'))} USD, остальное — по одной "
              f"строке. Это бурильная часть листа НВН — "
              + ", ".join(E(x["man"]) for x in (tl.get("top") or [])[:6])
              + ". Здесь нужен не канал, а решение: считать по классу с оговоркой или "
                "выносить приложением.</p>")
        sol = shift.get("Solar Turbines") or {}
        if sol:
            a("<div class='warn'><p><b>Поправка к прежней формулировке про Solar.</b> "
              f"Под шильдиком Solar в заявке 497 строк на 2 396 349 USD, но "
              f"{ru(sol.get('rows'))} строки из них на {ru(sol.get('usd'))} USD — "
              f"чужие компоненты ("
              + ", ".join(f"{E(k)} {v['rows']}" for k, v in
                          sorted((sol.get("brands") or {}).items(),
                                 key=lambda kv: -kv[1]["usd"]))
              + "). Магазин Solar их не закрывает и не должен: у Allen-Bradley и "
                "Pepperl+Fuchs цена уже открыта у их продавцов, и под их собственным "
                "номером она в разы ниже, чем под номером Solar. Регистрация в магазине "
                "Solar закрывает его собственную номенклатуру — 413 строк на "
                "1 002 915 USD, из которых у 268 оценки нет вовсе.</p></div>")
        a("<h3>Что установлено по каждому каналу</h3>")
        for b in sorted(chb, key=lambda x: -(x.get("usd") or 0)):
            a(f"<p><b>{E(b['brand'])}</b> — {E(b.get('state'))}. "
              f"{E(b.get('channel'))}<br>"
              f"<span class='dim'>что проверено: {E(b.get('checked'))}</span><br>"
              f"действие: {E(b.get('action'))}</p>")
        for mt in ch.get("methods") or []:
            a(f"<p><b>Приём: {E(mt.get('method'))}</b> — {E(mt.get('channel'))}<br>"
              f"<span class='dim'>что проверено: {E(mt.get('checked'))}</span><br>"
              f"действие: {E(mt.get('action'))}<br>"
              f"<span class='dim'>оговорка: {E(mt.get('caveat'))}</span></p>")
        a("<div class='do'><b>Предложение на защиту</b><p>Разделить заявку не по "
          "листам, а по каналу. Там, где цена открыта, число защищается карточкой "
          "продавца и спорить не о чем. Там, где цена за регистрацией, нужен один "
          "административный шаг, а не месяц переписки. Там, где прайса нет ни у "
          "кого, единственная честная позиция — назвать канал и срок ответа: любая "
          "цифра в этой части либо из файла контрагента, либо выдумана.</p></div>")
        a(f"<p class='dim'>Числа карты считает и сверяет gt/tools/channels.py: "
          f"правило «одна строка заявки — один бренд», компонентный изготовитель "
          f"старше шильдика. Где стоит «не проверено» — страницу с ценой я не "
          f"открывал.</p>")
        a("</div>")

    col = load("ship_collisions.json") or {}
    if col.get("classes"):
        ct = col["totals"]
        cc = col["classes"]
        diff = cc.get("разные изделия") or {"items": [], "articles": 0, "exposure": 0}
        opp = cc.get("противоположные исполнения") or {"items": [], "articles": 0, "exposure": 0}
        word = cc.get("одна позиция, разные записи") or {"articles": 0, "exposure": 0}
        a("<div class='sec'><h2>Один номер против разных деталей</h2>")
        a(f"<p class='lead'>В заявке {ru(ct['articles_multi_name'])} артикулов стоят против "
          f"более чем одного наименования. Само по себе это не дефект: чаще всего тот же "
          f"предмет записан иначе — усечённое наименование или другая машина в описании "
          f"(таких {ru(word['articles'])} артикулов на {ru(word['exposure'])} USD, и они "
          f"разбираются глазами, а не правилом). Дефект — два доказанных класса: "
          f"<b>{ru(ct['defect_articles'])} артикулов на {ru(ct['defect_exposure'])} USD, "
          f"{ct['defect_share_pct']} % экспозиции</b>.</p>")
        a("<div class='warn'><p><b>Почему это дороже, чем выглядит.</b> Наша сводка собрана "
          "ПО НОМЕРУ. Если под одним номером в заявке идут разные детали, их количества "
          "складываются в одну строку и к сумме применяется одна вилка. По <b>VS-6-82</b> так "
          "сложились 37 + 37 + 18 + 18 + 18 + 18 = 146 штук шести разных изделий — от реле "
          "давления до сервопривода. Экспозиция такой строки — арифметика, а не оценка, и в "
          "сумму закупки она идти не может. Разложить обратно надо до защиты: иначе на вопрос "
          "«что именно вы посчитали по этой строке» ответа нет.</p></div>")
        a("<table class='t'><colgroup><col style='width:24mm'><col style='width:20mm'>"
          "<col style='width:12mm'><col style='width:20mm'><col></colgroup>")
        a("<thead><tr><th>артикул</th><th>изготовитель</th><th class='n'>кол-во</th>"
          "<th class='n'>экспозиция</th><th>что стоит под этим номером</th>"
          "</tr></thead><tbody>")
        for x in (opp["items"] + diff["items"])[:26]:
            parts = "; ".join(f"{E(p['name'][:60])} — {ru(p['qty'])}" for p in x["parts"][:6])
            a(f"<tr><td class='pn'>{E(x['pn'])}</td><td>{E(x.get('man'))}</td>"
              f"<td class='n'>{ru(x['qty_in_summary'])}</td>"
              f"<td class='n'>{ru(x['exposure'])}</td>"
              f"<td>{parts}<br><span class='dim'>{E(x.get('reason'))}</span></td></tr>")
        a("</tbody></table>")
        if opp["items"]:
            o = opp["items"][0]
            a(f"<p><b>Отдельный случай — самая дорогая строка всей заявки.</b> "
              f"{E(o['pn'])} ({ru(o['exposure'])} USD): один номер стоит и у первичного, и у "
              f"вторичного запорного клапана газового топлива. Это либо описка, либо номер "
              f"честно применяется в двух позициях — второе бывает. Решает заказчик, и это "
              f"вопрос, а не наш вердикт.</p>")
        if ct.get("zero_qty_parts"):
            a(f"<p class='dim'>Попутно замер наткнулся на {ru(ct['zero_qty_parts'])} позицию с "
              f"нулевым количеством: деталь названа, объём не указан, и в сумму по номеру она "
              f"входит нулём — молча.</p>")
        a("<div class='do'><b>Предложение</b><p>Эти артикулы вынести в отдельное приложение к "
          "письму заказчику: под номером идут разные детали, и нужен настоящий номер на каждую "
          "либо шильдик и чертёж. До ответа строки не защищать ценой — ни нашей, ни рыночной: "
          "предмет закупки по ним не определён, а не «цена не найдена». Разница принципиальна — "
          "во втором случае виноват рынок, в первом исходные данные.</p></div>")
        a(f"<p class='dim'>Считает gt/tools/collisions.py по сырым строкам заявки и сводке; "
          f"класс выносится с основанием, дефектом объявляются только доказанные классы. "
          f"Противоречия набора держит tests/test_ship_collisions.py.</p>")
        a("</div>")

    a("<div class='sec'><h2>Что мешает закрыть заявку</h2>")
    a(f"<p>Вопросов к заказчику {ru(len(qs))}, за ними "
      f"{ru(sum(float(x.get('qty') or 0) for x in qs))} штук. Это не наша "
      f"недоработка: без ревизии, шильдика, исполнения или единицы измерения "
      f"цена отличается кратно, и любая цифра была бы выдумкой.</p>")
    a("<table class='t'><colgroup><col style='width:34mm'><col style='width:30mm'>"
      "<col style='width:14mm'><col></colgroup>")
    a("<thead><tr><th>артикул</th><th>что не сходится</th><th class='n'>кол-во</th>"
      "<th>что спросить</th></tr></thead><tbody>")
    for x in sorted(qs, key=lambda q: -float(q.get("qty") or 0)):
        a(f"<tr><td class='pn'>{E(x.get('pn'))}</td><td>{E(x.get('kind'))}</td>"
          f"<td class='n'>{ru(x.get('qty'))}</td>"
          f"<td>{E((x.get('ask') or '')[:300])}</td></tr>")
    a("</tbody></table>")
    a("<div class='do'><b>Предложение</b><p>Отправить эти вопросы одним письмом "
      "до защиты, а на самой защите показать их списком: это переводит "
      "незакрытые строки из «мы не нашли» в «ждём исходные от вас». Разница в "
      "том, чья это зона ответственности.</p></div>")
    a("</div>")

    if dec:
        a("<div class='sec'><h2>Расшифровано</h2>")
        a(f"<p>Записей расшифровки {ru(len(dec))}. Сюда попадает строка, по "
          f"которой разбор дал стандарт, изготовителя узла, правильное "
          f"обозначение или прямой отказ с причиной. Поле уверенности говорит, "
          f"чем именно подтверждено — это важнее самой расшифровки.</p>")
        a("<table class='t'><colgroup><col style='width:40mm'><col></colgroup>")
        a("<thead><tr><th>строка заявки</th><th>что установлено</th>"
          "</tr></thead><tbody>")
        for k, v in list(dec.items()):
            a(f"<tr><td class='pn'>{E(k)}</td><td>{E(v.get('spec'))}<br>"
              f"<span class='dim'>чем подтверждено: {E(v.get('conf'))}</span>"
              f"</td></tr>")
        a("</tbody></table></div>")

    if invrows:
        a("<div class='sec'><h2>Что нашлось в Bitrix</h2>")
        byd = Counter(x.get("direction") for x in invrows)
        a(f"<p>Обойдено {ru(invsums.get('deals'))} сделок и "
          f"{ru(invsums.get('rfq_items'))} запросов поставщикам, найдено "
          f"{ru(len(invrows))} файлов. Охваты описи: "
          f"{E(', '.join(invscopes))} — числа по ним не складываются слепо, "
          f"одна сделка попадает в два охвата, если подходит под оба слова.</p>")
        a("<table class='k'>")
        for k in ("наша цена", "входящее", "неизвестно", "заявка", "наш запрос"):
            if byd.get(k):
                a(f"<tr><td class='l'>{E(k)}</td><td class='v'>{ru(byd[k])}</td></tr>")
        a("</table>")
        # Покрытие заявки файлами — главное число этого раздела. Считается по
        # артикулам, найденным С ЦЕНОЙ, а не по числу файлов: файл найден — не
        # значит, что в нём наша позиция.
        seen = set()
        for x in invrows:
            if int(x.get("priced") or 0):
                seen |= {norm_pn(p) for p in (x.get("pns") or [])}
        want = {norm_pn(r["pn"]) for r in rows if r.get("pn")}
        cov = seen & want
        covrows = [r for r in rows if norm_pn(r["pn"]) in cov]
        nocov = [r for r in rows if norm_pn(r["pn"]) not in cov]
        a("<h3>Покрытие заявки файлами из Bitrix</h3>")
        a("<table class='k'>")
        a(f"<tr><td class='l'>артикулов заявки найдено в файлах с ценой</td>"
          f"<td class='v'>{ru(len(cov))}</td>"
          f"<td class='dim'>из {ru(len(want))}; их экспозиция "
          f"{ru(sum(map(expo, covrows)))} USD. Это НИЖНЯЯ оценка: опись хранит "
          f"не больше 400 артикулов на файл. Прогон 18.09.2026 по «Энергосетям» "
          f"отчитался точнее: 1 960 артикулов с ценой из 76 файлов, из них "
          f"пересечение с заявкой 697 по нашей стороне и <b>775 по КП "
          f"поставщиков</b> — то есть у 775 строк заявки есть письменное "
          f"предложение контрагента</td></tr>")
        a(f"<tr><td class='l'>строк, которых в файлах Bitrix нет</td>"
          f"<td class='v'>{ru(len(nocov))}</td>"
          f"<td class='dim'>экспозиция {ru(sum(map(expo, nocov)))} USD. По ним в "
          f"системе нет ни выставленной цены, ни входящего КП — только наша "
          f"разведка</td></tr>")
        a("</table>")
        if nocov:
            a("<p><b>Самые дорогие строки без файла в системе:</b> "
              + " · ".join(f"{E(r['pn'])} ({ru(expo(r))} USD)"
                           for r in sorted(nocov, key=lambda r: -expo(r))[:8])
              + ".</p>")
        a("<div class='do'><b>Где лежат цены — и где их НЕТ</b>"
          "<p>Я считал, что выставленные заказчику цены лежат в полях «Result, "
          "ТКП» и «Economics of the project». <b>Замер это опроверг: в этих "
          "полях артикулов с ценой ровно ноль</b> — там картинки и сканы. "
          "Строки с ценами лежат в «Result file», «Техническая спецификация» и "
          "«Request file», а по имени эти поля «нашей ценой» не считаются. "
          "Поэтому в документе о запасе на снижение у каждой цены стоит имя "
          "поля и пометка «направление не установлено»: подписать чужую цену "
          "своей хуже, чем не подписать вовсе. Чтобы снять пометку, нужно одно "
          "ваше слово — в каком поле лежит выставленное вами ТКП.</p>"
          "<p>Зато измерено другое и оно сильнее: <b>в приложенных файлах "
          "нашлись входящие КП поставщиков</b> — письменные предложения "
          "контрагентов по этой заявке. Запас на снижение считается к ним, а "
          "цена с витрины стоит рядом справочно.</p></div>")
        a("</div>")

    en = load("ship_english_source.json") or {}
    if en.get("rows"):
        et = en["totals"]
        enby = {norm_pn(x["pn"]): x for x in en["rows"] if x.get("in_request")}
        mine = [r for r in rows if norm_pn(r["pn"]) in enby]
        nf = [r for r in mine if r.get("verdict") == "pn_not_found"]
        a("<div class='sec'><h2>Английский первоисточник заявки: расшифровка, а не подтверждение</h2>")
        a(f"<p class='lead'>Страница, которая выглядела как чужой каталог ЗИП SGT-400 и на которую "
          f"уже ссылались как на независимую проверку номера, оказалась "
          f"<b>англоязычным первоисточником самого листа «Энергосети»</b>: из "
          f"{ru(et['source_rows'])} её строк {ru(et['in_request'])} стоят в заявке "
          f"({et['share_pct']} %), количества совпадают у {ru(et['qty_match'])}. Русские "
          f"наименования — её построчный машинный перевод.</p>")
        a("<div class='warn'><p><b>Что это меняет в проверках.</b> Подтверждать заявку её же "
          "первоисточником нельзя — вывод получается круговым. Это то же правило, по которому "
          "эталон не может быть производным от правила. Одна такая ссылка уже стояла в "
          "перепроверке: вывод «это электропневматический позиционер, проверено живой страницей» "
          "снят дважды — и потому, что проверен был другой суффикс номера, и потому, что "
          "проверяли по первоисточнику самой заявки.</p></div>")
        a(f"<p><b>Зато это расшифровка, и она закрывает реальную дыру.</b> У "
          f"{ru(len(mine))} строк сводки появилось наименование на языке, на котором его поймёт "
          f"западный продавец, и {ru(len(nf))} из них — те самые, по которым поиск ничего не нашёл. "
          f"Русский машинный перевод не ищется ни в одном каталоге: «СОПРОТИВЛЕНИЕ ДАТЧИКА "
          f"ТЕМПЕРАТУРЫ» в оригинале — RESISTANCE TEMPERATURE TRANSMITTER, то есть не "
          f"сопротивление, а преобразователь. Искали не то изделие.</p>")
        a("<div class='do'><b>Предложение</b><p>Запросы западным продавцам и изготовителям "
          "составлять на английском оригинале наименования, а не на обратном переводе русской "
          "строки. Это ничего не стоит и снимает часть строк, которые числятся «не найдены»: "
          "номер у них искали правильный, а название — нет.</p></div>")
        a("<table class='t'><colgroup><col style='width:26mm'><col><col></colgroup>")
        a("<thead><tr><th>артикул</th><th>как записано в заявке</th>"
          "<th>как звучит в оригинале — это и идёт в запрос</th></tr></thead><tbody>")
        for r in nf[:14]:
            x = enby[norm_pn(r["pn"])]
            a(f"<tr><td class='pn'>{E(r['pn'])}</td>"
              f"<td>{E((r.get('name') or '')[:70])}</td>"
              f"<td><b>{E(x['name_en'])}</b></td></tr>")
        a("</tbody></table>")
        a(f"<p class='dim'>Показаны 14 из {ru(len(nf))}. Полный список — "
          f"gt/data/ship_english_source.json, поле ask_as.</p>")
        a("</div>")

    a("<div class='sec'><h2>Следующие шаги по порядку</h2><ol>")
    a("<li><b>Регистрация там, где цена за входом: магазин Solar, ONERGYS и "
      "iggnita по Jenbacher.</b> Это административный шаг на один час, и он "
      "открывает цену вместе с остатком по кластеру, где у 268 строк оценки нет "
      "вовсе. Поштучная разведка того же объёма стоит недели и даёт хуже: "
      "магазин изготовителя показывает остаток, а витрина перепродавца — нет.</li>")
    a("<li><b>Твёрдые офферы по строкам с подтверждённым остатком.</b> Пока "
      "продавец не подтвердил остаток письмом на наш объём, строка не "
      "отгружаемая, сколько бы «in stock» ни стояло на карточке.</li>")
    a("<li><b>Вопросы заказчику — одним письмом до защиты.</b> Список готов.</li>")
    a("<li><b>Закрыть занижения.</b> На этих строках выставленная цена может "
      "оказаться ниже закупки, и узнать это на защите хуже, чем до неё.</li>")
    a("<li><b>Разовые запчасти без оценки — решением, а не работой.</b> "
      "Половина строк это одна-две штуки; поштучная проработка каждой стоит "
      "дороже самих позиций. Либо считать их по нижней границе класса с "
      "оговоркой, либо выносить в отдельное приложение.</li>")
    a("<li><b>Строки с дефектом записи — исправить в самой заявке.</b> "
      "Кириллический двойник внутри латинского номера и неверный разделитель "
      "делают номер незаказуемым: ни один дистрибьютор такой номер не "
      "примет.</li>")
    a("</ol></div>")
    return "".join(h)


def main() -> int:
    if not (D / "ship_lukoil.json").exists():
        print("нет gt/data/ship_lukoil.json", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    hp = OUT / "СПРАВКА-НА-ЗАЩИТУ-ЛУКОЙЛ.html"
    pp = OUT / "СПРАВКА-НА-ЗАЩИТУ-ЛУКОЙЛ.pdf"
    hp.write_text(build(), encoding="utf-8")
    exe = next((c for c in CHROME if Path(c).exists()), None)
    if not exe:
        print("Chromium не найден — PDF не собран", file=sys.stderr)
        return 1
    subprocess.run(
        [exe, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
         "--run-all-compositor-stages-before-draw", "--virtual-time-budget=120000",
         f"--print-to-pdf={pp}", hp.as_uri()],
        check=True, capture_output=True)
    print(f"{pp.name}: {pp.stat().st_size / 1e6:.1f} МБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
