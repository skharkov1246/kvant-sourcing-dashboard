#!/usr/bin/env python3
"""Письма на ПОДТВЕРЖДЕНИЕ ОБЪЁМА: строки, которым до отгружаемости один ответ.

ЗАЧЕМ. В лестнице понимания между ступенями «есть цена и адрес» и «отгружаема»
стоит одно условие — продавец назвал остаток ЧИСЛОМ, а не словом «в наличии».
Замер 18.09.2026: таких строк 108 на 1 186 490 USD. Это не разведка и не поиск —
это одно письмо на продавца, и его до сих дня никто не составил: набор писем
(gt/tools/ship_rfq.py) собирался по строкам БЕЗ цены, а здесь цена уже есть, и
спрашивается другое.

ЧЕТЫРЕ ВОПРОСА, И ТОЛЬКО ОНИ. Остаток числом на сегодня; срок под наше
количество целиком; цена за весь объём; срок действия цены. Пятый вопрос делает
письмо перепиской, а не запросом, и ответ приходит позже.

ПОЧЕМУ БЕЗ НАШИХ ЦЕН И БЕЗ КОЛИЧЕСТВ ЗАКАЗЧИКА. Письмо уходит наружу. В нём
стоит номер, наше количество и вопрос — ни нашей вилки, ни имени заказчика, ни
сумм. Это правило выкладок, а не осторожность: назвав объём закупки, мы отдаём
продавцу нашу переговорную позицию.

ЧЕГО ЭТОТ НАБОР НЕ ДЕЛАЕТ. Не пишет письма там, где почты нет: таких строк 78
из 108, и это отдельная работа — найти адрес, а не сочинить его. Их число
печатается, чтобы отсев не был молчаливым.

    python gt/tools/volume_letters.py [--print]
"""
from __future__ import annotations

import argparse
import collections
import html
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASK = ROOT / "gt/data/ship_lukoil.json"
RV = ROOT / "gt/data/ship_reverify.json"
OUT = ROOT / "gt/data/ship_volume_letters.json"
LEAK = ROOT / "gt/data/ship_leak.json"
DOCS = ROOT / "gt/docs"
NAME = "ПИСЬМА-НА-ПОДТВЕРЖДЕНИЕ-ОБЪЁМА-ЛУКОЙЛ"
CHROME = ["/opt/pw-browsers/chromium/chrome-linux/chrome",
          "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
          "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]
# Точка в конце — часть предложения, а не адреса: первая же выдача дала
# «export@aftermarket.express.» и письмо ушло бы в никуда. Хвостовые знаки
# снимаются, зона требует минимум двух букв.
MAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")


#: Имя изготовителя для письма берётся ТОЛЬКО из коротких полей.
#: Поле real_maker — это разбор с оговорками, и первая же попытка вырезать из
#: него имя дала бы в письме поставщику строку «ДОГАДКА ПО КЛАССУ
#: ОПРОВЕРГНУТА: в нашей» — то есть наш внутренний вывод, отправленный наружу.
#: Порядок: maker_short перепроверки, затем поле изготовителя из заявки.
def short_maker(maker_short, ask_man) -> str:
    """Короткое имя изготовителя, пригодное для письма наружу."""
    for cand in (maker_short, ask_man):
        t = str(cand or "").strip()
        if not t or len(t) > 61 or ":" in t:
            continue
        return t
    return ""

CSS = """
@page { size: A4 portrait; margin: 14mm 12mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 9pt; color: #111; margin: 0; }
h1 { font-size: 15pt; margin: 0 0 2mm; }
h2 { font-size: 10.5pt; margin: 6mm 0 2mm; border-bottom: 1.2pt solid #111;
     padding-bottom: 0.8mm; page-break-after: avoid; }
p { margin: 0 0 2.5mm; line-height: 1.5; }
.dim { color: #555; font-size: 8.4pt; }
.lead { border: 1pt solid #111; padding: 3mm; margin-bottom: 4mm; }
pre { background: #f6f6f6; border: 0.4pt solid #bbb; padding: 2.5mm; white-space: pre-wrap;
      font-family: "DejaVu Sans Mono", monospace; font-size: 8pt; line-height: 1.45;
      page-break-inside: avoid; }
table { border-collapse: collapse; width: 100%; font-size: 8.4pt; }
thead { display: table-header-group; }
th, td { border: 0.4pt solid #999; padding: 1.2mm 1.6mm; text-align: left; vertical-align: top; }
th { background: #eee; }
tr { page-break-inside: avoid; }
.n { text-align: right; white-space: nowrap; }
"""


def key(x) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x or "").split("(")[0].upper())


