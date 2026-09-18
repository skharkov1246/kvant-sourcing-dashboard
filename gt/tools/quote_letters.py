#!/usr/bin/env python3
"""Запрос ЦЕНЫ И НАЛИЧИЯ: строки, где адресат назван, а спрашивать никто не начал.

ЗАЧЕМ ТРЕТИЙ НАБОР ПИСЕМ. У каждого своя задача, и складывать их нельзя:

  gt/tools/ship_rfq.py       — твёрдое предложение там, где склад уже НАЙДЕН;
  gt/tools/volume_letters.py — подтвердить ОБЪЁМ там, где цена есть, а остатка нет;
  этот инструмент           — спросить ЦЕНУ И НАЛИЧИЕ там, где назван только адрес.

Третий случай оказался самым большим и был не закрыт вовсе. Замер 18.09.2026:
у 657 строк перепроверки канал назван, и 522 из них на 8 629 058 USD не попали
ни в один пакет писем — просто потому, что у первого условие «есть склад», а у
второго «есть цена». Письмо по ним пишется одинаково и стоит один раз.

ПОЧЕМУ ЭТО НЕ РАЗВЕДКА. Разведка ищет, у кого спросить. Здесь уже известно, у
кого, и известно из разбора с названной страницей. Дальше поиск не помогает:
прейскурантов по этим номерам в открытом доступе нет, что и записано в разборе
каждой строки.

ЧТО СПРАШИВАЕТСЯ. Пять вопросов: цена за штуку и за весь объём, остаток числом,
срок под наше количество, срок действия цены, базис поставки. Больше нельзя —
письмо станет перепиской.

ЧЕГО В ПИСЬМЕ НЕТ: нашей вилки, суммы и имени заказчика.

    python gt/tools/quote_letters.py [--print]
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
LEAK = ROOT / "gt/data/ship_leak.json"
OTHER_SETS = ("ship_rfq_letters.json", "ship_volume_letters.json", "ship_lists_rfq.json")
OUT = ROOT / "gt/data/ship_quote_letters.json"
DOCS = ROOT / "gt/docs"
NAME = "ЗАПРОС-ЦЕНЫ-И-НАЛИЧИЯ-ЛУКОЙЛ"
CHROME = ["/opt/pw-browsers/chromium/chrome-linux/chrome",
          "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
          "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]
MAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")

CSS = """
@page { size: A4 portrait; margin: 14mm 12mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 9pt; color: #111; margin: 0; }
h1 { font-size: 15pt; margin: 0 0 2mm; }
h2 { font-size: 10.5pt; margin: 6mm 0 2mm; border-bottom: 1.2pt solid #111;
     padding-bottom: 0.8mm; page-break-after: avoid; }
p { margin: 0 0 2.5mm; line-height: 1.5; }
.dim { color: #555; font-size: 8.4pt; }
.warn { border: 1pt solid #111; padding: 2mm; }
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


def short_maker(maker_short, ask_man) -> str:
    """Короткое имя для письма наружу; проза разбора не годится (см.
    gt/tools/volume_letters.py — там это уже стоило бы отправленного письма)."""
    for cand in (maker_short, ask_man):
        t = str(cand or "").strip()
        if t and len(t) <= 61 and ":" not in t:
            return t
    return ""


def already_written() -> set[str]:
    """Номера, по которым письмо уже есть в другом пакете."""
    out: set[str] = set()
    for name in OTHER_SETS:
        p = ROOT / "gt/data" / name
        if not p.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        for L in d.get("letters") or []:
            for pn in L.get("pns") or []:
                out.add(key(pn))
    return out


def leak_domains() -> dict[str, str]:
    if not LEAK.exists():
        return {}
    out = {}
    for s in json.loads(LEAK.read_text(encoding="utf-8")).get("sources") or []:
        m = re.search(r"https?://(?:www\.)?([^/]+)", str(s.get("url") or ""))
        if m:
            out[m.group(1).lower()] = str(s.get("holder") or "")
    return out


def candidates() -> tuple[list, list]:
    ask = {key(r.get("pn")): r for r in json.loads(ASK.read_text(encoding="utf-8"))["rows"]
           if key(r.get("pn"))}
    done = already_written()
    with_mail, without = [], []
    for x in json.loads(RV.read_text(encoding="utf-8"))["rows"]:
        k = key(x.get("pn"))
        if not str(x.get("channel") or "").strip() or k in done:
            continue
        a = ask.get(k) or {}
        mails = MAIL.findall(f"{x.get('contacts') or ''} {x.get('channel') or ''}")
        item = {"pn": x.get("pn"), "qty": a.get("qty"), "unit": a.get("unit") or "шт",
                "maker": short_maker(x.get("maker_short"), a.get("man")),
                "our_exposure": round(expo(a), 2),
                "mail": mails[0] if mails else ""}
        (with_mail if mails else without).append(item)
    return with_mail, without


def body(items: list[dict]) -> str:
    lines = ["Добрый день!", "",
             "Просим дать предложение по позициям ниже.",
             "По каждой позиции нужны пять ответов:", "",
             "1) цена за штуку и цена за весь указанный объём;",
             "2) остаток на складе ЧИСЛОМ на сегодня;",
             "3) срок поставки под указанное количество целиком;",
             "4) срок действия цены;",
             "5) базис поставки (Инкотермс).", "",
             "Позиции:"]
    for i, it in enumerate(items, 1):
        maker = f", изготовитель {it['maker']}" if it["maker"] else ""
        qty = f" — {it['qty']} {it['unit']}" if it.get("qty") else ""
        lines.append(f"{i}. {it['pn']}{qty}{maker}")
    lines += ["", "Если позиция снята с производства, просим указать действующую замену и "
                  "её номер. Если позиции нет в вашей номенклатуре, достаточно одного слова "
                  "«нет» — это тоже ответ.", "",
              "С уважением,", "КВАНТ"]
    return "\n".join(lines)


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
                        f"({warn}). Письмо ему — осознанное решение, а не рассылка.")
            if warn else "",
            "subject": f"Запрос цены и наличия: {len(items)} позиц. "
                       f"({', '.join(str(x['pn']) for x in items[:3])}"
                       f"{' и др.' if len(items) > 3 else ''})",
            "rows": len(items),
            "our_exposure": round(sum(x["our_exposure"] for x in items), 2),
            "pns": [x["pn"] for x in items],
            "body": body(items),
        })
    return {
        "updated": date.today().isoformat(),
        "source": "Строки перепроверки, где канал назван, а письма нет ни в одном пакете. "
                  "Считает gt/tools/quote_letters.py.",
        "what_it_is": "Запрос цены и наличия по строкам, где адресат известен из разбора, а "
                      "спрашивать никто не начал.",
        "why_third_set": "У пакетов разные условия: ship_rfq берёт строки со НАЙДЕННЫМ "
                         "складом, volume_letters — строки с ценой без остатка числом. "
                         "Строки, где есть только адрес, не подходили ни туда, ни туда — и "
                         "оказались самым большим классом.",
        "what_is_not_in_the_letter": "Ни нашей вилки, ни суммы, ни имени заказчика.",
        "rows_total": len(with_mail) + len(without),
        "usd_total": round(sum(x["our_exposure"] for x in with_mail + without), 2),
        "rows_with_address": len(with_mail),
        "usd_with_address": round(sum(x["our_exposure"] for x in with_mail), 2),
        "rows_without_address": len(without),
        "usd_without_address": round(sum(x["our_exposure"] for x in without), 2),
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
    a(f"<h1>Запрос цены и наличия: {ru(len(d['letters']))} писем, "
      f"{ru(d['rows_with_address'])} строк на {ru(d['usd_with_address'])} долларов США</h1>")
    a("<div class='lead'>")
    a(f"<p><b>Что это.</b> {E(d['what_it_is'])} Всего таких строк "
      f"<b>{ru(d['rows_total'])}</b> на <b>{ru(d['usd_total'])} долларов США</b>; адрес есть "
      f"у <b>{ru(d['rows_with_address'])}</b> на <b>{ru(d['usd_with_address'])}</b>.</p>")
    a(f"<p><b>Почему это отдельный пакет.</b> {E(d['why_third_set'])}</p>")
    a(f"<p><b>Чего в письме нет.</b> {E(d['what_is_not_in_the_letter'])} Письмо уходит "
      f"наружу, а объём закупки — наша переговорная позиция.</p>")
    a(f"<p class='dim'>Строк без адреса: {ru(d['rows_without_address'])} на "
      f"{ru(d['usd_without_address'])} долларов США — по ним сначала ищется адрес у "
      f"изготовителя и в его сети обслуживания. Подбирать получателя по классу изделия "
      f"нельзя: ответ придёт про класс, а не про эту деталь.</p>")
    a("</div>")
    a("<h2>Порядок отправки по деньгам</h2>")
    a("<table><thead><tr><th class='n'>№</th><th>адресат</th><th class='n'>позиций</th>"
      "<th class='n'>наша экспозиция, USD</th></tr></thead><tbody>")
    for i, L in enumerate(d["letters"], 1):
        a(f"<tr><td class='n'>{i}</td><td>{E(L['to'])}"
          f"{' ⚠' if L.get('warning') else ''}</td><td class='n'>{ru(L['rows'])}</td>"
          f"<td class='n'>{ru(L['our_exposure'])}</td></tr>")
    a("</tbody></table>")
    for i, L in enumerate(d["letters"], 1):
        a(f"<h2>{i}. {E(L['to'])} — {ru(L['rows'])} позиц., "
          f"{ru(L['our_exposure'])} USD нашей экспозиции</h2>")
        if L.get("warning"):
            a(f"<p class='warn'><b>{E(L['warning'])}</b></p>")
        a(f"<p><b>Тема:</b> {E(L['subject'])}</p>")
        a(f"<pre>{E(L['body'])}</pre>")
    a("<h2>Что делать с этим документом</h2>")
    a("<p><b>1. Отправлять сверху вниз.</b> Порядок — по нашей экспозиции. Первые десять "
      "адресатов закрывают большую часть суммы, и каждый из них несёт по нескольку "
      "позиций: одно письмо закрывает не одну строку.</p>")
    a("<p><b>2. «Нет» — полноценный ответ.</b> Письмо прямо это говорит. Отказ снимает "
      "строку с канала и отправляет её искать другого продавца, а молчание держит её в "
      "работе месяцами.</p>")
    a("<p><b>3. Ответ записывать числом и с датой.</b> Цена без срока действия и остаток "
      "без даты в сумму закупки не идут — это правило уже стоило нам разбора.</p>")
    a(f"<p><b>4. По {ru(d['rows_without_address'])} строкам на "
      f"{ru(d['usd_without_address'])} долларов США адреса нет — и это следующая работа, "
      f"а не пропуск.</b> Адрес ищется у изготовителя и в его сети обслуживания. Проверять "
      f"нашу базу поставщиков по словам названия бесполезно: такая сверка уже дала "
      f"заведомо ложные пары — фильтр Fleetguard к торговцу программируемыми "
      f"контроллерами, — то есть ровно «родовой адрес вместо адреса по детали».</p>")
    a("<p class='dim'>Артикулы без адреса, по убыванию нашей экспозиции: "
      + E(", ".join(str(x) for x in d.get("rows_no_address_list") or [])) + ".</p>")
    a("<p class='dim'>Считает gt/tools/quote_letters.py, набор gt/data/ship_quote_letters.json. "
      "Адресат, уже опубликовавший нашу заявку, помечен знаком ⚠ и отдельной строкой в своём "
      "письме — см. gt/data/ship_leak.json.</p>")
    return "".join(h)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true")
    a = ap.parse_args()
    d = build()
    OUT.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    m = lambda v: f"{v:,.0f}".replace(",", " ")  # noqa: E731
    print(f"строк с адресом, но без письма: {d['rows_total']} на {m(d['usd_total'])} USD")
    print(f"  писем к отправке: {len(d['letters'])} — {d['rows_with_address']} строк на "
          f"{m(d['usd_with_address'])} USD")
    print(f"  без адреса: {d['rows_without_address']} строк на "
          f"{m(d['usd_without_address'])} USD")
    if a.print and d["letters"]:
        print(f"\n--- {d['letters'][0]['to']}\n{d['letters'][0]['body'][:500]}")
    DOCS.mkdir(parents=True, exist_ok=True)
    hp, pp = DOCS / f"{NAME}.html", DOCS / f"{NAME}.pdf"
    hp.write_text(doc(d), encoding="utf-8")
    exe = next((c for c in CHROME if Path(c).exists()), None)
    if not exe:
        print("Chromium не найден — документ не собран", file=sys.stderr)
        return 1
    subprocess.run([exe, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
                    "--run-all-compositor-stages-before-draw", "--virtual-time-budget=180000",
                    f"--print-to-pdf={pp}", hp.as_uri()], check=True, capture_output=True)
    chk = subprocess.run([sys.executable, str(ROOT / "scripts/pdf_check.py"), str(pp)],
                         capture_output=True, text=True)
    print(f"\n{pp.name}: {pp.stat().st_size / 1e6:.1f} МБ")
    print(chk.stdout.strip())
    if "ПРОБЛЕМЫ" in chk.stdout or chk.returncode != 0:
        print("проверку вёрстки НЕ ПРОШЁЛ — так не отдавайте", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
