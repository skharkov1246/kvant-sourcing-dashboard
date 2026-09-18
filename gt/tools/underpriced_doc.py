#!/usr/bin/env python3
"""«Где мы продаём ниже закупки» — решение по строкам с заниженной ценой.

ЗАЧЕМ. Цены владельца заказчику твёрдые: торг идёт от них вниз. Значит строка,
где единственная найденная цена выше нашего потолка, — это не «уточнить», а
позиция, по которой сделка теряет деньги. Перепроверка такие строки помечала
вердиктом «ЗАНИЖЕНА» с апреля, но денег на них никто не считал: вердикт стоял в
таблице, а сколько он стоит — нигде.

ГЛАВНОЕ ЧИСЛО — НАША ЭКСПОЗИЦИЯ, А НЕ РАЗРЫВ. Разрыв «найденная цена минус наш
потолок, умноженный на количество» считается легко и врёт: цена продавца
действует на подтверждённый им остаток, а не на весь объём. Первый подсчёт дал
2,78 млн USD, и половину принесли две строки — брокерский запрос на половину
объёма и витрина одного московского перекупщика с двумя доменами. Поэтому в
заголовок идёт наша собственная сумма по этим строкам: она измерена, а не
домножена.

ТРИ РАЗРЯДА, И СКЛАДЫВАТЬ ИХ НЕЛЬЗЯ — см. gt/tools/ship_underpriced.py.

    python gt/tools/underpriced_doc.py
"""
from __future__ import annotations