def expo(r: dict) -> float:
    lo, hi, q = r.get("usd_lo"), r.get("usd_hi"), r.get("qty")
    if lo in (None, "") or hi in (None, "") or not q:
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(q)


def candidates() -> tuple[list, list]:
    """(строки с адресом, строки без адреса). Условие одно: цена и канал есть,
    остатка числом нет."""
    ask = {key(r.get("pn")): r for r in json.loads(ASK.read_text(encoding="utf-8"))["rows"]
           if key(r.get("pn"))}
    with_mail, without = [], []
    for x in json.loads(RV.read_text(encoding="utf-8"))["rows"]:
        a = ask.get(key(x.get("pn")))
        if not a:
            continue
        if not isinstance(x.get("price_low"), (int, float)):
            continue
        if not str(x.get("channel") or "").strip():
            continue
        if re.search(r"\d", str(x.get("stock") or "")):
            continue                       # остаток уже назван числом
        mails = MAIL.findall(f"{x.get('contacts') or ''} {x.get('channel') or ''}")
        item = {"pn": x.get("pn"), "qty": a.get("qty"), "unit": a.get("unit") or "шт",
                "what_it_is": str(x.get("what_it_is") or "")[:200],
                "maker": short_maker(x.get("maker_short"), a.get("man")),
                "our_exposure": round(expo(a), 2),
                "seller_hint": str(x.get("channel") or "")[:200],
                "mail": mails[0] if mails else ""}
        (with_mail if mails else without).append(item)
    return with_mail, without


def body(items: list[dict]) -> str:
    """Тело письма. Четыре вопроса и перечень позиций — без наших цен."""
    lines = ["Добрый день!", "",
             "Просим подтвердить возможность поставки по позициям ниже.",
             "По каждой позиции нужны четыре ответа:", "",
             "1) остаток на складе ЧИСЛОМ на сегодня;",
             "2) срок поставки под указанное количество целиком;",
             "3) цена за весь объём (и цена за штуку при этом объёме);",
             "4) срок действия цены и базис поставки (Инкотермс).", "",
             "Позиции:"]
    for i, it in enumerate(items, 1):
        maker = f", изготовитель {it['maker']}" if it["maker"] else ""
        lines.append(f"{i}. {it['pn']} — {it['qty']} {it['unit']}{maker}")
    lines += ["", "Если позиция снята с производства, просим указать действующую замену "
                  "и её номер.", "",
              "С уважением,", "КВАНТ"]
    return "\n".join(lines)


def leak_domains() -> dict[str, str]:
    """Домены, опубликовавшие нашу заявку. Письмо им — решение сорсера, а не
    случайность: адресат уже выложил наш перечень с количествами."""
    if not LEAK.exists():
        return {}
    out = {}
    for s_ in json.loads(LEAK.read_text(encoding="utf-8")).get("sources") or []:
        m = re.search(r"https?://(?:www\.)?([^/]+)", str(s_.get("url") or ""))
        if m:
            out[m.group(1).lower()] = str(s_.get("holder") or "")
    return out


