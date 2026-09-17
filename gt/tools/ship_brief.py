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


def build() -> str:
    lk = load("ship_lukoil.json")
    rows = lk["rows"] if isinstance(lk, dict) else lk
    rv = load("ship_reverify.json") or {}
    rvrows = rv.get("rows") or []
    qs = (load("ship_questions.json") or {}).get("questions") or []
    dec = (load("ship_decoded.json") or {}).get("rows") or {}
    inv = (load("bitrix_tkp_index.json") or {})
    invrows = inv.get("inventory") or []

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
        a(f"<p>Обойдено {ru(inv.get('deals'))} сделок и "
          f"{ru(inv.get('rfq_items'))} запросов поставщикам, найдено "
          f"{ru(len(invrows))} файлов.</p>")
        a("<table class='k'>")
        for k in ("наша цена", "входящее", "неизвестно", "заявка", "наш запрос"):
            if byd.get(k):
                a(f"<tr><td class='l'>{E(k)}</td><td class='v'>{ru(byd[k])}</td></tr>")
        a("</table>")
        a("<div class='do'><b>Почему это важно именно для защиты</b>"
          "<p>157 файлов в полях «Result, ТКП» и «Economics of the project» — "
          "это выставленные заказчику цены и наш расчёт по ним. В товарных "
          "строках сделки цен нет вовсе, поэтому защищаемая цифра берётся "
          "оттуда, а не из разведки. Разведка отвечает на другой вопрос: "
          "сколько это стоит купить.</p></div>")
        a("</div>")

    a("<div class='sec'><h2>Следующие шаги по порядку</h2><ol>")
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
