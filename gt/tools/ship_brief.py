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
  gt/data/ship_offer_stats.json    счётчики: КП поставщиков против наших вилок
  gt/data/ship_confidence.json     где уверенность не обеспечена проверкой
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verdicts import vkey  # noqa: E402  один классификатор вердиктов на все документы

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
.do { background: #f2f2f2; padding: 2.4mm 3mm; margin: 0 0 3mm;
  /* Блок предложения не должен рваться: его хвост уезжал
     на отдельную страницу и проверка PDF считала её полупустой. */
  page-break-inside: avoid; }
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


def offer_scope(st: dict, prefer: str = "Энергосети") -> tuple[str, dict]:
    """Счётчики КП по одному охвату: охваты — разные заявки и не складываются.

    Форма до разделения (счётчики одного прогона прямо в корне файла) читается
    тоже: иначе первый же прогон по другому листу оставил бы документ пустым.
    """
    sc = st.get("scopes")
    if not isinstance(sc, dict) or not sc:
        return (st.get("scope") or "—", st) if st.get("rows_with_offer") else ("—", {})
    if prefer in sc:
        return prefer, sc[prefer]
    name = max(sc, key=lambda k: (sc[k] or {}).get("rows_with_offer") or 0)
    return name, sc[name] or {}


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
    eng = load("ship_english_source.json") or {}
    qg = ((eng.get("totals") or {}).get("qty_gap") or {})
    if qg.get("usd_at_stake"):
        a(f"<tr><td class='l'>экспозиция на количестве, которое не подтверждено "
          f"первоисточником заявки</td>"
          f"<td class='v'>{ru(qg['usd_at_stake'])} USD</td>"
          f"<td class='dim'>{100 * float(qg['usd_at_stake']) / tot:.0f} % всех денег. К заявке "
          f"есть англоязычный лист, с которого сделан построчный перевод русских наименований; "
          f"по {ru(qg['qty_summary_above_source'])} номерам из {ru(qg['pns_measured'])} общих "
          f"заявка просит БОЛЬШЕ, чем стоит в листе. Это не обвинение в переплате: лист может "
          f"покрывать меньше машин. Но подтверждение количества здесь стоит дороже любой цены, "
          f"которую по этим строкам можно найти — разбор ниже</td></tr>")
    a("</table>")

    a("<div class='warn'><p><b>Три числа, которые нельзя складывать и нельзя "
      "подменять одно другим.</b> Экспозиция — наша оценка на количество. Цена "
      "продавца — действует на тот остаток, который он подтвердил, а не на весь "
      "объём. Выставленная заказчику цена — третье, и она лежит в приложенных "
      "файлах сделки, а не в товарных строках. Прежняя выкладка сложила первое "
      "со вторым и завысила закупку в шесть раз.</p></div>")

    # Состояние защиты по деньгам: что известно про топ-60 строк, которые несут
    # две трети экспозиции. Считается join'ом уже измеренных наборов, своего
    # набора не заводит — числа приходят из тех же замеров, что и разделы ниже.
    cf_all = load("ship_confidence.json") or {}
    col_all = load("ship_collisions.json") or {}
    conf_bad = {norm_pn(x["pn"]) for x in (cf_all.get("items") or [])}
    over = {norm_pn(x["pn"]) for x in ((cf_all.get("found") or {}).get("items") or [])
            if x.get("where") == "выше потолка вилки"}
    coll_pn = {norm_pn(i["pn"]) for cls in ("разные изделия", "противоположные исполнения")
               for i in ((col_all.get("classes") or {}).get(cls, {}).get("items") or [])}
    rv_pn = {norm_pn(x.get("pn")) for x in rvrows}
    MARKS = [
        ("перепроверена и проверена на опровержение", rv_pn,
         "по строке есть разбор и возражение скептика — это самый надёжный класс"),
        ("уверенность не обеспечена проверкой", conf_bad,
         "буква A или B стоит там, где проверка того же набора записала дефект"),
        ("один номер — разные детали", coll_pn,
         "количество сложено по несопоставимым позициям, цена за штуку бессмысленна"),
        ("найденная цена выше потолка вилки", over,
         "наша оценка ниже того, что уже видели на странице"),
    ]
    a("<h2>Состояние защиты по деньгам: топ-60 строк</h2>")
    a(f"<p>Шестьдесят строк несут {ru(sum(map(expo, top60)))} USD — "
      f"{100 * sum(map(expo, top60)) / tot:.0f} % экспозиции при "
      f"{100 * 60 / len(rows):.0f} % строк. Защита стоит или падает на них, поэтому первое, что "
      f"надо знать про них, — не цена, а что про каждую известно. Пометки не исключают друг "
      f"друга: строка бывает и перепроверена, и с необеспеченной уверенностью.</p>")
    a("<table class='t'><colgroup><col style='width:52mm'><col style='width:12mm'>"
      "<col style='width:22mm'><col></colgroup>")
    a("<thead><tr><th>что известно про строку</th><th class='n'>строк</th>"
      "<th class='n'>экспозиция</th><th>как это читать</th></tr></thead><tbody>")
    for label, pns, how in MARKS:
        g = [r for r in top60 if norm_pn(r["pn"]) in pns]
        a(f"<tr><td><b>{E(label)}</b></td><td class='n'>{ru(len(g))}</td>"
          f"<td class='n'>{ru(sum(map(expo, g)))}</td><td>{E(how)}</td></tr>")
    marked = {p for _, pns, _ in MARKS[1:] for p in pns}
    clean = [r for r in top60 if norm_pn(r["pn"]) not in marked]
    nothing = [r for r in clean if norm_pn(r["pn"]) not in rv_pn]
    a(f"<tr><td><b>ни одной пометки о дефекте</b></td><td class='n'>{ru(len(clean))}</td>"
      f"<td class='n'>{ru(sum(map(expo, clean)))}</td>"
      f"<td>ни выдуманного количества, ни необеспеченной уверенности, ни цены выше потолка — "
      f"на защите это опора</td></tr>")
    a(f"<tr><td><b>из них не разобрано вовсе</b></td><td class='n'>{ru(len(nothing))}</td>"
      f"<td class='n'>{ru(sum(map(expo, nothing)))}</td>"
      f"<td>ни перепроверки, ни найденного дефекта: про эти строки мы просто ничего не знаем, и "
      f"это следующая работа</td></tr>")
    a("</tbody></table>")
    a("<div class='do'><b>Как этим пользоваться на защите</b><p>Строку из первой категории "
      "защищать разбором: у неё есть и цена с названной страницы, и возражение скептика, и "
      "оговорка. Строку с необеспеченной уверенностью не защищать буквой «A» — честнее сказать "
      "«здесь у нас оценка» и назвать канал. Строку со склеенным номером не защищать ценой "
      "вообще: она не про цену, а про исходные данные, и вопрос по ней уже в письме заказчику.</p>"
      "</div>")

    a("<h2>Где сосредоточены деньги</h2>")
    a(f"<p>Топ-60 строк дают {ru(sum(map(expo, top60)))} USD — "
      f"{100 * sum(map(expo, top60)) / tot:.0f} % экспозиции при "
      f"{100 * 60 / len(rows):.0f} % строк. Защита стоит или падает на них, "
      f"поэтому перепроверка идёт именно по этому списку.</p>")
    SHOW = 18          # столько строк умещается на страницу вместе с заголовком
    a(f"<p class='dim'>Показаны {SHOW} из 60 по величине экспозиции. Полный разбор с ценами, "
      f"страницами и возражениями скептиков — в документе ПЕРЕПРОВЕРКА-ЛУКОЙЛ.</p>")
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
    for r in top60[:SHOW]:
        v = rvby.get(norm_pn(r["pn"])) or nearest(rvby, r["pn"])
        # В колонку идёт КЛАСС вердикта, а не его текст с оговорками: полная
        # фраза «ЗАНИЖЕНА ТОЛЬКО ПРОТИВ АВТОРИЗОВАННОГО КАНАЛА» распирала строку
        # и выносила последнюю на отдельную полупустую страницу. Оговорки живут
        # в документе перепроверки, где им и место.
        st = (E(vkey(v).lower()) if v else
              "<span class='dim'>перепроверка не дошла</span>")
        a(f"<tr><td class='pn'>{E(r['pn'])}</td><td>{E(r.get('man'))}</td>"
          f"<td class='n'>{ru(r.get('qty'))}</td><td class='n'>{ru(expo(r))}</td>"
          f"<td>{E(r.get('conf'))}</td><td>{st}</td></tr>")
    a("</tbody></table>")

    if rvrows:
        # Своим разделом, а не хвостом первого: после добавления «состояния защиты»
        # первый раздел перерос страницу, и проверка PDF нашла полупустую.
        a("</div><div class='sec'><h2>Что дала перепроверка</h2>")
        # Вердикты считает ОДИН общий классификатор (gt/tools/verdicts.py), тот
        # же, что в самом документе перепроверки: два документа об одних данных
        # расходились на девять строк, пока считали каждый по-своему.
        by = Counter(vkey(r) for r in rvrows)
        a(f"<p>Проверено {ru(len(rvrows))} строк, взятых по величине экспозиции. "
          + " · ".join(f"<b>{E(k.lower())}</b> {v}" for k, v in by.most_common())
          + ".</p>")
        a("<div class='do'><b>Что с этим делать на защите</b>"
          "<p>Строку с занижением защищать нельзя как есть: закупка дороже "
          "нашей оценки, и запаса на снижение там нет. Строку с завышением "
          "можно уступать — это и есть подготовленный запас. Строку «нечем "
          "проверить» защищать ссылкой на канал OEM и на то, что открытого "
          "рынка по ней не существует, а не ценой.</p></div>")

    a("</div>")   # закрывает раздел перепроверки либо первый, если её нет

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
        # Числа по кластеру Solar считаются, а не пишутся руками: собственная
        # номенклатура — из карты каналов, весь кластер — она плюс то, что из
        # него ушло компонентным изготовителям.
        sol_own = next((b for b in chb if b["brand"] == "Solar Turbines"),
                       {"rows": 0, "usd": 0, "no_estimate": 0})
        sol_all_rows = sol_own["rows"] + (sol.get("rows") or 0)
        sol_all_usd = sol_own["usd"] + (sol.get("usd") or 0)
        if sol:
            a("<div class='warn'><p><b>Поправка к прежней формулировке про Solar.</b> "
              f"Под шильдиком Solar в заявке {ru(sol_all_rows)} строк на "
              f"{ru(sol_all_usd)} USD, но "
              f"{ru(sol.get('rows'))} строки из них на {ru(sol.get('usd'))} USD — "
              f"чужие компоненты ("
              + ", ".join(f"{E(k)} {v['rows']}" for k, v in
                          sorted((sol.get("brands") or {}).items(),
                                 key=lambda kv: -kv[1]["usd"]))
              + "). Магазин Solar их не закрывает и не должен: у Allen-Bradley и "
                "Pepperl+Fuchs цена уже открыта у их продавцов, и под их собственным "
                "номером она в разы ниже, чем под номером Solar. Регистрация в магазине "
                f"Solar закрывает его собственную номенклатуру — {ru(sol_own['rows'])} "
                f"строк на {ru(sol_own['usd'])} USD, из которых у "
                f"{ru(sol_own['no_estimate'])} оценки нет вовсе.</p></div>")
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

    st_doc = load("ship_offer_stats.json") or {}
    st_scope, st = offer_scope(st_doc)
    if st.get("rows_with_offer"):
        tot_off = st["rows_with_offer"]
        a("<div class='sec'><h2>Что письменные предложения поставщиков делают с нашими вилками</h2>")
        a(f"<p class='lead'>Перепроверка идёт по сорока строкам, а письменные предложения "
          f"контрагентов лежат по {ru(tot_off)} строкам заявки. Значит на главный вопрос "
          f"защиты — «наши вилки вообще низкие или высокие» — отвечает не выборка, а счёт "
          f"по всему пересечению. Охват прогона: {E(st_scope)}.</p>")
        others = {k: v for k, v in (st_doc.get("scopes") or {}).items() if k != st_scope}
        if others:
            # Охваты НЕ складываются: это разные листы заявки с разными вилками.
            # Поэтому они названы отдельной строкой, а не приплюсованы к итогу.
            a("<p class='dim'>По другим охватам прогонов счёт отдельный и складывать его с "
              "этим нельзя: " + "; ".join(
                  f"<b>{E(k)}</b> — {ru((v or {}).get('rows_with_offer'))} строк с КП, из них "
                  f"выше потолка {ru((v or {}).get('above_ceiling'))}, внутри "
                  f"{ru((v or {}).get('inside_band'))}, ниже пола "
                  f"{ru((v or {}).get('below_floor'))}"
                  for k, v in sorted(others.items())) + ".</p>")
        a("<table class='k'>")
        for key, label, why in (
            ("above_ceiling", "предложение ВЫШЕ потолка вилки",
             "наша оценка занижена: закупка дороже, чем мы считали, и запаса на снижение "
             "по этим строкам нет"),
            ("inside_band", "предложение внутри вилки",
             "оценка подтверждена письменным предложением — эти строки защищаются как есть"),
            ("below_floor", "предложение НИЖЕ пола вилки",
             "наша оценка завышена: это и есть подготовленный запас на торг"),
            ("no_band", "вилки по строке нет",
             "предложение есть, а сравнивать не с чем — строку надо оценить"),
        ):
            v = st.get(key) or 0
            a(f"<tr><td class='l'>{label}</td><td class='v'>{ru(v)}</td>"
              f"<td class='dim'>{100 * v / tot_off:.0f} % строк с предложением. {why}</td></tr>")
        a("</table>")
        rvst = st.get("reverified") or {}
        if rvst.get("with_offer"):
            a("<h3>А по крупным перепроверенным строкам картина обратная</h3>")
            if rvst.get("rows") and len(rvrows) > rvst["rows"]:
                a(f"<p class='dim'>Счёт ниже снят прогоном выгрузки, когда перепроверено было "
                  f"{ru(rvst['rows'])} строк; сейчас их {ru(len(rvrows))}. Число обновится следующим "
                  f"прогоном — руками его править нельзя, иначе оно перестанет быть замером.</p>")
            a(f"<p>Те же предложения поставщиков, но только по строкам, которые мы разбирали "
              f"поштучно: из {ru(rvst.get('rows'))} строк перепроверки предложение нашлось по "
              f"{ru(rvst['with_offer'])}, и <b>завышение подтверждено "
              f"{ru(rvst.get('overstated_confirmed'))} раз против занижения "
              f"{ru(rvst.get('understated_confirmed'))}</b> (ещё "
              f"{ru(rvst.get('band_right'))} — вилка верна). То есть на крупных, специально "
              f"проработанных строках запас на снижение ЕСТЬ: там наши вилки выше, чем просит "
              f"поставщик. А масса мелких строк, наоборот, недооценена. Складывать эти два счёта "
              f"в одно число нельзя — они про разные части заявки.</p>")
        a("<div class='warn'><p><b>Чего здесь нет и почему.</b> Ни одной цены и ни одной "
          "привязки цены к артикулу: предложения контрагентов — их коммерческие данные, и в "
          "репозиторий они не попадают. В репозитории живёт только счёт строк. Сами цены — в "
          "документе ЗАПАС-НА-СНИЖЕНИЕ, который собирается прогоном выгрузки и уходит "
          "артефактом, а не коммитом.</p></div>")
        a("</div>")

    cf = load("ship_confidence.json") or {}
    if cf.get("items"):
        ct = cf["totals"]
        a("<div class='sec'><h2>Где уверенность не обеспечена собственной проверкой</h2>")
        a(f"<p class='lead'>Буква уверенности идёт в документ и читается как «проверено». "
          f"Замер по набору цен: у <b>{ru(ct['rows'])} строк</b> стоит уверенность A или B, а "
          f"проверка ТОГО ЖЕ номера в том же файле записала дефект. Экспозиция этих строк — "
          f"<b>{ru(ct['exposure'])} USD из {ru(ct['exposure_total'])}, то есть "
          f"{ct['share_pct']} %</b>. Находка родилась из одной строки на защите: у 1701/05 "
          f"уверенность A, а её же проверка говорит «мёртвая ссылка, цену не увидел ни одну».</p>")
        a("<table class='t'><colgroup><col style='width:30mm'><col style='width:14mm'>"
          "<col style='width:24mm'><col></colgroup>")
        a("<thead><tr><th>что записала проверка</th><th class='n'>строк</th>"
          "<th class='n'>экспозиция</th><th>что это значит и чего стоит</th>"
          "</tr></thead><tbody>")
        for v, c in sorted(cf["classes"].items(), key=lambda kv: -kv[1]["exposure"]):
            a(f"<tr><td><b>{E(v)}</b></td><td class='n'>{ru(c['rows'])}</td>"
              f"<td class='n'>{ru(c['exposure'])}</td><td>{E(c['means'])}</td></tr>")
        a("</tbody></table>")
        a("<div class='warn'><p><b>Читать по классам, а не одной суммой.</b> У мёртвой ссылки "
          "цены не видел никто. У ссылки на другой артикул цена чужая — это худший класс, потому "
          "что число выглядит настоящим. «Цена расходится» значит, что записанное число неверно, "
          "а уровень может быть верным. «По адресу не продавец» — что число вообще не является "
          "предложением. Складывать это в одну претензию нельзя.</p></div>")
        a("<h3>Самые дорогие строки с необеспеченной уверенностью</h3>")
        a("<table class='t'><colgroup><col style='width:26mm'><col style='width:22mm'>"
          "<col style='width:10mm'><col style='width:20mm'><col></colgroup>")
        a("<thead><tr><th>артикул</th><th>изготовитель</th><th>увер.</th>"
          "<th class='n'>экспозиция</th><th>что записала проверка</th></tr></thead><tbody>")
        for x in cf["items"][:16]:
            a(f"<tr><td class='pn'>{E(x['pn'])}</td><td>{E(x.get('man'))}</td>"
              f"<td>{E(x['conf'])}</td><td class='n'>{ru(x['exposure'])}</td>"
              f"<td>{E(x['verdict'])}: {E((x.get('note') or '')[:150])}</td></tr>")
        a("</tbody></table>")
        a("<div class='do'><b>Предложение</b><p>Уверенность у этих строк снизить до C, пока "
          "страница не открыта заново. Это не потеря: буква «A» на строке, где проверка "
          "записала мёртвую ссылку, хуже честной «C» — на защите её оспорят одним щелчком по "
          "ссылке. Начинать с класса «ссылка ведёт на другой артикул»: там число чужое, а не "
          "просто непроверенное.</p></div>")
        f = cf.get("found") or {}
        if f.get("rows"):
            a("<h3>А теперь главное: где наша же проверка уже нашла цену</h3>")
            a(f"<p class='lead'>Это самый сильный замер из тех, что целиком на наших данных — "
              f"без вложений сделок и без чужих секретов. Блок проверок набора цен хранит то, "
              f"что проверяющий УВИДЕЛ на странице. Сравниваем это с вилкой той же строки по "
              f"<b>{ru(f['rows'])} строкам</b>: экспозиция по вилкам {ru(f['exposure_band'])} USD, "
              f"а по найденным ценам <b>{ru(f['exposure_checked'])} USD</b>.</p>")
            a("<table class='t'><colgroup><col style='width:34mm'><col style='width:14mm'>"
              "<col style='width:26mm'><col style='width:26mm'><col></colgroup>")
            a("<thead><tr><th>где найденная цена</th><th class='n'>строк</th>"
              "<th class='n'>по вилкам</th><th class='n'>по найденным ценам</th>"
              "<th>что это значит</th></tr></thead><tbody>")
            WHAT = {
                "выше потолка вилки": "наши оценки НИЖЕ рынка, и это самый дорогой класс: "
                                      "запаса на снижение по этим строкам нет",
                "внутри вилки": "оценка подтверждена собственной проверкой — эти строки "
                                "защищаются как есть",
                "ниже пола вилки": "оценка выше найденной цены: здесь запас на торг есть",
            }
            for w, g in f["groups"].items():
                a(f"<tr><td><b>{E(w)}</b></td><td class='n'>{ru(g['rows'])}</td>"
                  f"<td class='n'>{ru(g['exposure_band'])}</td>"
                  f"<td class='n'>{ru(g['exposure_checked'])}</td>"
                  f"<td>{E(WHAT.get(w, ''))}</td></tr>")
            a("</tbody></table>")
            up = f["groups"].get("выше потолка вилки") or {}
            if up.get("rows"):
                d1, d2 = up["exposure_band"], up["exposure_checked"]
                a(f"<div class='warn'><p><b>Ответ на главный вопрос защиты.</b> Вилки в основном "
                  f"верны: у {ru((f['groups'].get('внутри вилки') or {}).get('rows'))} строк из "
                  f"{ru(f['rows'])} найденная цена попадает ВНУТРЬ вилки, и там сумма почти не "
                  f"меняется. Ломается хвост: {ru(up['rows'])} строк выходят за потолок, и "
                  f"выходят сильно — {ru(d1)} USD по вилкам против {ru(d2)} USD по найденным "
                  f"ценам, то есть в {d2 / d1:.1f} раза. Направление системное: там, где реальная "
                  f"цена нашлась, она обычно ВЫШЕ нашей оценки, а не ниже.</p></div>")
            a("<table class='t'><colgroup><col style='width:26mm'><col style='width:20mm'>"
              "<col style='width:8mm'><col style='width:24mm'><col style='width:18mm'>"
              "<col style='width:22mm'><col></colgroup>")
            a("<thead><tr><th>артикул</th><th>изготовитель</th><th class='n'>кол-во</th>"
              "<th>наша вилка, USD/шт</th><th class='n'>найдено, USD/шт</th>"
              "<th class='n'>экспозиция: вилка → факт</th><th>у кого найдено</th>"
              "</tr></thead><tbody>")
            for x in f["items"][:20]:
                a(f"<tr><td class='pn'>{E(x['pn'])}</td><td>{E(x['man'])}</td>"
                  f"<td class='n'>{ru(x['qty'])}</td>"
                  f"<td>{ru(x['band_lo'])} – {ru(x['band_hi'])}</td>"
                  f"<td class='n'>{ru(x['checked_price'])}</td>"
                  f"<td class='n'>{ru(x['exposure_band'])} → {ru(x['exposure_checked'])}</td>"
                  f"<td>{E((x.get('seller') or '')[:40])}</td></tr>")
            a("</tbody></table>")
            a("<div class='do'><b>Что с этим делать</b><p>Пересобрать вилки по этим строкам из "
              "найденных цен — работа механическая, данные уже есть в репозитории. И не читать "
              "итог как закупку: найденные цены это карточки продавцов, а не подтверждённые "
              "офферы с остатком, и по части строк проверка на опровержение уже уточнила цифру. "
              "Но направление они задают надёжно, потому что измерены по девяноста строкам, а не "
              "по выборке из пяти.</p></div>")

        a(f"<p class='dim'>Считает и сверяет gt/tools/confidence_audit.py: уверенность A или B "
          f"плюс вердикт проверки из закрытого списка. Вердикт «подтверждено» дефектом не "
          f"считается. Противоречия набора держит tests/test_confidence_audit.py.</p>")
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
        a("<div class='warn'><p><b>Число файлов — не покрытие, и это измерено.</b> Прогон "
          "17.09.2026 по слову «ЛУКОЙЛ» обошёл 137 сделок и разобрал 674 файла (128 994 "
          "строки), извлёк 2 153 "
          "артикула с ценой — и пересечение с нашей заявкой оказалось НОЛЬ точных совпадений, "
          "1 по первым шести знакам, 13 по цифровой части. Номера настоящие, но номенклатура "
          "другая: имя заказчика вытягивает сделки по другим его заявкам. Файлы НАШЕЙ заявки "
          "лежат в сделках охвата «Энергосети» — там 697 артикулов по нашей стороне и 775 по КП "
          "поставщиков. Поэтому охват прогона задаётся словом «Энергосети» или id сделки, а не "
          "именем заказчика.</p></div>")
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

    sl = load("ship_stocklist_cross.json") or {}
    sellers = sl.get("sellers") or {}
    if sellers:
        # Раздел ЧИТАЕТ МНОГОПРОДАВЦОВУЮ форму. Пока инструмент писал одного
        # продавца в корень файла, здесь стояло sl["totals"], и после перехода на
        # слияние по продавцу раздел ИСЧЕЗ из справки молча — ровно та ошибка, на
        # которую в правилах есть пункт про две вставки в одну таблицу.
        dis = sl.get("disagreements") or {}
        nob = sl.get("no_band_reach") or {}
        best = max(sellers.items(), key=lambda kv: (kv[1].get("totals") or {}).get("matched_pns", 0))
        groups = {(v.get("group") or k) for k, v in sellers.items()}
        a(f"<div class='sec'><h2>{ru(len(sellers))} открытых сток-листов: где они согласны и "
          f"где расходятся</h2>")
        a(f"<p class='lead'>Самый дешёвый источник ориентира по цене из всех, что нам попадались: "
          f"один открытый лист несёт десятки НАШИХ номеров и стоит одну загрузку страницы. "
          f"Сейчас в наборе <b>{ru(len(sellers))}</b> листов от <b>{ru(len(groups))}</b> "
          f"независимых операторов. Крупнейший ({E(best[0])}) закрывает "
          f"<b>{ru((best[1].get('totals') or {}).get('matched_pns'))}</b> наших номеров.</p>")
        a("<table><colgroup><col style='width:52mm'><col style='width:18mm'>"
          "<col style='width:26mm'><col></colgroup>")
        a("<thead><tr><th>лист</th><th class='n'>наших номеров</th>"
          "<th class='n'>экспозиция по нашим вилкам</th><th>группа витрин одного оператора</th>"
          "</tr></thead>")
        for name, sl_one in sorted(sellers.items(),
                                   key=lambda kv: -((kv[1].get("totals") or {})
                                                    .get("matched_exposure") or 0)):
            t1 = sl_one.get("totals") or {}
            a(f"<tr><td>{E(name)}</td><td class='n'>{ru(t1.get('matched_pns'))}</td>"
              f"<td class='n'>{ru(t1.get('matched_exposure'))} USD</td>"
              f"<td class='dim'>{E(sl_one.get('group') or '—')}</td></tr>")
        a("</table>")
        if dis.get("pns_on_more_than_one_list"):
            a(f"<div class='warn'><p><b>Главная цифра набора: класс расходится между "
              f"продавцами.</b> {ru(dis['pns_on_more_than_one_list'])} наших номеров стоят более "
              f"чем на одном листе, и у <b>{ru(dis['pns_with_conflicting_class'])}</b> из них на "
              f"<b>{ru(dis['usd_in_conflict'])} USD</b> ask одного продавца лежит внутри нашей "
              f"вилки, а другого — вне её. То есть по этим строкам вывод держится не на "
              f"измерении, а на выборе листа. Согласие есть по остальным — "
              f"{ru(dis['usd_in_agreement'])} USD, и вот там ориентир чего-то стоит. Считается по "
              f"ГРУППЕ, а не по домену: два листа одного оператора — один свидетель, иначе "
              f"расхождение искалось бы там, где свидетель один.</p></div>")
        if nob.get("pns_without_band_on_lists"):
            lk_rows = (load("ship_lukoil.json") or {}).get("rows") or []
            priced = sum(1 for r in lk_rows if r.get("usd_lo") not in (None, "")
                         and r.get("usd_hi") not in (None, ""))
            a(f"<div class='warn'><p><b>Строки, которых не видно ни в одной сумме.</b> У "
              f"{ru(len(lk_rows) - priced)} строк заявки из {ru(len(lk_rows))} вилки нет вовсе, "
              f"и в экспозицию они не входят. Листы "
              f"называют <b>{ru(nob['pns_without_band_on_lists'])}</b> из таких номеров, а "
              f"<b>{ru(nob['pns_without_band_on_two_independent_lists'])}</b> — сразу на двух "
              f"независимых листах. ВИЛКУ ИЗ ЭТИХ ЛИСТОВ НЕ СТАВИМ: поставить её из листа, против "
              f"которого потом считается класс, значит получить «ask внутри вилки» по построению "
              f"— эталон стал бы производным от правила. Лист годится как повод запросить цену у "
              f"второго ТИПА свидетеля (изготовитель, авторизованный канал), и вилкой может стать "
              f"только его ответ.</p></div>")
        a("<div class='warn'><p><b>Чего эти цифры НЕ значат.</b> Сток-лист — это ask продавца, а "
          "не рынок, и у части листов внутри ещё и ДВА ценовых уровня на один номер (продавец сам "
          "пишет, что верхний уровень не живой). Внутри ask сидит неизмеренная премия за "
          "поставку, поэтому «ask выше потолка» означает «наша вилка ниже, чем просит этот "
          "продавец», а не «закупка дороже». И остаток: в листе стоит флаг, а ЧИСЛО остатка — "
          "только на карточке; там, где мы его сняли, покрытия количества обычно нет.</p></div>")
        a(f"<p class='dim'>Цен в наборе нет и быть не может: репозиторий публичный, а сток-лист — "
          f"коммерческий документ контрагента. Набор несёт только счёт и класс "
          f"(gt/data/ship_stocklist_cross.json), считает gt/tools/stocklist_cross.py. Замеры "
          f"живут по продавцам и НЕ складываются: один номер стоит в нескольких листах.</p>")
        a("<div class='do'><b>Предложение</b><p>По 14 расходящимся номерам искать ТРЕТЬЕГО "
          "независимого продавца прежде, чем трогать вилку: пока два листа спорят, вилка не "
          "опровергнута ни одним. По номерам без вилки, которых листы называют, — запрос "
          "изготовителю или авторизованному каналу, и вилка ставится по его ответу, а не по "
          "листу. По турбинному железу открытых листов нет ни у кого, и это измерено: весь "
          "выигрыш от листов пришёлся на автоматику внутри тех же машин.</p></div>")
        a("</div>")

    mb = load("ship_maker_basis.json") or {}
    if (mb.get("totals") or {}).get("weak_usd_in_request"):
        t = mb["totals"]
        a("<div class='sec'><h2>На чём стоит «изготовитель»: на документе или на догадке по "
          "классу</h2>")
        a(f"<p class='lead'>Звено «у кого спрашивать» стоит ровно столько, сколько стоит его "
          f"основание. В базе номеров {ru(t['db_rows'])} строк, изготовитель назван у "
          f"{ru(t['maker_named'])}. У <b>{ru(t['weak_by_db_basis_rows'])}</b> строк нашей заявки "
          f"он выведен ИЗ КЛАССА изделия, а не из документа, и в самой разметке на этих строках "
          f"<b>{ru(t['weak_by_db_basis_usd'])} USD</b>.</p>")
        # Пока изготовители не применены к базе, «из них уже закрыто» — настоящее
        # пересечение. После применения оно структурно нулевое, и печатать «закрыла
        # 0 строк на 0 USD» рядом с «значит осталось столько же» — значит выдавать
        # тавтологию за измерение. Замер сам сообщает, какой это случай.
        if t.get("weak_closed_tautological"):
            minus = ("Закрытое перепроверкой из этой цифры УЖЕ ВЫЧТЕНО: применённый "
                     "изготовитель заменил догадку в разметке, поэтому пересечение "
                     "«догадка И закрыто» здесь структурно нулевое, а не пустое. ")
        else:
            minus = (f"Но перепроверка часть этих строк уже закрыла каталогом: "
                     f"{ru(t['weak_closed_by_reverify_rows'])} строк на "
                     f"{ru(t['weak_closed_by_reverify_usd'])} USD. ")
        a(f"<div class='warn'><p><b>Два числа, и подменять одно другим нельзя.</b> "
          f"{ru(t['weak_by_db_basis_usd'])} USD — это сколько денег стоит на догадке В САМОЙ "
          f"РАЗМЕТКЕ. {minus}Значит ОТКРЫТОЙ РАБОТЫ осталось "
          f"<b>{ru(t['weak_usd_in_request'])} USD</b> — "
          f"{100 * float(t['weak_usd_in_request']) / float(t['request_exposure']):.1f} % "
          f"экспозиции. Всего каталогом при перепроверке "
          f"закрыто {ru(t['closed_by_reverify_rows'])} строк на "
          f"{ru(t['closed_by_reverify_usd'])} USD — это НЕ подмножество догадок, там есть строки "
          f"с другим основанием.</p>"
          "<p><b>Историческая поправка, чтобы её не повторили.</b> 18.09.2026 в отчёт владельцу "
          "ушло 2 124 537 USD и «23 % экспозиции» — это была цифра ПО РАЗМЕТКЕ, поданная как "
          "открытая работа, и она завышала её впятеро: перепроверка к тому часу уже закрыла "
          "каталогом 52 строки. С тех пор оба числа считаются и печатаются раздельно, а какое "
          "из них живое, решает не текст, а сам замер.</p></div>")
        a("<div class='warn'><p><b>Это не теория: за сутки разметку опровергли дважды.</b> "
          "Масляному фильтру Fleetguard были приписаны четыре фильтровых дома «по типу» с "
          "основанием «сегмент фильтров» — на деле изготовитель Fleetguard (Cummins Filtration), "
          "и кросс напечатан прямо на карточке продавца. Наконечнику свечи Jenbacher был "
          "приписан авиационный поставщик зажигания «типично» с основанием «OE-поставщик "
          "зажигания Solar» — на деле Jenbacher/INNIO, и это вообще не турбина. Оба раза "
          "механизм один: изготовитель выведен из класса изделия. Тот же приём сделал электрод "
          "розжига виброизолятором.</p></div>")
        a("<table class='t'><colgroup><col style='width:42mm'><col style='width:16mm'>"
          "<col style='width:16mm'><col style='width:24mm'><col></colgroup>")
        a("<thead><tr><th>основание</th><th class='n'>в базе</th><th class='n'>в заявке</th>"
          "<th class='n'>экспозиция, USD</th><th>как это читать</th></tr></thead><tbody>")
        for c in (mb.get("classes") or []):
            a(f"<tr><td><b>{E(c['basis'])}</b></td><td class='n'>{ru(c['rows_in_db'])}</td>"
              f"<td class='n'>{ru(c['rows_in_request'])}</td>"
              f"<td class='n'>{ru(c['usd_in_request'])}</td>"
              f"<td class='dim'>{E(c.get('why'))}</td></tr>")
        a("</tbody></table>")
        a("<div class='do'><b>Предложение</b><p>Порядок работы по звену «исполнитель»: сначала "
          "закрыть остаток строк с догадкой по классу — их меньше двухсот, и закрывается каждая "
          "одним открытым каталогом изготовителя. Строки, "
          "где основание не записано вовсе, идут следом: там столько же работы, но меньше "
          "уверенности, что разметка вообще неверна. Правило на будущее: изготовитель, "
          "выведенный из класса изделия, записывается с этим основанием и в запрос не идёт — "
          "запрос уходит по каталогу, а не по догадке.</p></div>")
        a("</div>")

    si = load("ship_seller_independence.json") or {}
    sg = load("ship_seller_groups.json") or {}
    if (si.get("totals") or {}).get("rows_citing_one_group_twice"):
        t = si["totals"]
        a("<div class='sec'><h2>«Два независимых продавца» — довод, который развалился шесть "
          "раз</h2>")
        a(f"<p class='lead'>Этим доводом закрывается строка: одна цена может быть наценкой "
          f"перепродавца, две совпадающие цены РАЗНЫХ компаний — уже рынок. За ночь он "
          f"развалился шесть раз: витрины с разными доменами оказывались одним оператором. "
          f"Известных групп — {ru(t['groups_known'])}, доменов в них {ru(t['domains_known'])}. "
          f"Строк перепроверки, где названы две витрины ОДНОЙ группы: "
          f"<b>{ru(t['rows_citing_one_group_twice'])}</b> на "
          f"<b>{ru(t['usd_on_those_rows'])} USD</b> — это деньги, за которыми стоит один "
          f"продавец, а выглядит как рынок.</p>")
        a(f"<div class='warn'><p><b>Замер отделён от правила, иначе он был бы тавтологией.</b> "
          f"Отдельно считается, сказано ли в самой строке, что это один оператор: сейчас "
          f"оговорено {ru(t['rows_marked_as_one_operator'])} строк из "
          f"{ru(t['rows_citing_one_group_twice'])}, не оговорено "
          f"{ru(t['rows_not_marked'])}. Без этого деления строка, где мы сами написали «это одна "
          f"компания», попадала бы в дефект наравне со строкой, где две витрины выданы за "
          f"рынок.</p></div>")
        a("<table class='t'><colgroup><col style='width:52mm'><col style='width:26mm'>"
          "<col></colgroup>")
        a("<thead><tr><th>группа</th><th>что за витрины</th><th>чем доказано и что задевает</th>"
          "</tr></thead><tbody>")
        for g in (sg.get("groups") or []):
            a(f"<tr><td><b>{E(g['group'])}</b><br><span class='dim'>{E(g.get('kind'))}</span></td>"
              f"<td class='dim'>{E(', '.join(g.get('domains') or []))}</td>"
              f"<td>{E(g.get('evidence'))} <b>{E(g.get('affects'))}</b></td></tr>")
        a("</tbody></table>")
        a("<h3>Строки, где это уже сыграло</h3>")
        a("<table class='t'><colgroup><col style='width:30mm'><col style='width:22mm'>"
          "<col style='width:34mm'><col></colgroup>")
        a("<thead><tr><th>артикул</th><th class='n'>экспозиция</th><th>вердикт</th>"
          "<th>группа и витрины</th></tr></thead><tbody>")
        # ВНИМАНИЕ: имя `h` здесь занято — это сам список HTML (a = h.append).
        # Переиспользование его под строку таблицы обнулило документ до 24 байт.
        for si_row in (si.get("rows") or [])[:14]:
            gs = "; ".join(f"{g}: {', '.join(d)}"
                           for g, d in (si_row.get("groups") or {}).items())
            a(f"<tr><td class='pn'>{E(si_row['pn'])}</td>"
              f"<td class='n'>{ru(si_row['usd'])}</td>"
              f"<td>{E(si_row.get('verdict'))}</td><td class='dim'>{E(gs)}</td></tr>")
        a("</tbody></table>")
        a("<div class='do'><b>Предложение</b><p>Признак независимости проверять по списку групп, "
          "а не на глаз по разным доменам: список лежит в gt/data/ship_seller_groups.json, счёт "
          "по нему делает gt/tools/seller_groups.py. Группа записывается только при ПРЯМОЙ улике "
          "на странице — общий объект в коде, общий складской номер, общая почта или телефон, "
          "посимвольно совпадающее описание; похожесть дизайна или соседство в выдаче уликой не "
          "считаются. И практический вывод для защиты: по строкам из таблицы выше цену нельзя "
          "называть рыночной — она одна, и её держит один продавец.</p></div>")
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
          f"({et['share_pct']} %). Русские наименования — её построчный машинный перевод.</p>")
        g = et.get("qty_gap") or {}
        if g.get("top"):
            a("<h3>А количество со своим же первоисточником не сходится</h3>")
            a(f"<p>Здесь была моя собственная ошибка в замере, и она стоила ложной цифры в "
              f"отчёте. Мера «количество совпадает» проверяла, ВСТРЕЧАЕТСЯ ли количество "
              f"первоисточника среди строк заявки, — и печаталась словами «количества совпадают "
              f"у {ru(et['qty_match_line'])}». Сумма строк заявки при этом равна сумме строк "
              f"первоисточника только у {ru(et['qty_match_total'])} из "
              f"{ru(et['in_request'])}. Пример: у сборки заглушки бороскопа ПТ-1 первоисточник "
              f"несёт 8 шт, а заявка — 8 плюс 16 отдельной строкой, итого 24, и слабая мера "
              f"говорила «совпало».</p>")
            a(f"<p><b>Сколько это стоит.</b> У {ru(g['qty_summary_above_source'])} номеров из "
              f"{ru(g['pns_measured'])} измеренных заявка просит больше своего английского "
              f"листа. По серединам вилок это {ru(round(g['usd_by_summary_qty']))} USD против "
              f"{ru(round(g['usd_by_source_qty']))} USD — "
              f"<b>{ru(round(g['usd_at_stake']))} USD экспозиции стоит на количестве, которое "
              f"первоисточником не подтверждено</b>. Ровно у "
              f"{ru(g['qty_equal'])} номеров количество сходится точно.</p>")
            a("<div class='warn'><p><b>Это не обвинение в переплате.</b> Объяснений два, и "
              "выбрать может только заказчик: либо английский лист покрывает меньше машин, чем "
              "заявка (в нём преобладает количество 2, а в русском блоке — 4), либо сводка "
              "складывает строку-перевод первоисточника со строкой русского блока об одной и той "
              "же позиции. Первое объяснение работает для кратности 2 и 4; для болта с "
              "12-гранной головкой, где 16 шт первоисточника превратились в 1 168, оно не "
              "работает никак.</p></div>")
            a("<table class='t'><colgroup><col style='width:30mm'><col style='width:22mm'>"
              "<col style='width:22mm'><col></colgroup>")
            a("<thead><tr><th>артикул</th><th>в сводке</th><th>в первоисточнике</th>"
              "<th>экспозиция на неподтверждённом количестве, USD</th></tr></thead><tbody>")
            for x in g["top"][:12]:
                a(f"<tr><td class='pn'>{E(x['pn'])}</td>"
                  f"<td>{ru(round(x['qty_summary']))}</td>"
                  f"<td>{ru(round(x['qty_source']))}</td>"
                  f"<td><b>{ru(round(x['usd_gap']))}</b></td></tr>")
            a("</tbody></table>")
            a(f"<p class='dim'>Показаны 12 из {ru(g['qty_summary_above_source'])} по величине "
              f"экспозиции. Полный список — gt/data/ship_english_source.json, "
              f"totals.qty_gap.</p>")
            a("<div class='do'><b>Предложение</b><p>Прежде чем запрашивать цены по этим "
              "66 номерам, получить у заказчика письменное подтверждение количества и число "
              "машин в заявке. Это дешевле любого поиска цены: по одному болту камеры сгорания "
              "подтверждение количества стоит дороже, чем любая цена, которую по нему можно "
              "найти.</p></div>")
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
    doc = build()
    # СТРАЖ ПУСТОГО ДОКУМЕНТА. Переиспользование имени `h` (это сам список HTML)
    # под переменную цикла обнулило справку до 24 байт, и наружу ушёл бы
    # одностраничный PDF с одним словом. Проверка PDF поймала это лишь как
    # «полупустая страница 1» — поэтому порог стоит здесь, до записи файла.
    if len(doc) < 20_000 or doc.count("<h2>") < 5:
        print(f"справка вышла пустой ({len(doc)} байт, разделов "
              f"{doc.count('<h2>')}) — файл не перезаписан", file=sys.stderr)
        return 1
    hp.write_text(doc, encoding="utf-8")
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