def build() -> dict:
    with_mail, without = candidates()
    leaks = leak_domains()
    groups: dict[str, list] = collections.defaultdict(list)
    for it in with_mail:
        groups[it["mail"]].append(it)
    letters = []
    for mail, items in sorted(groups.items(), key=lambda kv: -sum(x["our_exposure"] for x in kv[1])):
        items.sort(key=lambda x: -x["our_exposure"])
        dom = mail.split("@")[-1].lower()
        warn = next((v for k, v in leaks.items() if dom == k or dom.endswith("." + k)), "")
        letters.append({
            "to": mail,
            "warning": (f"ЭТОТ АДРЕСАТ УЖЕ ОПУБЛИКОВАЛ НАШУ ЗАЯВКУ в открытом доступе "
                        f"({warn}): перечень с номерами и количествами лежит у него на сайте. "
                        f"Письмо ему — осознанное решение, а не рассылка: он и так знает наш "
                        f"объём. См. gt/data/ship_leak.json.") if warn else "",
            "subject": f"Подтверждение наличия и срока: {len(items)} позиц. "
                       f"({', '.join(str(x['pn']) for x in items[:3])}"
                       f"{' и др.' if len(items) > 3 else ''})",
            "rows": len(items),
            "our_exposure": round(sum(x["our_exposure"] for x in items), 2),
            "pns": [x["pn"] for x in items],
            "body": body(items),
        })
    return {
        "updated": date.today().isoformat(),
        "source": "Строки перепроверки, где цена и канал есть, а остаток числом не назван. "
                  "Считает gt/tools/volume_letters.py.",
        "what_it_is": "Письма на подтверждение объёма: этим строкам до ступени «отгружаема» "
                      "остался один ответ продавца, а не разведка.",
        "what_is_not_in_the_letter": "Ни нашей вилки, ни суммы, ни имени заказчика: письмо "
                                     "уходит наружу, и объём закупки — наша переговорная "
                                     "позиция.",
        "rows_total": len(with_mail) + len(without),
        "usd_total": round(sum(x["our_exposure"] for x in with_mail + without), 2),
        "rows_with_address": len(with_mail),
        "usd_with_address": round(sum(x["our_exposure"] for x in with_mail), 2),
        "rows_without_address": len(without),
        "usd_without_address": round(sum(x["our_exposure"] for x in without), 2),
        "what_to_do_without_address": "Найти адрес, а не сочинить: это отдельная работа. Пока "
                                      "адреса нет, строка остаётся на ступени «есть цена и "
                                      "адрес» и в отгрузку не идёт.",
        "letters": letters,
        "rows_no_address_list": [x["pn"] for x in
                                 sorted(without, key=lambda z: -z["our_exposure"])],
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


def doc(d: dict) -> str:
    h = [f"<!doctype html><meta charset='utf-8'><style>{CSS}</style>"]
    a = h.append
    a(f"<h1>Письма на подтверждение объёма: {ru(len(d['letters']))} писем, "
      f"{ru(d['rows_with_address'])} строк на {ru(d['usd_with_address'])} долларов США</h1>")
    a("<div class='lead'>")
    a(f"<p><b>Что это.</b> {E(d['what_it_is'])} Всего таких строк "
      f"<b>{ru(d['rows_total'])}</b> на <b>{ru(d['usd_total'])} долларов США</b>; адрес есть у "
      f"<b>{ru(d['rows_with_address'])}</b> на <b>{ru(d['usd_with_address'])}</b>.</p>")
    a(f"<p><b>Чего в письме нет.</b> {E(d['what_is_not_in_the_letter'])}</p>")
    a(f"<p><b>Строки без адреса: {ru(d['rows_without_address'])} на "
      f"{ru(d['usd_without_address'])} долларов США.</b> {E(d['what_to_do_without_address'])}</p>")
    a("</div>")
    for i, L in enumerate(d["letters"], 1):
        a(f"<h2>{i}. {E(L['to'])} — {ru(L['rows'])} позиц., "
          f"{ru(L['our_exposure'])} USD нашей экспозиции</h2>")
        if L.get("warning"):
            a(f"<p><b>{E(L['warning'])}</b></p>")
        a(f"<p><b>Тема:</b> {E(L['subject'])}</p>")
        a(f"<pre>{E(L['body'])}</pre>")
    a("<h2>Строки, по которым письма нет: адрес не найден</h2>")
    a(f"<p>{ru(d['rows_without_address'])} строк на {ru(d['usd_without_address'])} долларов "
      f"США. {E(d['what_to_do_without_address'])} Это не пропуск документа, а названная "
      f"числом работа: адрес ищется по изготовителю и по его сети обслуживания, а не "
      f"подбирается по догадке.</p>")
    # Перечень идёт текстом, а не столбцом таблицы: столбец из 78 артикулов
    # оставлял последнюю страницу на 312 знаков, и проверка вёрстки
    # справедливо это отбивала.
    a(f"<p>{E(', '.join(str(x) for x in d['rows_no_address_list']))}.</p>")
    a("<h2>Что делать с этим документом</h2>")
    a("<p><b>1. Отправить письма сверху вниз.</b> Порядок — по нашей экспозиции: первые пять "
      "адресатов закрывают больше половины суммы. Письмо составлено так, что ответ на него "
      "переводит строку на ступень «отгружаема» без дополнительной переписки.</p>")
    a("<p><b>2. Ответ записывать числом.</b> «В наличии» ответом не является — нужен остаток "
      "цифрой на дату и срок под весь объём. Именно отсутствие числа и держит эти строки "
      "здесь, а не отсутствие продавца.</p>")
    a("<p><b>3. Отказ — тоже ответ.</b> «Нет на складе, срок восемь недель» закрывает строку "
      "не хуже наличия: она уходит из отгружаемых в заказные, и это видно в плане.</p>")
    a("<p><b>4. По строкам без адреса не сочинять получателя.</b> Адрес ищется у изготовителя "
      "и в его сети обслуживания. Письмо, отправленное «в общую почту» подходящего по классу "
      "продавца, даёт ответ про класс, а не про эту деталь — это правило уже стоило нам "
      "разбора.</p>")
    a("<p class='dim'>Ни нашей вилки, ни суммы, ни имени заказчика в письмах нет: объём "
      "закупки — наша переговорная позиция. Считает gt/tools/volume_letters.py, набор "
      "gt/data/ship_volume_letters.json.</p>")
    return "".join(h)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true")
    a = ap.parse_args()
    d = build()
    OUT.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    m = lambda v: f"{v:,.0f}".replace(",", " ")  # noqa: E731
    print(f"строк, которым до отгружаемости один ответ: {d['rows_total']} на "
          f"{m(d['usd_total'])} USD")
    print(f"  писем к отправке: {len(d['letters'])} — {d['rows_with_address']} строк на "
          f"{m(d['usd_with_address'])} USD")
    print(f"  без адреса: {d['rows_without_address']} строк на "
          f"{m(d['usd_without_address'])} USD — работа найти адрес")
    if a.print:
        for L in d["letters"][:3]:
            print(f"\n--- {L['to']} · {L['rows']} позиц.\n{L['body'][:400]}")
    DOCS.mkdir(parents=True, exist_ok=True)
    hp, pp = DOCS / f"{NAME}.html", DOCS / f"{NAME}.pdf"
    hp.write_text(doc(d), encoding="utf-8")
    exe = next((c for c in CHROME if Path(c).exists()), None)
    if not exe:
        print("Chromium не найден — документ не собран", file=sys.stderr)
        return 1
    subprocess.run([exe, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
                    "--run-all-compositor-stages-before-draw", "--virtual-time-budget=120000",
                    f"--print-to-pdf={pp}", hp.as_uri()], check=True, capture_output=True)
    chk = subprocess.run([sys.executable, str(ROOT / "scripts/pdf_check.py"), str(pp)],
                         capture_output=True, text=True)
    print(f"\n{pp.name}: {pp.stat().st_size / 1e6:.1f} МБ")
    if "ПРОБЛЕМЫ" in chk.stdout or chk.returncode != 0:
        print(chk.stdout.strip(), file=sys.stderr)
        print("проверку вёрстки НЕ ПРОШЁЛ — так не отдавайте", file=sys.stderr)
        return 1
    print(chk.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
