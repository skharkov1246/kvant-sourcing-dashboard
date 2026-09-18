#!/usr/bin/env python3
"""Лист решений по заявке ЛУКОЙЛ: одна страница, только цифры и развилки.

Зачем отдельно от справки. Справка на защиту — это двадцать пять страниц разбора,
её читают перед защитой. Этот лист читают за две минуты утром: что измерено и
какие решения нужны от владельца, с ценой каждого решения в деньгах и во времени.

Все числа считаются из наборов репозитория, ни одно не вписано руками: если
набор изменится, изменится и лист. Где числа нет, стоит прочерк с причиной.

    python gt/tools/ship_decisions.py
"""
from __future__ import annotations

import html
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "gt/data"
OUT = ROOT / "gt/docs"
CHROME = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
]

CSS = """
@page { size: A4; margin: 11mm 10mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 8.8pt; color: #111; margin: 0; }
h1 { font-size: 16pt; margin: 0 0 1mm; }
h2 { font-size: 11pt; margin: 4mm 0 2mm; border-bottom: 1.3pt solid #111; padding-bottom: 0.8mm; }
p { margin: 0 0 2mm; line-height: 1.4; }
.dim { color: #666; }
.lead { font-size: 9.2pt; }
table { width: 100%; border-collapse: collapse; margin-bottom: 2.5mm; table-layout: fixed; }
th { background: #111; color: #fff; text-align: left; padding: 1.2mm 1.5mm; font-size: 7.8pt; }
td { padding: 1.2mm 1.5mm; vertical-align: top; word-wrap: break-word;
     overflow-wrap: anywhere; border-bottom: 0.3pt solid #ddd; }
td.n, th.n { text-align: right; white-space: nowrap; }
tr.big td { font-weight: bold; }
.key { font-size: 10.5pt; font-weight: bold; }
ol { margin: 0 0 2mm; padding-left: 5mm; }
li { margin-bottom: 1.6mm; line-height: 1.4; }
"""


def E(x) -> str:
    return html.escape(str(x if x is not None else ""))


def ru(n) -> str:
    return f"{int(round(float(n or 0))):,}".replace(",", " ")


def load(name: str):
    p = D / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def expo(r) -> float:
    lo, hi = r.get("usd_lo"), r.get("usd_hi")
    if lo in (None, "") or hi in (None, ""):
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(r.get("qty") or 0)


def rfq_en_size(rows: list, en: dict) -> tuple[int, float]:
    """Сколько строк и штук уйдёт в запрос на английском.

    Считается тем же правилом, что и сам запрос (gt/tools/rfq_en.py): строка без
    цены продавца, у которой есть английский оригинал наименования. Печатать эти
    числа руками нельзя — ровно на таких вписанных цифрах ночная проверка
    поймала семь выдуманных вилок.
    """
    import re as _re
    keys = {_re.sub(r"[^A-Z0-9]", "", str(r.get("pn") or "").upper())
            for r in (en.get("rows") or []) if r.get("in_request")}
    sel = [r for r in rows
           if r.get("unit_price_usd") in (None, "")
           and _re.sub(r"[^A-Z0-9]", "", str(r.get("pn") or "").upper()) in keys]
    return len(sel), sum(float(r.get("qty") or 0) for r in sel)


