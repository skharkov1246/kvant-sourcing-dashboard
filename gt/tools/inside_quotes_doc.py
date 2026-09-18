#!/usr/bin/env python3
"""«Где цена уже есть» — рабочий лист исполнителя, сгруппированный по файлам.

ЗАЧЕМ ОТДЕЛЬНЫЙ ДОКУМЕНТ. Замер gt/tools/inside_quotes.py показал, что по 835
строкам заявки цена лежит во вложениях, которые нам УЖЕ прислали. Но список из
835 строк — не работа, а список. Работа становится работой, когда сгруппирована
по файлам: тринадцать вложений закрывают весь этот объём, а два из них — три
четверти денег. Открыть файл один раз и выписать из него все нужные номера —
это полдня человека, а не месяц разведки.

ПОРЯДОК — ПО ДЕНЬГАМ. Сначала файл, за которым стоит больше всего.

ЧТО ЗДЕСЬ НЕ НАПИСАНО. Сами цены: опись их не хранит, репозиторий публичный.
Здесь адрес и список номеров, которые надо в этом файле найти.

ЧЕСТНАЯ ОГОВОРКА, она же первая строка документа: номер стоит в файле, где есть
строки с ценой, — но стоит ли цена ПРОТИВ НЕГО, покажет только открытый файл.
Это зацепка высокой пробы, а не готовое число.

    python gt/tools/inside_quotes_doc.py
"""
from __future__ import annotations

