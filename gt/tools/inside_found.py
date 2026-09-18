#!/usr/bin/env python3
"""Документ владельцу: цена закупки, найденная в НАШИХ ЖЕ вложениях сделок.

ЗАЧЕМ ОТДЕЛЬНЫЙ ДОКУМЕНТ. Разведка по витринам — слабейшее доказательство:
карточка неизвестного продавца неизвестного исполнения. Письменное предложение
контрагента по ЭТОЙ заявке сильнее всего, что можно найти поиском, и оно всё
это время лежало в приложениях к сделкам, не попадая ни в один счёт денег.
Причина была в правиле извлечения: без опознанного заголовка первый проход брал
последнее число строки, и из 6 619 значений выгрузки «Энергосети» ценой по
колонке оказались 317. Второй проход (gt/tools/tkp_tables.py) берёт цену только
там, где строка подтверждает её сама: единица × количество = итог.

ЧТО ЗДЕСЬ ПЕЧАТАЕТСЯ И ЧЕГО НЕТ. Печатается наша вилка, цена поставщика, вердикт
по вилке и происхождение цифры — файл, сделка, лист, строка. Не печатается
ничего, что получено домножением без подтверждения: сумма закупки считается
только по строкам, где количество в предложении покрывает количество заявки.

ВЫХОД НЕ КОММИТИТСЯ: документ содержит цены контрагентов. Инструмент проверяет,
что путь вывода вне дерева git.

    python gt/tools/inside_found.py --rows /tmp/rows_es.json \
        --out /tmp/pdf/ЦЕНА-ИЗ-ВЛОЖЕНИЙ.pdf --scope Энергосети
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
ASK = ROOT / "gt/data/ship_lukoil.json"
INSIDE = ROOT / "gt/data/ship_inside_quotes.json"
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
h2 { font-size: 11pt; margin: 5mm 0 2mm; border-bottom: 1.3pt solid #111; padding-bottom: 0.8mm; }
h3 { font-size: 9.4pt; margin: 3mm 0 1.5mm; }
p { margin: 0 0 2mm; line-height: 1.45; }
.dim { color: #666; }
.lead { font-size: 9pt; }
table { width: 100%; border-collapse: collapse; margin-bottom: 3mm; table-layout: fixed; }
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th { background: #111; color: #fff; text-align: left; padding: 1.2mm 1.5mm; font-size: 7.4pt; }
td { padding: 1.2mm 1.5mm; vertical-align: top; word-wrap: break-word;
     overflow-wrap: anywhere; border-bottom: 0.3pt solid #ddd; font-size: 7.4pt; }
td.n, th.n { text-align: right; white-space: nowrap; }
.take { background: #eef7ee; }
.hold { background: #fdf6e3; }
.stop { background: #fbeeee; }
.box { border: 0.8pt solid #111; padding: 2.5mm; margin-bottom: 3mm; page-break-inside: avoid; }
.sec { page-break-before: always; }
.sec:first-child { page-break-before: auto; }
ol, ul { margin: 0 0 2mm; padding-left: 5mm; }
li { margin-bottom: 1.4mm; line-height: 1.45; }
.k { font-weight: bold; }
"""


def E(x) -> str:
    return html.escape(str(x if x is not None else ""))


def ru(n) -> str:
    return f"{int(round(float(n or 0))):,}".replace(",", " ")


def money(n) -> str:
    return f"{float(n or 0):,.2f}".replace(",", " ").replace(".", ",")