import html
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "gt/data/ship_underpriced.json"
OUT = ROOT / "gt/docs"
NAME = "ЗАНИЖЕННЫЕ-ЦЕНЫ-ЛУКОЙЛ"
CHROME = ["/opt/pw-browsers/chromium/chrome-linux/chrome",
          "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
          "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]

CSS = """
@page { size: A4 landscape; margin: 12mm 10mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 8.6pt; color: #111; margin: 0; }
h1 { font-size: 14pt; margin: 0 0 2mm; }
h2 { font-size: 10.5pt; margin: 6mm 0 2mm; border-bottom: 1.2pt solid #111;
     padding-bottom: 0.8mm; page-break-after: avoid; }
p { margin: 0 0 2.5mm; line-height: 1.5; }
.dim { color: #555; font-size: 8pt; }
.lead { border: 1pt solid #111; padding: 3mm; margin-bottom: 4mm; }
table { border-collapse: collapse; width: 100%; font-size: 7.8pt; }
thead { display: table-header-group; }
th, td { border: 0.4pt solid #999; padding: 1.1mm 1.4mm; text-align: left; vertical-align: top; }
th { background: #eee; font-weight: bold; }
tr { page-break-inside: avoid; }
.n { text-align: right; white-space: nowrap; }
.k { font-weight: bold; white-space: nowrap; }
.src { font-size: 7.2pt; color: #444; }
"""

#: Что делать с разрядом — текстом, а не оставлять читателя догадываться.
WHAT_TO_DO = {
    "в сумму идёт": "Решение можно принимать сразу: покрытие объёма подтверждено, цена не "
                    "брокерская, свидетель не один. Либо перецени́ть строку, либо снять её из "
                    "предложения.",
    "покрытие не подтверждено": "Цена годная, но продавец не подтверждал, что закроет наш "
                                "объём. Одно письмо на строку: остаток числом на сегодня, срок "
                                "под наше количество, цена за весь объём, срок действия. До "
                                "ответа в сумму закупки строка не идёт.",
    "цена не закупочная": "Цена настоящая, но закупочной не является: либо это запрос брокера, "
                          "либо она стоит на одном операторе под несколькими доменами. Нужен "
                          "второй, независимый свидетель — до него цифра работает только как "
                          "предупреждение.",
}


def E(x) -> str:
    return html.escape(str(x if x is not None else ""))


def ru(n) -> str:
    try:
        v = float(n)
    except (TypeError, ValueError):
        return E(n)
    s = f"{v:,.0f}" if abs(v - round(v)) < 0.005 else f"{v:,.2f}"
    return s.replace(",", " ").replace(".", ",")


def table(rows: list[dict]) -> str:
    h = ["<table><thead><tr><th>артикул</th><th>наименование</th><th class='n'>кол-во</th>"
         "<th class='n'>наш потолок, USD</th><th class='n'>найдено за штуку, USD</th>"
         "<th class='n'>во сколько раз</th><th class='n'>наша экспозиция, USD</th>"
         "<th>чем подтверждено и почему этот разряд</th></tr></thead><tbody>"]
    for r in rows:
        times = r["found_unit"] / r["our_hi"] if r["our_hi"] else 0
        h.append(
            f"<tr><td class='k'>{E(r['pn'])}</td><td>{E(r['name'])}</td>"
            f"<td class='n'>{ru(r['qty'])}</td><td class='n'>{ru(r['our_hi'])}</td>"
            f"<td class='n'>{ru(r['found_unit'])}</td><td class='n'>{ru(round(times, 1))}×</td>"
            f"<td class='n'>{ru(r['our_exposure'])}</td>"
            f"<td class='src'>{E(r['source'])}<br><b>Разряд:</b> {E(r['why_tier'])}</td></tr>")
    h.append("</tbody></table>")
    return "".join(h)


def build() -> str:
    d = json.loads(SRC.read_text(encoding="utf-8"))
    rows, tiers = d["rows"], d["tiers"]
    h: list[str] = [f"<!doctype html><meta charset='utf-8'><style>{CSS}</style>"]
    a = h.append
    a(f"<h1>Мы продаём ниже закупки: {ru(len(rows))} строк заявки ЛУКОЙЛ на "
      f"{ru(d['our_exposure_usd'])} долларов США нашего же предложения</h1>")
    a("<div class='lead'>")
    a(f"<p><b>Что это.</b> Строки, где единственная найденная цена закупки выше потолка "
      f"нашей вилки. Цены заказчику твёрдые, торг идёт вниз — значит по такой строке сделка "
      f"теряет деньги с первого дня. Всего таких строк <b>{ru(len(rows))}</b>, и нашего "
      f"предложения на них стоит <b>{ru(d['our_exposure_usd'])} долларов США</b>.</p>")
    a(f"<p><b>Почему главное число — наше, а не разрыв.</b> Разрыв «найденная цена минус наш "
      f"потолок, умноженный на количество» выходит {ru(sum(t['gap_usd'] for t in tiers.values()))} "
      f"USD, и это число нельзя ставить в заголовок: цена продавца действует на тот остаток, "
      f"который он подтвердил, а не на весь объём. Половину разрыва приносят две строки — "
      f"запрос брокера на половину количества и витрина одного перекупщика с двумя доменами. "
      f"Поэтому решение принимается по нашей экспозиции: она измерена.</p>")
    a("<p><b>Три разряда, складывать их нельзя.</b></p>")
    a("<table><thead><tr><th>разряд</th><th class='n'>строк</th>"
      "<th class='n'>наша экспозиция, USD</th><th class='n'>верхняя граница разрыва, USD</th>"
      "<th>что это значит</th></tr></thead><tbody>")
    for name, v in tiers.items():
        a(f"<tr><td class='k'>{E(name)}</td><td class='n'>{ru(v['rows'])}</td>"
          f"<td class='n'>{ru(v['our_exposure_usd'])}</td><td class='n'>{ru(v['gap_usd'])}</td>"
          f"<td>{E(WHAT_TO_DO[name])}</td></tr>")
    a("</tbody></table>")
    a(f"<p class='dim'>{E(d['what_it_is_not'])} Считает gt/tools/ship_underpriced.py, "
      f"документ собирает gt/tools/underpriced_doc.py. Отсеяно из разбора: "
      f"{E(', '.join(f'{k} — {v}' for k, v in d['skipped'].items() if v) or 'ничего')}.</p>")
    a("</div>")

    for n, (name, v) in enumerate(tiers.items(), 1):
        part = [r for r in rows if r["tier"] == name]
        if not part:
            continue
        part.sort(key=lambda r: -r["our_exposure"])
        a(f"<h2>{n}. Разряд «{E(name)}»: {ru(len(part))} строк, наша экспозиция "
          f"{ru(v['our_exposure_usd'])} USD</h2>")
        a(f"<p>{E(WHAT_TO_DO[name])}</p>")
        a(table(part))

    wo = d.get("written_offers") or {}
    if wo.get("by_scope"):
        a("<h2>Довод сильнее витрины: письменные предложения поставщиков</h2>")
        a(f"<p>{E(wo['why_it_matters'])}</p>")
        a("<table><thead><tr><th>лист заявки</th><th class='n'>строк с письменным "
          "предложением</th><th class='n'>предложение ВЫШЕ нашего потолка</th>"
          "<th class='n'>внутри нашей вилки</th><th class='n'>ниже нашего пола</th>"
          "</tr></thead><tbody>")
        for name, v in wo["by_scope"].items():
            a(f"<tr><td class='k'>{E(name)}</td>"
              f"<td class='n'>{ru(v['rows_with_written_offer'])}</td>"
              f"<td class='n'>{ru(v['offer_above_our_ceiling'])}</td>"
              f"<td class='n'>{ru(v['offer_inside_our_band'])}</td>"
              f"<td class='n'>{ru(v['offer_below_our_floor'])}</td></tr>")
        a("</tbody></table>")
        a(f"<p class='dim'>{E(wo['note'])} {E(wo['why_no_money_here'])} Поэтому таблицы выше "
          f"и эта таблица не противоречат друг другу: выше — строки, по которым цену нашла "
          f"разведка в открытом доступе и её можно назвать числом здесь; здесь — строки, по "
          f"которым цена пришла нам письмом, и она лежит в файле, а не в этом документе.</p>")

    a("<h2>Что делать по порядку</h2>")
    a("<p><b>1. Разряд «в сумму идёт» — решение сегодня.</b> По этим строкам оговорок нет: "
      "покрытие подтверждено, цена не брокерская, свидетель не один. Либо новая цена "
      "заказчику, либо строка уходит из предложения.</p>")
    a("<p><b>2. Разряд «покрытие не подтверждено» — одно письмо на строку.</b> Спрашивается "
      "ровно четыре вещи: остаток числом на сегодня, срок под наше количество, цена за весь "
      "объём, срок действия цены. Без ответа эта цена не закупочная, а ориентир.</p>")
    a("<p><b>3. Разряд «цена не закупочная» — сначала второй свидетель.</b> Брокерский запрос "
      "и одна витрина с двумя доменами не доказывают уровень цены. Здесь стоят самые дорогие "
      "строки, и именно поэтому их нельзя брать в расчёт как есть.</p>")
    a("<p><b>4. Три самые дорогие строки закрываются не поиском.</b> По ним цена лежит в "
      "предложении, которое нам прислали и которое мы ещё не открыли: перепроверка прямо "
      "указывает файл в Битриксе. Пока он не прочитан, любая цифра по этим строкам — оценка "
      "одного продавца.</p>")
    a("<p class='dim'>Обратное утверждение документ не делает: «наша цена выше найденной» "
      "здесь не считается за ошибку. Завышенная цена — предмет торга и разговора с "
      "заказчиком, а не убыток; по ней отдельный разбор в перепроверке.</p>")
    return "".join(h)


def main() -> int:
    if not SRC.exists():
        print("замера нет — сначала gt/tools/ship_underpriced.py", file=sys.stderr)
        return 1
    doc = build()
    OUT.mkdir(parents=True, exist_ok=True)
    hp, pp = OUT / f"{NAME}.html", OUT / f"{NAME}.pdf"
    hp.write_text(doc, encoding="utf-8")
    exe = next((c for c in CHROME if Path(c).exists()), None)
    if not exe:
        print("Chromium не найден — документ не собран", file=sys.stderr)
        return 1
    subprocess.run(
        [exe, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
         "--run-all-compositor-stages-before-draw", "--virtual-time-budget=180000",
         f"--print-to-pdf={pp}", hp.as_uri()], check=True, capture_output=True)
    print(f"{pp.name}: {pp.stat().st_size / 1e6:.1f} МБ")
    chk = subprocess.run([sys.executable, str(ROOT / "scripts/pdf_check.py"), str(pp)],
                         capture_output=True, text=True)
    if "ПРОБЛЕМЫ" in chk.stdout or chk.returncode != 0:
        print(chk.stdout.strip(), file=sys.stderr)
        print("документ собран, но проверку вёрстки НЕ ПРОШЁЛ — так его не отдавайте",
              file=sys.stderr)
        return 1
    print(chk.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
