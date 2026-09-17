#!/usr/bin/env python3
"""Что лежит в Bitrix по заявке ЛУКОЙЛ: опись файлов без цен, документом.

Зачем отдельный документ. Распоряжение владельца 17.09.2026: «наша задача
собрать всё, что есть в системе». «Всё, что есть» — это сперва опись: сколько
файлов, где они лежат, какие из них цены содержат и какие разобрать нечем.
Пока описи нет, любая цифра по ценам висит в воздухе: непонятно, доля от чего.

Вход:  gt/data/bitrix_tkp_index.json — пишет bitrix_tkp.py, цен в нём нет.
Выход: gt/docs/ЧТО-ЕСТЬ-В-СИСТЕМЕ-ЛУКОЙЛ.pdf — цен нет, значит коммитится.

Сознательно не делает: не считает цены (их в описи нет и быть не должно) и не
складывает файлы в «покрытие». Файл найден — это не файл разобран, а разобран —
не значит, что в нём наша позиция. Три разных числа, и в документе они стоят
порознь.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "gt/data/bitrix_tkp_index.json"
OUT = ROOT / "gt/docs"
CHROME = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
]

CSS = """
@page { size: A4; margin: 12mm 10mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 8.4pt; color: #111; margin: 0; }
h1 { font-size: 18pt; margin: 0 0 2mm; }
h2 { font-size: 12pt; margin: 0 0 2.5mm; border-bottom: 1.4pt solid #111; padding-bottom: 1mm; }
h3 { font-size: 9.5pt; margin: 4mm 0 1.5mm; }
p { margin: 0 0 2.5mm; line-height: 1.4; }
/* Разделы ТЕКУТ, а не начинаются с новой страницы. Принудительный разрыв
   оставлял полупустые страницы, когда охват описи маленький (93 файла вместо
   1998) — pdf_check это ловит. Не рвём только заголовок от своего текста. */
.sec { margin-bottom: 4mm; }
h2 { page-break-after: avoid; }
h3 { page-break-after: avoid; }
.lead { font-size: 9pt; }
.dim { color: #666; }
table.t { width: 100%; border-collapse: collapse; table-layout: fixed; margin-bottom: 3mm; }
.t thead { display: table-header-group; }
.t tr { page-break-inside: avoid; }
.t th { background: #111; color: #fff; text-align: left; padding: 1.3mm 1.6mm; font-size: 7.6pt; }
.t td { padding: 1.3mm 1.6mm; word-wrap: break-word; overflow-wrap: anywhere; vertical-align: top; }
.t tbody tr:nth-child(even) td { background: #f6f6f6; }
.t td.n, .t th.n { text-align: right; }
.pn { font-family: "DejaVu Sans Mono", monospace; }
table.k { border-collapse: collapse; margin: 0 0 3mm; width: 100%; }
.k td { padding: 1.4mm 3mm 1.4mm 0; border-bottom: 0.3pt solid #ddd; vertical-align: top; }
.k td.l { font-weight: bold; width: 52mm; }
.big { font-size: 13pt; font-weight: bold; }
ol, ul { margin: 0 0 3mm; padding-left: 5.5mm; }
li { margin-bottom: 1.5mm; line-height: 1.4; }
.warn { border-left: 2.4pt solid #111; padding-left: 3.5mm; margin-bottom: 3.5mm; }
.bar { height: 3.4mm; background: #111; display: inline-block; vertical-align: middle; }
.barb { background: #e6e6e6; display: block; width: 100%; }
"""

# Направления в порядке ценности: так же, как их разбирает bitrix_tkp.py
DIR_ORDER = ["наша цена", "входящее", "неизвестно", "заявка", "наш запрос"]
DIR_WHAT = {
    "наша цена": "выставленное заказчику: «Result, ТКП» и «Economics of the project»",
    "входящее": "пришло от поставщика или сорсера: «Offer from supplier(s)», «КП поставщика»",
    "неизвестно": "по имени поля не определить — решается по содержимому файла",
    "заявка": "требования заказчика: «Техническая спецификация», цен в ней нет",
    "наш запрос": "наши исходящие: «Offer from us», «Request file» — цен заказчику нет",
}
ORIGIN_KIND = [
    (re.compile(r"^сделка "), "карточка сделки"),
    (re.compile(r"^СП-166 "), "запрос поставщику"),
    (re.compile(r"^дело "), "письмо или дело"),
    (re.compile(r"^комментарий|^таймлайн"), "комментарий таймлайна"),
    (re.compile(r"^чат"), "чат сделки"),
]


def E(x) -> str:
    return html.escape(str(x if x is not None else ""))


def ru(n) -> str:
    return f"{int(round(float(n or 0))):,}".replace(",", " ")


def kind_of(origin: str) -> str:
    for rx, name in ORIGIN_KIND:
        if rx.search(origin or ""):
            return name
    return "прочее"


def bar(part: int, whole: int) -> str:
    pct = 0 if not whole else max(0.6, 100 * part / whole)
    return (f'<span class="barb"><span class="bar" style="width:{pct:.1f}%"></span></span>')


def flatten(doc: dict, scope: str | None = None) -> tuple[list, dict, list]:
    """Опись, сводка и список охватов. Понимает оба формата.

    Опись накопительная: ключ `scopes` — охват прогона (слово в названии сделки
    либо её id). Прогон обновляет только свой охват. Прежний однопрогонный
    формат с верхним `inventory` тоже читается — иначе документ переставал
    собираться на старых файлах, и это выглядело бы как отсутствие данных.
    """
    sc = doc.get("scopes")
    if not isinstance(sc, dict):
        return (doc.get("inventory") or []), doc, ["(прежний формат)"]
    names = sorted(sc)
    if scope:
        names = [n for n in names if n == scope] or names
    inv, sums = [], {"deals": 0, "rfq_items": 0, "files": 0, "downloaded": 0}
    for n in names:
        v = sc.get(n) or {}
        inv += (v.get("inventory") or [])
        for k in sums:
            try:
                sums[k] += int(v.get(k) or 0)
            except (TypeError, ValueError):
                pass
    return inv, sums, names


def scope_table(doc: dict) -> str:
    """Что за охваты в описи и сколько в каждом.

    Складывать их можно только осознанно: одна сделка попадает в два охвата,
    если её название подходит под оба слова. Документ об этом говорит вслух,
    чтобы сумма не выглядела покрытием.
    """
    sc = doc.get("scopes")
    if not isinstance(sc, dict):
        return ""
    h = ["<h3>Охваты описи</h3><p>Опись накопительная: каждый прогон пишет свой "
         "охват и не касается чужих. Числа по охватам НЕ складываются слепо — "
         "одна сделка попадает в два охвата, если подходит под оба слова.</p>",
         "<table class='t'><colgroup><col style='width:48mm'><col style='width:20mm'>"
         "<col style='width:22mm'><col style='width:22mm'><col></colgroup>",
         "<thead><tr><th>охват</th><th class='n'>сделок</th><th class='n'>запросов</th>"
         "<th class='n'>файлов</th><th>состояние</th></tr></thead><tbody>"]
    for n in sorted(sc):
        v = sc[n] or {}
        h.append(f"<tr><td>{E(n)}</td><td class='n'>{ru(v.get('deals'))}</td>"
                 f"<td class='n'>{ru(v.get('rfq_items'))}</td>"
                 f"<td class='n'>{ru(len(v.get('inventory') or []))}</td>"
                 f"<td>{E(v.get('state') or v.get('updated') or '')}</td></tr>")
    h.append("</tbody></table>")
    return "".join(h)


def build(doc: dict) -> str:
    inv, sums, scopes = flatten(doc)
    total = len(inv)
    by_dir = Counter(x.get("direction") or "неизвестно" for x in inv)
    by_kind = Counter(kind_of(x.get("origin")) for x in inv)
    by_field = Counter(x.get("field_name") or "(без имени поля)" for x in inv)
    by_via = Counter(x.get("via") or "" for x in inv)
    named = sum(1 for x in inv if x.get("file_name"))
    deals = {x["origin"] for x in inv if str(x.get("origin", "")).startswith("сделка ")}

    # что попадёт в разбор и что отрежется пределом
    wanted = [x for x in inv if (x.get("direction") in ("наша цена", "входящее")
                                 or x.get("direction") == "неизвестно")]
    cut = total - len(wanted)

    # по сделке: сколько файлов и сколько из них с ценой
    per = defaultdict(Counter)
    for x in inv:
        per[x.get("origin")][x.get("direction") or "неизвестно"] += 1

    # разобранность — есть только после боевого прогона
    parsed = [x for x in inv if x.get("status")]
    by_status = Counter(x.get("status") for x in parsed)
    priced = sum(int(x.get("priced") or 0) for x in inv)
    pns = {p for x in inv for p in (x.get("pns") or [])}

    h = [f"<!doctype html><meta charset='utf-8'><title>Что есть в системе</title>"
         f"<style>{CSS}</style>"]
    a = h.append

    a("<div class='sec'><h1>Что есть в системе по ЛУКОЙЛу</h1>")
    a("<p class='lead'>Опись файлов, найденных адресным обходом Bitrix24: сделка → "
      "запросы поставщикам → таймлайн → дела → чат. Цен в описи нет намеренно — "
      "репозиторий публичный, цены уезжают отдельно.</p>")
    a("<table class='k'>")
    a(f"<tr><td class='l'>охватов в описи</td><td class='big'>{ru(len(scopes))}</td>"
      f"<td class='dim'>{E(', '.join(scopes))}</td></tr>")
    a(f"<tr><td class='l'>сделок обойдено</td>"
      f"<td class='big'>{ru(sums.get('deals') or len(deals))}</td></tr>")
    a(f"<tr><td class='l'>запросов поставщикам</td>"
      f"<td class='big'>{ru(sums.get('rfq_items', 0))}</td></tr>")
    a(f"<tr><td class='l'>файлов найдено</td><td class='big'>{ru(total)}</td></tr>")
    a(f"<tr><td class='l'>из них с именем файла</td><td>{ru(named)} — "
      f"остальным имя даёт заголовок отдачи или магические байты, "
      f"<span class='dim'>Bitrix в файловом объекте имени не отдаёт</span></td></tr>")
    a(f"<tr><td class='l'>к разбору</td><td class='big'>{ru(len(wanted))}</td></tr>")
    a(f"<tr><td class='l'>отсечено направлением</td><td>{ru(cut)} — "
      f"заявка заказчика и наши исходящие запросы</td></tr>")
    if sums.get("downloaded"):
        a(f"<tr><td class='l'>скачано и разобрано</td>"
          f"<td class='big'>{ru(sums['downloaded'])}</td></tr>")
        a(f"<tr><td class='l'>строк с парой «артикул — цена»</td><td class='big'>{ru(priced)}</td></tr>")
        a(f"<tr><td class='l'>уникальных артикулов с ценой</td><td class='big'>{ru(len(pns))}</td></tr>")
    else:
        a("<tr><td class='l'>скачано</td><td>ничего: это холостой прогон. "
          "Опись говорит, ЧТО есть, а не что в этом написано</td></tr>")
    a("</table>")

    a("<div class='warn'><p><b>Три разных числа, и складывать их нельзя.</b> "
      "«Файл найден» — не «файл разобран». «Файл разобран» — не «в нём наша "
      "позиция». «В нём есть цена» — не «цена на наш объём». В документе они "
      "стоят порознь, и ни одно не выдаётся за покрытие заявки.</p></div>")
    a("</div>")

    a("<div class='sec'><h2>Направление файла: четыре группы, а не две</h2>")
    a("<p>Направление читается по имени поля карточки. Это и есть та "
      "очевидность из контекста, которая отделяет присланное КП от нашего же "
      "запроса. При сомнении ставится «неизвестно», а не «наш запрос»: "
      "отброшенное КП мы не увидим никогда, а свой запрос виден сразу по "
      "отсутствию цен.</p>")
    a("<table class='t'><colgroup><col style='width:26mm'><col style='width:18mm'>"
      "<col style='width:26mm'><col></colgroup>")
    a("<thead><tr><th>направление</th><th class='n'>файлов</th><th>доля</th>"
      "<th>что это</th></tr></thead><tbody>")
    for d in DIR_ORDER:
        n = by_dir.get(d, 0)
        if not n:
            continue
        a(f"<tr><td><b>{E(d)}</b></td><td class='n'>{ru(n)}</td>"
          f"<td>{bar(n, total)}</td><td>{E(DIR_WHAT.get(d, ''))}</td></tr>")
    a("</tbody></table>")

    a("<h3>Откуда взят файл</h3>")
    a("<table class='t'><colgroup><col style='width:46mm'><col style='width:20mm'>"
      "<col></colgroup><thead><tr><th>источник</th><th class='n'>файлов</th>"
      "<th>доля</th></tr></thead><tbody>")
    for k, n in by_kind.most_common():
        a(f"<tr><td>{E(k)}</td><td class='n'>{ru(n)}</td><td>{bar(n, total)}</td></tr>")
    a("</tbody></table>")
    a("<h3>Каким путём качается</h3><p>")
    a(" · ".join(f"<b>{E(k or 'не определено')}</b> {ru(v)}" for k, v in by_via.most_common()))
    a("</p>")
    a(scope_table(doc))
    a("</div>")

    a("<div class='sec'><h2>Поля карточек: где что лежит</h2>")
    a("<p>Полный перечень полей, в которых нашлись файлы. Колонка «направление» "
      "показывает, как поле прочитано правилом.</p>")
    a("<table class='t'><colgroup><col><col style='width:20mm'>"
      "<col style='width:26mm'><col style='width:26mm'></colgroup>")
    a("<thead><tr><th>поле</th><th class='n'>файлов</th><th>доля</th>"
      "<th>направление</th></tr></thead><tbody>")
    fdir = {}
    for x in inv:
        fdir.setdefault(x.get("field_name") or "(без имени поля)", x.get("direction"))
    for f, n in by_field.most_common():
        a(f"<tr><td>{E(f)}</td><td class='n'>{ru(n)}</td><td>{bar(n, total)}</td>"
          f"<td>{E(fdir.get(f) or 'неизвестно')}</td></tr>")
    a("</tbody></table></div>")

    if by_status:
        a("<div class='sec'><h2>Чем кончился разбор</h2>")
        a("<p>Статус не должен врать: «пусто» — строго для файла без символов, а "
          "pdf с текстовым слоем без позиций — это «текст без цен». Иначе оценка "
          "объёма распознавания сканов завышается.</p>")
        a("<table class='t'><colgroup><col><col style='width:20mm'><col style='width:30mm'>"
          "</colgroup><thead><tr><th>статус</th><th class='n'>файлов</th><th>доля</th>"
          "</tr></thead><tbody>")
        for s, n in by_status.most_common():
            a(f"<tr><td>{E(s)}</td><td class='n'>{ru(n)}</td>"
              f"<td>{bar(n, len(parsed))}</td></tr>")
        a("</tbody></table></div>")

    a("<div class='sec'><h2>По сделкам</h2>")
    a(f"<p>Сделок с файлами — {ru(len(per))}. Отсортировано по числу файлов; "
      f"столбец «наша цена» показывает, у каких сделок вообще есть документ с "
      f"выставленной ценой.</p>")
    a("<table class='t'><colgroup><col style='width:34mm'><col style='width:16mm'>"
      "<col style='width:20mm'><col style='width:18mm'><col style='width:20mm'>"
      "<col style='width:18mm'><col></colgroup>")
    a("<thead><tr><th>источник</th><th class='n'>всего</th><th class='n'>наша цена</th>"
      "<th class='n'>входящее</th><th class='n'>неизвестно</th>"
      "<th class='n'>заявка</th><th class='n'>наш запрос</th></tr></thead><tbody>")
    for o, c in sorted(per.items(), key=lambda kv: (-sum(kv[1].values()), str(kv[0]))):
        a(f"<tr><td class='pn'>{E(o)}</td><td class='n'>{ru(sum(c.values()))}</td>"
          + "".join(f"<td class='n'>{ru(c.get(d, 0)) if c.get(d) else '—'}</td>"
                    for d in DIR_ORDER) + "</tr>")
    a("</tbody></table></div>")

    a("<div class='sec'><h2>Что делать с этим дальше</h2><ol>")
    a(f"<li><b>Разобрать {ru(min(len(wanted), 400))} файлов в порядке ценности.</b> "
      f"Порядок: сначала «наша цена» ({ru(by_dir.get('наша цена', 0))} файлов), "
      f"затем «входящее» ({ru(by_dir.get('входящее', 0))}), затем вложения писем, "
      f"и только потом остальное неопознанное. Предел есть всегда, и отрезаться "
      f"должны канбан-картинки, а не выставленные цены.</li>")
    a("<li><b>Сверить артикулы с ценой против заявки.</b> Пересечение и даст "
      "ответ, на какие строки в системе есть подтверждённая цена, а на какие "
      "нет ничего, кроме нашей разведки.</li>")
    a(f"<li><b>Сканы — отдельной работой.</b> Файлов без текстового слоя видно "
      f"будет после боевого прогона; распознавание к ним применяется адресно, а "
      f"не ко всей {ru(total)}-файловой массе.</li>")
    a("<li><b>Не выдавать найденное за покрытие.</b> Пока строка не сведена с "
      "артикулом заявки и объёмом, она в свод закупки не идёт.</li>")
    a("</ol></div>")
    return "".join(h)


def main() -> int:
    if not SRC.exists():
        print(f"нет {SRC} — сначала gt/tools/bitrix_tkp.py", file=sys.stderr)
        return 1
    doc = json.loads(SRC.read_text(encoding="utf-8"))
    if not flatten(doc)[0]:
        print("опись пуста", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    hp = OUT / "ЧТО-ЕСТЬ-В-СИСТЕМЕ-ЛУКОЙЛ.html"
    pp = OUT / "ЧТО-ЕСТЬ-В-СИСТЕМЕ-ЛУКОЙЛ.pdf"
    hp.write_text(build(doc), encoding="utf-8")
    exe = next((c for c in CHROME if Path(c).exists()), None)
    if not exe:
        print("Chromium не найден — PDF не собран", file=sys.stderr)
        return 1
    subprocess.run(
        [exe, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
         "--run-all-compositor-stages-before-draw", "--virtual-time-budget=120000",
         f"--print-to-pdf={pp}", hp.as_uri()],
        check=True, capture_output=True)
    print(f"{pp.name}: файлов в описи {len(flatten(doc)[0])}, "
          f"{pp.stat().st_size / 1e6:.1f} МБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