def plural(n: int, one: str, few: str, many: str) -> str:
    """Согласование по-русски: «173 позиции», а не «173 позиций»."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def key(x) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x or "").split("(")[0].upper())


def expo(r: dict) -> float:
    lo, hi, q = r.get("usd_lo"), r.get("usd_hi"), r.get("qty")
    if lo in (None, "") or hi in (None, "") or not q:
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(q)


def verdict(lo, hi, usd) -> tuple[str, str]:
    """Что предложение делает с нашей вилкой. Края вилки — её часть."""
    if lo in (None, "") or hi in (None, ""):
        return "вилки нет", "hold"
    if usd > float(hi):
        return "ЗАНИЖЕНА", "stop"
    if usd < float(lo):
        return "ЗАВЫШЕНА", "hold"
    return "ВЕРНА", "take"


def best_by_part(rows: list, ask: dict) -> dict:
    """По номеру — НАИМЕНЬШАЯ подтверждённая цена: осторожная оценка закупки."""
    out: dict[str, dict] = {}
    for x in rows:
        if x["key"] not in ask or x.get("usd") is None:
            continue
        prev = out.get(x["key"])
        if prev is None or x["usd"] < prev["usd"]:
            out[x["key"]] = x
    return out


def build(rows: list, ask: dict, scope: str, counters: dict, inside: dict) -> str:
    best = best_by_part(rows, ask)
    joined = []
    for k, x in best.items():
        r = ask[k]
        v, cls = verdict(r.get("usd_lo"), r.get("usd_hi"), x["usd"])
        q = int(r.get("qty") or 0)
        covers = bool(q and x.get("qty_in_file") and int(x["qty_in_file"]) >= q)
        joined.append({"r": r, "x": x, "v": v, "cls": cls, "qty": q, "covers": covers,
                       "expo": expo(r), "buy": x["usd"] * q if covers else None})
    joined.sort(key=lambda j: -j["expo"])
    kinds = Counter(j["v"] for j in joined)
    expo_sum = sum(j["expo"] for j in joined)
    buy_rows = [j for j in joined if j["buy"] is not None]
    buy_sum = sum(j["buy"] for j in buy_rows)
    top = max(buy_rows, key=lambda j: j["buy"]) if buy_rows else None
    by_file = Counter(j["x"]["file"] for j in joined)
    yen = [j for j in joined if "¥" in str(j["x"].get("currency_why") or "")
           or "юань" in str(j["x"].get("usd_why") or "") or "CNY" in str(j["x"].get("usd_why") or "")]

    poz = plural(len(joined), "позиция", "позиции", "позиций")
    p = [f"<h1>Цена закупки из наших же вложений: {len(joined)} {poz} заявки</h1>",
         '<p class="lead">Цифры ниже взяты не из поиска по витринам, а из письменных '
         'предложений контрагентов, приложенных к нашим сделкам. Цена принята только там, '
         'где строка предложения подтверждает её сама: цена за единицу, умноженная на '
         'количество, даёт итог, напечатанный в той же строке. Поэтому ошибка «принял итог '
         'за цену штуки» здесь невозможна по построению.</p>',
         '<div class="box">',
         f'<p><span class="k">Главное.</span> По {len(joined)} позициям заявки появилась '
         f'цена закупки из письменного предложения поставщика. Это {ru(expo_sum)} USD нашей '
         f'экспозиции. Наша оценка занижена по {kinds.get("ЗАНИЖЕНА", 0)} позициям, верна по '
         f'{kinds.get("ВЕРНА", 0)}, завышена по {kinds.get("ЗАВЫШЕНА", 0)}, а по '
         f'{kinds.get("вилки нет", 0)} вилки у нас не было вовсе — теперь по ним есть цена.</p>',
         f'<p><span class="k">Сумма закупки считается по {len(buy_rows)} позициям</span> — '
         f'тем, где количество в предложении покрывает количество заявки: {ru(buy_sum)} USD. '
         + (f'Оговорка обязательна: {money(top["buy"])} USD этой суммы, то есть '
            f'{int(round(100 * top["buy"] / buy_sum))} %, приходится на одну позицию '
            f'{E(top["r"]["pn"])}. Одна строка определяет итог, и решение по ней надо '
            f'принимать отдельно от остальных.' if top and buy_sum else '')
         + '</p>',
         '<p><span class="k">Почему этого не видели раньше.</span> Правило извлечения брало '
         'последнее число строки там, где не опознало заголовок таблицы: из 6 619 значений, '
         'вынутых из этой выгрузки, ценой по колонке оказались 317, а остальные 6 302 — '
         'догадка, и она брала номера позиций и количество. На этом основании три настоящих '
         'предложения по нашей заявке не дали ни одной цены в счёт денег.</p>',
         '</div>']

    if yen:
        p += ['<div class="box">',
              f'<p><span class="k">Оговорка по валюте, без неё цифры читать нельзя.</span> '
              f'{len(yen)} {plural(len(yen), "позиция", "позиции", "позиций")} '
              f'взяты из предложения, где цена стоит со знаком ¥ и без кода '
              f'валюты. Знак означает и юань, и иену, а курсы различаются в 22,8 раза. Принят '
              f'юань: поставщик китайский, количество в строках указано в «pcs», а иена дала бы '
              f'по камере сгорания SGT-400 порядок 2 700 USD за штуку, чего не бывает. Решение '
              f'записано в прогоне, а не подразумевается. Если поставщик подтвердит иену, все '
              f'эти цифры делятся на 22,8.</p>', '</div>']

    p += ['<h2>Откуда взяты цены</h2>',
          '<table><thead><tr><th style="width:52%">файл предложения</th>'
          '<th class="n" style="width:14%">позиций заявки</th>'
          '<th style="width:34%">что это за файл</th></tr></thead><tbody>']
    notes = {
        "QT 127 SGT-400 minor parts (4).pdf":
            "Предложение по запчастям SGT-400, цены в ¥, количества совпадают с нашими.",
        "Quotation-CambiaTech Group Limited.pdf":
            "Предложение в долларах, со сроком поставки по каждой строке.",
    }
    for fn, n in by_file.most_common():
        p.append(f'<tr><td>{E(fn)}</td><td class="n">{n}</td>'
                 f'<td>{E(notes.get(fn, "предложение или ведомость из вложений сделки"))}</td></tr>')
    p.append('</tbody></table>')

    p += ['<div class="sec"><h2>Позиции с ценой поставщика: наша вилка против его цены</h2>',
          '<p class="dim">Отсортировано по нашей экспозиции. «Занижена» значит, что поставщик '
          'просит выше потолка нашей вилки, «завышена» — что ниже пола, «верна» — что внутри. '
          'Столбец «покрытие» показывает, хватает ли количества в предложении на весь объём '
          'заявки: где не хватает, строка в сумму закупки не идёт.</p>',
          '<table><thead><tr>'
          '<th style="width:13%">номер</th><th class="n" style="width:5%">кол-во</th>'
          '<th class="n" style="width:11%">наша вилка, USD/шт</th>'
          '<th class="n" style="width:9%">поставщик, USD/шт</th>'
          '<th style="width:8%">вердикт</th><th class="n" style="width:9%">экспозиция</th>'
          '<th class="n" style="width:9%">закупка</th><th style="width:7%">покрытие</th>'
          '<th style="width:29%">происхождение цифры</th>'
          '</tr></thead><tbody>']
    for j in joined:
        r, x = j["r"], j["x"]
        band = (f'{ru(r.get("usd_lo"))} – {ru(r.get("usd_hi"))}'
                if r.get("usd_lo") not in (None, "") else "—")
        src = (f'{E(x.get("origin"))}, файл {E(x.get("file"))}'
               f'{", лист " + E(x.get("sheet")) if x.get("sheet") else ""}'
               f'{", строка " + E(x.get("row")) if x.get("row") else ""}. '
               f'{E(x.get("unit"))} {E(x.get("currency"))} × {E(x.get("qty_in_file"))} = '
               f'{E(x.get("total"))}')
        p.append(f'<tr class="{j["cls"]}"><td>{E(r.get("pn"))}</td>'
                 f'<td class="n">{ru(j["qty"])}</td><td class="n">{band}</td>'
                 f'<td class="n">{money(x["usd"])}</td><td>{E(j["v"])}</td>'
                 f'<td class="n">{ru(j["expo"])}</td>'
                 f'<td class="n">{ru(j["buy"]) if j["buy"] is not None else "—"}</td>'
                 f'<td>{"полное" if j["covers"] else "не хватает"}</td>'
                 f'<td>{src}</td></tr>')
    p.append('</tbody></table></div>')

    p += ['<div class="sec"><h2>Поправка к прежнему утверждению</h2>',
          '<p>В отчётах стояло: «цена лежит в наших вложениях по '
          f'{ru(inside.get("rows_of_request_found", 0))} строкам на '
          f'{ru(inside.get("usd_found", 0))} USD». Это был АДРЕС, а не цена: набор сводит '
          'строку заявки с файлом, в котором есть хоть одна цена, и номер попадает в адрес '
          'даже тогда, когда цены по нему нет. Замер по выгрузке даёт другую картину:</p>',
          '<ul>',
          '<li><span class="k">Quotation p76057.pdf</span> — ответ поставщика на наш же запрос '
          'RFQ 22566-41034 по Solar Taurus. В нём 316 значений со знаком доллара, и все до '
          'одного — 0,00. Поставщик вернул перечень без цен, и 371 номер заявки, чей адрес '
          'указывает на этот файл, ценой не закрыт ничем.</li>',
          '<li><span class="k">suppliers_22566.xlsx</span> — наша собственная рабочая таблица '
          '(990 номеров заявки), в графах наличия стоит «нет». Предложением она не является '
          'и ценой быть не может.</li>',
          '<li>Цена сошлась по строке ровно там, где предложение её действительно называет, — '
          'и это то, что напечатано в таблице выше.</li>',
          '</ul>',
          '<p><span class="k">Вывод для защиты.</span> В деньги имеет право идти только '
          'сошедшаяся цена. Разница между адресом и ценой — не потеря данных, а разница между '
          '«в файле есть цены и есть наш номер» и «у нашей строки есть цена».</p></div>']

    p += ['<div class="sec"><h2>Что делать</h2><ol>',
          f'<li><span class="k">Подтвердить твёрдым офером {len(buy_rows)} '
          f'{plural(len(buy_rows), "позицию", "позиции", "позиций")}.</span> '
          'Предложение поставщика — не подтверждённая закупка: остаток на сегодня, срок под '
          'наше количество, срок действия цены и Инкотермс он отдельно не подтверждал. '
          'Письмо по этим строкам уходит одному адресату — тому, кто предложение и присылал.</li>',
          f'<li><span class="k">Пересмотреть вилки по {kinds.get("ЗАНИЖЕНА", 0)} заниженным '
          f'{plural(kinds.get("ЗАНИЖЕНА", 0), "позиции", "позициям", "позициям")}.</span> Это не разведка по витрине, а письменная цена контрагента: там, где '
          'он просит выше нашего потолка, запаса на снижение нет вовсе, и строка убыточна при '
          'выставленной цене.</li>',
          f'<li><span class="k">Закрыть {kinds.get("вилки нет", 0)} '
          f'{plural(kinds.get("вилки нет", 0), "позицию", "позиции", "позиций")} без вилки.</span> '
          'По ним оценку не надо искать разведкой — она уже написана контрагентом, достаточно '
          'перенести её в заявку с пометкой происхождения.</li>',
          '<li><span class="k">Дать вебхуку право на вложения писем.</span> Диск не отдал '
          '335 файлов по двум охватам (222 и 113), и это единственное, что упирается в решение '
          'владельца, а не в нашу работу. Среди них могут быть такие же предложения с ценами.</li>',
          '<li><span class="k">Прогнать охват «НВН» по исправленному правилу.</span> Его '
          'счётчики помечены устаревшими (считаны до разделения цен по градусу), а в его файлах '
          '423 номера нашей заявки.</li>',
          '</ol></div>']
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>Цена из вложений — {E(scope)}</title><style>{CSS}</style></head>"
            f"<body>{''.join(p)}</body></html>")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True, help="выдача gt/tools/tkp_tables.py --json")
    ap.add_argument("--out", required=True, help="куда положить PDF (ВНЕ репозитория)")
    ap.add_argument("--scope", default="", help="охват прогона")
    a = ap.parse_args()

    out = Path(a.out).resolve()
    try:
        out.relative_to(ROOT)
    except ValueError:
        pass
    else:
        print(f"ОТКАЗ: {out} внутри репозитория, а документ содержит цены контрагентов.",
              file=sys.stderr)
        return 2
    src = Path(a.rows)
    if not src.exists():
        print(f"нет {src}", file=sys.stderr)
        return 1
    doc = json.loads(src.read_text(encoding="utf-8"))
    ask: dict[str, dict] = {}
    for r in json.loads(ASK.read_text(encoding="utf-8"))["rows"]:
        k = key(r.get("pn"))
        if k:
            ask.setdefault(k, r)
    inside = json.loads(INSIDE.read_text(encoding="utf-8")) if INSIDE.exists() else {}
    html_text = build(doc["rows"], ask, a.scope, doc.get("counters", {}), inside)
    html_path = out.with_suffix(".html")
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_text, encoding="utf-8")
    exe = next((c for c in CHROME if Path(c).exists()), None)
    if not exe:
        print("Chromium не найден — PDF не собран", file=sys.stderr)
        return 1
    subprocess.run(
        [exe, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
         "--run-all-compositor-stages-before-draw", "--virtual-time-budget=120000",
         f"--print-to-pdf={out}", html_path.as_uri()],
        check=True, capture_output=True)
    print(f"{out} — {out.stat().st_size / 1e6:.2f} МБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