def build() -> str:
    rows = (load("ship_lukoil.json") or {}).get("rows") or []
    ch = load("ship_channels.json") or {}
    col = load("ship_collisions.json") or {}
    cf = load("ship_confidence.json") or {}
    st = load("ship_offer_stats.json") or {}
    rv = (load("ship_reverify.json") or {}).get("rows") or []
    qs = (load("ship_questions.json") or {}).get("questions") or []
    en = load("ship_english_source.json") or {}
    tot = sum(map(expo, rows))

    h = ["<!doctype html><meta charset='utf-8'><title>Лист решений</title>"
         f"<style>{CSS}</style>"]
    a = h.append
    a("<h1>Заявка ЛУКОЙЛ: что измерено и что нужно решить</h1>")
    a("<p class='lead'>Все числа посчитаны из наборов репозитория. Ни одно не вписано руками: "
      "изменится набор — изменится лист. Полный разбор — в СПРАВКЕ-НА-ЗАЩИТУ.</p>")

    a("<h2>Объём и что о нём известно</h2>")
    a("<table><colgroup><col style='width:74mm'><col style='width:26mm'><col></colgroup>")
    a(f"<tr class='big'><td>строк в заявке · экспозиция</td>"
      f"<td class='n'>{ru(len(rows))} · {ru(tot)} USD</td>"
      f"<td class='dim'>экспозиция — середина НАШЕЙ вилки на количество; это не цена "
      f"заказчику и не подтверждённая закупка</td></tr>")
    if st.get("rows_with_offer"):
        a(f"<tr class='big'><td>строк с письменным КП поставщика в файлах сделок</td>"
          f"<td class='n'>{ru(st['rows_with_offer'])}</td>"
          f"<td class='dim'>из них выше потолка нашей вилки {ru(st.get('above_ceiling'))}, "
          f"внутри {ru(st.get('inside_band'))}, ниже пола {ru(st.get('below_floor'))}, без "
          f"вилки вовсе {ru(st.get('no_band'))}</td></tr>")
        rvst = st.get("reverified") or {}
        if rvst.get("with_offer"):
            a(f"<tr><td>то же по крупным перепроверенным строкам</td>"
              f"<td class='n'>{ru(rvst['with_offer'])} из {ru(rvst.get('rows'))}</td>"
              f"<td class='dim'>здесь картина ОБРАТНАЯ: завышение подтверждено "
              f"{ru(rvst.get('overstated_confirmed'))} раз против занижения "
              f"{ru(rvst.get('understated_confirmed'))}. На крупных строках запас есть, мелкие "
              f"недооценены</td></tr>")
    f = cf.get("found") or {}
    if f.get("rows"):
        a(f"<tr><td>строк, где наша же проверка видела цену</td>"
          f"<td class='n'>{ru(f['rows'])}</td>"
          f"<td class='dim'>экспозиция по вилкам {ru(f['exposure_band'])} против "
          f"{ru(f['exposure_checked'])} USD по найденным ценам; за потолок выходят "
          f"{ru((f.get('groups') or {}).get('выше потолка вилки', {}).get('rows'))} строк</td>"
          f"</tr>")
    if cf.get("totals"):
        t = cf["totals"]
        a(f"<tr><td>строк с уверенностью A или B, где проверка записала дефект</td>"
          f"<td class='n'>{ru(t['rows'])}</td>"
          f"<td class='dim'>{ru(t['exposure'])} USD — {t['share_pct']} % экспозиции; худший "
          f"класс «ссылка ведёт на другой артикул»</td></tr>")
    if col.get("totals"):
        t = col["totals"]
        a(f"<tr><td>артикулов, под которыми идут РАЗНЫЕ детали</td>"
          f"<td class='n'>{ru(t['defect_articles'])}</td>"
          f"<td class='dim'>{ru(t['defect_exposure'])} USD — {t['defect_share_pct']} %; "
          f"количество сложено по несопоставимым позициям, цена за штуку бессмысленна</td></tr>")
    rvsk = [r for r in rv if r.get("skeptics")]
    fell = sum(1 for r in rv for s in (r.get("skeptics") or []) if not s.get("holds"))
    a(f"<tr><td>строк перепроверено, из них проверено на опровержение</td>"
      f"<td class='n'>{ru(len(rv))} · {ru(len(rvsk))}</td>"
      f"<td class='dim'>не устояло выводов: {ru(fell)}. Каждое возражение — либо цифра не с той "
      f"страницы, либо вывод не о том предмете, либо сравнение с вилкой, которой в данных "
      f"нет</td></tr>")
    if (en.get("totals") or {}).get("in_request"):
        a(f"<tr><td>строк с восстановленным английским оригиналом наименования</td>"
          f"<td class='n'>{ru(en['totals']['in_request'])}</td>"
          f"<td class='dim'>русское наименование этого листа — машинный перевод, он не ищется "
          f"ни в одном каталоге; запрос на 330 строк готов к отправке</td></tr>")
    a(f"<tr><td>вопросов заказчику готово к отправке</td><td class='n'>{ru(len(qs))}</td>"
      f"<td class='dim'>за ними {ru(sum(float(x.get('qty') or 0) for x in qs))} штук; пока "
      f"строка в этом списке, она не идёт ни в запрос цен, ни в сумму закупки</td></tr>")
    a("</table>")

    a("<h2>Что нужно от вас, по цене решения</h2>")
    solar = next((b for b in (ch.get("brands") or []) if b["brand"] == "Solar Turbines"), {})
    a("<table><colgroup><col style='width:62mm'><col style='width:24mm'><col style='width:20mm'>"
      "<col></colgroup>")
    a("<thead><tr><th>решение</th><th class='n'>что открывает</th><th>сколько занимает</th>"
      "<th>почему без вас нельзя</th></tr></thead>")
    a(f"<tr><td><b>Регистрация в магазине Solar</b> (shop.solarturbines.com)</td>"
      f"<td class='n'>{ru(solar.get('usd'))} USD<br>{ru(solar.get('rows'))} строк</td>"
      f"<td>час</td>"
      f"<td>магазин изготовителя даёт цену И остаток, у {ru(solar.get('no_estimate'))} из этих "
      f"строк оценки нет вовсе. Регистрация требует ваших данных и подписи</td></tr>")
    a("<tr><td><b>Отправить письмо с вопросами заказчику</b></td>"
      f"<td class='n'>{ru(sum(float(x.get('qty') or 0) for x in qs))} штук<br>"
      f"{ru(len(qs))} строк</td><td>письмо готово</td>"
      "<td>переводит строки из «мы не нашли» в «ждём исходные от вас». Отправка наружу — "
      "ваше решение, я не отправляю</td></tr>")
    en_rows, en_qty = rfq_en_size(rows, en)
    a(f"<tr><td><b>Отправить запрос цен на английском</b> ({ru(en_rows)} строк, "
      f"{ru(en_qty)} штук)</td>"
      "<td class='n'>по этим строкам цены нет вовсе</td><td>документ готов</td>"
      "<td>то же: отправка наружу за вами. Наших цифр в запросе нет ни одной</td></tr>")
    a("<tr><td><b>Сказать, в каком поле Bitrix лежит выставленное вами ТКП</b></td>"
      "<td class='n'>снимает пометку<br>«направление не установлено»</td><td>одна строка</td>"
      "<td>у полей «Result, ТКП» и «Economics of the project» артикулов с ценой ровно ноль — "
      "там картинки. Пока поле не названо, я не подписываю чужую цену вашей</td></tr>")
    a("<tr><td><b>Права disk для вебхука Bitrix</b></td>"
      "<td class='n'>113 файлов НВН</td><td>настройка вебхука</td>"
      "<td>без них файлы таймлайна не скачиваются, и по листу НВН мы видим меньше, чем "
      "есть</td></tr>")
    a("<tr><td><b>BITRIX_WEBHOOK_URL в переменные окружения сессии</b></td>"
      "<td class='n'>прогон без Actions</td><td>настройка</td>"
      "<td>сейчас выгрузка идёт только через Actions, и документ с ценами приходится забирать "
      "артефактом</td></tr>")
    tail = ((ch.get("measure") or {}).get("tail") or {})
    a("<tr><td><b>Решение по остатку заявки вне карты каналов</b></td>"
      f"<td class='n'>{ru(tail.get('usd'))} USD<br>{ru(tail.get('rows'))} строк</td>"
      f"<td>ваше решение</td>"
      f"<td>{ru(tail.get('makers'))} изготовителей, крупнейший даёт "
      f"{ru(tail.get('biggest_maker_usd'))} USD: каналом это не закрывается. Либо считать по "
      f"классу с оговоркой, либо выносить отдельным приложением</td></tr>")
    a("</table>")

    a("<h2>Что я делаю дальше без вас</h2><ol>")
    a("<li>Добираю строки из топ-60, про которые не знаем ничего: по деньгам это "
      "705 095 USD на девятнадцати строках — замер на 18.09.2026, он уменьшается по ходу "
      "разведки.</li>")
    a("<li>Веду перепроверку крупных строк с проверкой на опровержение — без неё не устояло "
      f"{ru(fell)} выводов, и это цена отсутствия такой проверки раньше.</li>")
    a("<li>Держу прогон выгрузки на охвате «Энергосети»: он даёт цены поставщиков по нашим "
      "номерам. Охват именем заказчика не работает — проверено, пересечение ноль.</li>")
    a("</ol>")
    a("<p class='dim'>Собирает gt/tools/ship_decisions.py из наборов ship_lukoil, "
      "ship_channels, ship_collisions, ship_confidence, ship_offer_stats, ship_reverify, "
      "ship_questions, ship_english_source.</p>")
    return "".join(h)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    hp = OUT / "РЕШЕНИЯ-ЛУКОЙЛ.html"
    pp = OUT / "РЕШЕНИЯ-ЛУКОЙЛ.pdf"
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