import collections
import html
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "gt/data/ship_inside_quotes.json"
OUT = ROOT / "gt/docs"
NAME = "ГДЕ-ЦЕНА-УЖЕ-ЕСТЬ-ЛУКОЙЛ"
CHROME = ["/opt/pw-browsers/chromium/chrome-linux/chrome",
          "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
          "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]

CSS = """
@page { size: A4 portrait; margin: 14mm 12mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 9pt; color: #111; margin: 0; }
h1 { font-size: 15pt; margin: 0 0 2mm; }
h2 { font-size: 10.5pt; margin: 6mm 0 2mm; border-bottom: 1.2pt solid #111;
     padding-bottom: 0.8mm; page-break-after: avoid; }
p { margin: 0 0 2.5mm; line-height: 1.5; }
.dim { color: #555; font-size: 8.4pt; }
.lead { border: 1pt solid #111; padding: 3mm; margin-bottom: 4mm; }
table { border-collapse: collapse; width: 100%; font-size: 8.4pt; }
thead { display: table-header-group; }
th, td { border: 0.4pt solid #999; padding: 1.2mm 1.6mm; text-align: left;
         vertical-align: top; }
th { background: #eee; font-weight: bold; }
tr { page-break-inside: avoid; }
.n { text-align: right; white-space: nowrap; }
.k { font-weight: bold; }
"""


def E(x) -> str:
    return html.escape(str(x if x is not None else ""))


def ru(n) -> str:
    try:
        v = float(n)
    except (TypeError, ValueError):
        return E(n)
    s = f"{v:,.0f}" if abs(v - round(v)) < 0.005 else f"{v:,.2f}"
    return s.replace(",", " ").replace(".", ",")


def build() -> str:
    d = json.loads(SRC.read_text(encoding="utf-8"))
    groups: dict[tuple, list] = collections.defaultdict(list)
    for r in d["rows"]:
        f = r["found_in"][0]
        groups[(f["deal"], f["file"], f["rows"], f["rows_with_price"])].append(r)
    order = sorted(groups.items(),
                   key=lambda kv: -sum(x["usd_exposure"] for x in kv[1]))

    h: list[str] = [f"<!doctype html><meta charset='utf-8'><style>{CSS}</style>"]
    a = h.append
    a(f"<h1>Где цена уже есть: {ru(len(d['rows']))} строк заявки ЛУКОЙЛ "
      f"в {ru(len(order))} наших же вложениях</h1>")
    a("<div class='lead'>")
    a(f"<p><b>Что это.</b> По этим строкам заявки цену не надо искать в открытом доступе: "
      f"номер стоит в предложении, которое нам уже прислали. Всего "
      f"<b>{ru(len(d['rows']))}</b> строк на <b>{ru(d['usd_without_our_price'])} долларов "
      f"США</b>, и они закрываются <b>{ru(len(order))}</b> файлами.</p>")
    a("<p><b>Чего здесь нет.</b> Самих цен. Опись вложений их не хранит намеренно: "
      "репозиторий открыт, а это коммерческие данные контрагентов. Здесь адрес файла и "
      "список номеров, которые надо в нём найти.</p>")
    a("<p><b>Оговорка, без которой документом пользоваться нельзя.</b> Номер заявки НАЙДЕН в "
      "присланном нам файле — это и есть содержание документа, и оно проверено. Стоит ли "
      "цена против этого номера, покажет только открытый файл. Это зацепка высокой пробы, а "
      "не готовое число.</p>")
    a("<p><b>Почему графа «с ценой» ниже читается с осторожностью.</b> 18.09.2026 разбор "
      "выгрузки был проверен на своих же цифрах: из 6 619 значений по колонке «цена» взято "
      "317, остальные — правилом «последнее число строки». Правило брало номера позиций (в "
      "одном файле: 94, 101, 104, 105, 107 подряд) и количество из файлов-заявок. Теперь "
      "опись считает цены строго, а догадки — отдельным числом, и в таблицах ниже они "
      "названы раздельно. На адресную часть это не влияет: она стоит на найденном номере, а "
      "не на разобранной колонке цены.</p>")
    c = d.get("negative_control") or {}
    if c:
        a(f"<p><b>Почему этому списку можно верить.</b> Сверка по номеру убедительна сама "
          f"по себе, и это её слабое место: если предложений много, а номера коротки, "
          f"совпадать будет что угодно. Поэтому сделана проверка наоборот — теми же "
          f"предложениями сверены ВЫДУМАННЫЕ номера той же формы (перестановка цифр внутри "
          f"настоящего номера: длина, набор знаков и расположение букв те же, номер "
          f"другой). Настоящие номера совпали в <b>{ru(c['real_matched_pct'])} %</b> "
          f"случаев ({ru(c['real_matched'])} из {ru(c['real_checked'])}), выдуманные — в "
          f"<b>{ru(c['fake_matched_pct'])} %</b> ({ru(c['fake_matched'])} из "
          f"{ru(c['fake_checked'])}). Совпадение здесь несёт сведение, а не шум.</p>")
    a(f"<p class='dim'>{E(d['caveat'])} Считает gt/tools/inside_quotes.py, документ "
      f"собирает gt/tools/inside_quotes_doc.py.</p>")
    a("</div>")

    for n, ((deal, fname, rows, priced), items) in enumerate(order, 1):
        usd = sum(x["usd_exposure"] for x in items)
        items.sort(key=lambda x: -x["usd_exposure"])
        a(f"<h2>{n}. {E(deal)} · {E(fname)}</h2>")
        guesses = items[0]["found_in"][0].get("rows_with_price_guess")
        a(f"<p>За этим файлом стоит <b>{ru(usd)} долларов США</b> по "
          f"<b>{ru(len(items))}</b> строкам заявки. В самом файле {ru(rows)} строк, из них "
          f"с ценой по колонке {ru(priced)}"
          + (f", ещё {ru(guesses)} значений взяты догадкой «последнее число строки» и ценой "
             f"не считаются" if guesses else "") + ".</p>")
        a("<table><thead><tr><th>артикул заявки</th><th>наименование</th>"
          "<th class='n'>кол-во</th><th class='n'>деньги, USD</th></tr></thead><tbody>")
        for it in items:
            a(f"<tr><td class='k'>{E(it['pn'])}</td><td>{E(it['name'])}</td>"
              f"<td class='n'>{ru(it['qty'])}</td>"
              f"<td class='n'>{ru(it['usd_exposure'])}</td></tr>")
        a("</tbody></table>")

    # Закрывающий раздел. Он не украшение: без него последняя страница выходит
    # почти пустой (хвост таблицы в одну строку), и scripts/pdf_check.py
    # справедливо это помечает. Заодно документ перестаёт обрываться списком и
    # говорит, что с ним делать.
    a("<h2>Что делать с этим документом</h2>")
    a("<p><b>1. Открывать файлы сверху вниз.</b> Порядок здесь — по деньгам: первые два "
      "файла закрывают почти три четверти всей суммы. Один открытый файл — десятки "
      "закрытых строк, а не одна.</p>")
    a("<p><b>2. Выписывать цену вместе с тем, к чему она относится.</b> Не только цифру: "
      "количество, за которое она названа, валюту, срок действия предложения и имя "
      "приславшего. Цена без количества и без срока в сумму закупки не идёт — это правило "
      "уже стоило нам разбора.</p>")
    a("<p><b>3. Отсутствие цены против номера — тоже результат.</b> Если номер в файле "
      "есть, а цены против него нет, так и записать: тогда строка честно уходит в письмо "
      "поставщику, а не возвращается в разведку по второму кругу.</p>")
    a("<p class='dim'>Обратный порядок — сначала искать в открытом доступе, потом "
      "вспоминать про вложения — уже проверен: разведка по двенадцати строкам вернула одну "
      "цену из интернета и тут же сообщила, что по четырём другим цена лежит в присланном "
      "предложении, которое опись давно разобрала.</p>")
    return "".join(h)


def main() -> int:
    if not SRC.exists():
        print("замера нет — сначала gt/tools/inside_quotes.py --write", file=sys.stderr)
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
    # ПРОВЕРКА ВЁРСТКИ ЗДЕСЬ, А НЕ ОТДЕЛЬНОЙ КОМАНДОЙ. 18.09.2026 документ был
    # собран и запушен с полупустой последней страницей на 236 знаков:
    # scripts/pdf_check.py существует, но запускать его надо помнить, а помнить я
    # забыл. Дефект создаётся здесь — здесь ему и место быть пойманным.
    chk = subprocess.run(
        [sys.executable, str(ROOT / "scripts/pdf_check.py"), str(pp)],
        capture_output=True, text=True)
    if "ПРОБЛЕМЫ" in chk.stdout or chk.returncode != 0:
        print(chk.stdout.strip(), file=sys.stderr)
        print("документ собран, но проверку вёрстки НЕ ПРОШЁЛ — так его не отдавайте",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
