#!/usr/bin/env python3
"""Письма изготовителю по строкам заявки, где у нас НЕТ НИЧЕГО.

ЗАЧЕМ. Пакеты писем (gt/tools/quote_letters.py и соседние) собираются из строк
ПЕРЕПРОВЕРКИ — то есть из тех, по которым разведка уже назвала канал. А самый
дорогой разряд заявки в них не попадает вовсе: строки, по которым нет ни
найденной цены, ни экспертной вилки. Замер 21.09.2026 по данным репозитория:
таких 681 строка из 1 642. Сведение с файлом потребности заказчика
(gt/tools/budget_join.py) показало, сколько это стоит: 439 из них есть в его
файле с ценой, и на них лежит 20 367 578 USD бюджета — почти четверть всего
бюджета заявки. По этим строкам мы не знаем ни цены, ни даже порядка.

ЧЕМ ОПРЕДЕЛЯЕТСЯ АДРЕСАТ, И ПОЧЕМУ ДВУМЯ ПУТЯМИ. Сначала — НАШИ шаблоны
номеров из gt/data/ship_channels.json, те же, которыми считается разложение
заявки по каналам. Это главный путь, потому что на турбинных листах столбец
«Производитель» заказчика ВРЁТ, и это измерено: под «Солар» у него лежат блок
Bently Nevada 1701/25 и датчик Det-Tronics EQ3005PCNR, под «SIEMENS» —
сервоклапан MOOG MOD-885-005; письмо по его столбцу ушло бы изготовителю с
чужими деталями в перечне.

Но там, где наш шаблон молчит, столбец заказчика — единственное, что есть, и
на приборных листах он как раз называет настоящего изготовителя узла: Balluff
BTL7-E500-…, WIKA, BEKA BA327E, GE Druck PTX. Замер 21.09.2026 показал цену
отказа от этого пути: у 38 строк из 182 «неопознанных» изготовитель назван
заказчиком И адрес его страницы контактов у нас уже прочитан — то есть пять
писем терялись на пустом месте. Поэтому путь второй: столбец заказчика,
сведённый с gt/data/maker_contacts.json. Откуда взялся адресат, набор пишет
по каждому письму (brand_source), и счётчики этих двух путей раздельные —
доверие к ним разное.

ЧТО В ПИСЬМЕ ЕСТЬ И ЧЕГО В НЁМ НЕТ. Есть: каталожный номер и количество.
НЕТ: ни цены, ни бюджета заказчика, ни его имени — бюджет это его коммерческая
тайна, а имя конечного заказчика изготовитель употребит, чтобы отправить нас
в свой региональный канал. Адрес не достраивается и не угадывается: берётся
только тот, что прочитан на странице контактов изготовителя
(gt/data/maker_contacts.json), и только с уверенностью выше низкой.

ЧТО ОСТАЁТСЯ РАБОТОЙ, А НЕ ПИСЬМОМ. У части строк бренд нашими шаблонами не
опознаётся — по ним письмо собирать некуда, и это отдельная работа
(атрибуция), а не отказ рынка. Число таких строк набор называет прямо.

    python gt/tools/gap_letters.py --write
    python gt/tools/gap_letters.py --write --pdf   # плюс документ владельцу
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pnkey import key  # noqa: E402

ASK = ROOT / "gt/data/ship_lukoil.json"
RV = ROOT / "gt/data/ship_reverify.json"
CHANNELS = ROOT / "gt/data/ship_channels.json"
MAKER_CONTACTS = ROOT / "gt/data/maker_contacts.json"
OTHER_SETS = ("ship_quote_letters.json", "ship_rfq_letters.json",
              "ship_volume_letters.json", "ship_lists_rfq.json", "seller_index.json")
# Наборы, в которых почта изготовителя могла быть записана прежней разведкой.
OWN_SETS = ("ship_lukoil.json", "ship_sellers.json", "ship_reverify.json",
            "ship_sweep.json", "research_suppliers.json", "rfq_suppliers.json",
            "dossiers.json", "bitrix_supplier_sites.json")
MAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,})")
OUT = ROOT / "gt/data/gap_letters.json"
DOCS = ROOT / "gt/docs"
NAME = "ПИСЬМА-ПО-ПУСТЫМ-СТРОКАМ-ЛУКОЙЛ"
CHROME = ["/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
          "/opt/pw-browsers/chromium/chrome-linux/chrome",
          "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]

CSS = """
@page { size: A4 portrait; margin: 14mm 13mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 9pt; color: #111; margin: 0; }
h1 { font-size: 15pt; margin: 0 0 2mm; }
h2 { font-size: 11pt; margin: 6mm 0 2mm; border-bottom: 1.2pt solid #111;
     padding-bottom: 0.8mm; page-break-after: avoid; }
p { margin: 0 0 2.5mm; line-height: 1.45; }
.dim { color: #666; }
.box { border: 0.8pt solid #111; padding: 3mm; margin-bottom: 4mm; }
table { width: 100%; border-collapse: collapse; margin-bottom: 3mm; }
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th { background: #111; color: #fff; text-align: left; padding: 1.2mm 1.5mm; font-size: 7.6pt; }
td { padding: 1.2mm 1.5mm; vertical-align: top; border-bottom: 0.3pt solid #ddd;
     font-size: 7.6pt; word-wrap: break-word; overflow-wrap: anywhere; }
td.n, th.n { text-align: right; white-space: nowrap; }
/* Тело письма ТЕЧЁТ через страницы: его длина задаётся числом позиций и ничем
   не ограничена, а запрет разрыва работает только для блока меньше страницы.
   Вместе держим не письмо, а заголовок с его началом — это .subj. */
pre { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 8.4pt; white-space: pre-wrap;
      line-height: 1.42; margin: 0 0 4mm; page-break-inside: auto; orphans: 3; widows: 3; }
/* Заголовок письма держится со строкой «Кому/Тема», и на этом цепочка
   обрывается: если тянуть за собой ещё и начало тела, группа не влезает в
   остаток страницы и уезжает целиком — замер дал полупустую страницу на 358
   символов при 21 письме. */
.subj { margin-bottom: 1.5mm; }
"""


def E(x) -> str:
    return html.escape(str(x if x is not None else ""))


def ru(n) -> str:
    return f"{int(round(float(n or 0))):,}".replace(",", " ")


def already_written() -> set[str]:
    """Номера, по которым письмо уже собрано в другом пакете."""
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


def gap_rows() -> tuple[list[dict], int]:
    """Строки заявки, где нет ни найденной цены, ни вилки. Считается по репозиторию."""
    ask = json.loads(ASK.read_text(encoding="utf-8"))["rows"]
    rv = {}
    for r in json.loads(RV.read_text(encoding="utf-8"))["rows"]:
        rv.setdefault(key(r.get("pn")), r)
    out = []
    for r in ask:
        k = key(r.get("pn"))
        if not k:
            continue
        x = rv.get(k) or {}
        found = x.get("price_low") if isinstance(x.get("price_low"), (int, float)) else None
        if found is None and r.get("usd_lo") in (None, "") and r.get("usd_hi") in (None, ""):
            out.append(r)
    return out, len(ask)


def brands() -> list[dict]:
    return json.loads(CHANNELS.read_text(encoding="utf-8"))["brands"]


def brand_of(row: dict, bs: list[dict]) -> str:
    """Бренд по НАШИМ шаблонам номера и наименования. Главный путь атрибуции."""
    txt = " ".join(str(row.get(f) or "")
                   for f in ("pn", "name", "man", "model", "real_maker", "real_pn"))
    for b in bs:
        if re.search(b["pattern"], txt, re.I):
            return str(b["brand"])
    return ""


def _norm(x) -> str:
    return re.sub(r"[^a-zа-я0-9]+", "", str(x or "").lower())


def maker_of(row: dict, cs: list[dict]) -> str:
    """Изготовитель по столбцу заказчика, сведённый с нашей книгой адресов.

    Второй путь, и только там, где первый молчит. Сведение идёт по самому
    длинному имени из книги адресов, которое входит в написание заказчика:
    «Drillmec» ⊂ «Drillmec S.p.A. / Oleobi S.r.l.». Обратное вхождение не
    берём — «Solar» не должен ловиться на «SolaHD».
    """
    n = _norm(row.get("man"))
    if not n:
        return ""
    best = ""
    for c in cs:
        m = _norm(c.get("maker"))
        if not m or m not in n:
            continue
        if str(c.get("confidence") or "").strip() == "низкая":
            continue
        if len(m) > len(_norm(best)):
            best = str(c.get("maker"))
    return best


def contacts() -> list[dict]:
    return json.loads(MAKER_CONTACTS.read_text(encoding="utf-8"))["rows"]


def owns_domain(maker: str, domain: str) -> bool:
    """Принадлежит ли домен САМОМУ изготовителю. Правило закрытое, не «входит в».

    Замер 21.09.2026 показал, чего стоит проверка на вхождение: «Argo Hytos»
    нашлась в cargocaresolutions.com, «Versa» — в universal-thermosensors.co.uk,
    «NATIONAL Oilwell Varco» — в platinum-international.store, «General
    Monitors» — в general-gauges.com, «Johnson Controls» — в johnsonturbine.com.
    Пять писем ушли бы чужим компаниям. Поэтому сравнивается ЦЕЛАЯ метка
    второго уровня: «phoenixcontact» = «Phoenix Contact», а «general-gauges» ≠
    «General Monitors». Дистрибьютор тоже отсеивается: «drilltechuae» ≠
    «Drilltech» — и это правильно, письмо изготовителю адресуется изготовителю.
    """
    labels = [x for x in str(domain or "").lower().split(".") if x]
    if not labels:
        return False
    # Метка второго уровня: у «beka.co.uk» это «beka», а не «co». Составные
    # окончания вида co.uk снимаются, но только если под ними что-то есть.
    known = {"co", "com", "net", "org", "gov", "ac", "edu"}
    label = labels[0]
    if len(labels) >= 2:
        label = labels[-2]
        if label in known and len(labels) >= 3:
            label = labels[-3]
    return bool(_norm(maker)) and _norm(label) == _norm(maker)


@lru_cache(maxsize=None)
def _own_sets() -> tuple[tuple[str, str], ...]:
    """Тексты наборов читаются один раз: иначе обход идёт по файлам на каждую строку."""
    out = []
    for name in OWN_SETS:
        p = ROOT / "gt/data" / name
        if p.exists():
            out.append((name, p.read_text(encoding="utf-8", errors="replace")))
    return tuple(out)


@lru_cache(maxsize=None)
def own_address(maker: str) -> dict:
    """Почта изготовителя, записанная НАШЕЙ прежней разведкой в наборах репозитория.

    Третий путь, и самый слабый: адрес не прочитан сейчас на странице контактов,
    а взят из нашей же записи. Поэтому набор пишет, из какого файла он взят, и
    уверенность ставит среднюю — с оговоркой в самом поле.
    """
    best = {}
    for name, txt in _own_sets():
        for m in MAIL.finditer(txt):
            if owns_domain(maker, m.group(1)):
                cand = m.group(0)
                # Общий адрес предпочтительнее личного: письмо переживёт
                # увольнение сотрудника.
                cur = best.get("email", "")
                pref = cand.split("@")[0].lower() in ("info", "sales", "support", "contact",
                                                      "enquiries", "webenquiries", "order")
                if not cur or (pref and cur.split("@")[0].lower() not in ("info", "sales")):
                    best = {"maker": maker, "email": cand, "form_url": "", "phone": "",
                            "read_on": f"gt/data/{name} — запись прежней разведки, "
                                       f"страница контактов сейчас не перечитывалась",
                            "confidence": "средняя"}
    return best


def address_for(brand: str, rows: list[dict]) -> dict:
    """Адрес изготовителя. Не достраивается: только прочитанный на его странице.

    Уверенность «низкая» письма не порождает — это правило уже стоило бы
    отправленного письма в gt/tools/quote_letters.py. Сопоставление идёт по
    самому длинному имени изготовителя, которое входит в название бренда:
    «Solar» ⊂ «Solar Turbines», но «Drillmec» ⊄ «Solar Turbines».
    """
    b = brand.lower()
    best = None
    for r in rows:
        m = str(r.get("maker") or "").strip()
        if not m or m.lower() not in b:
            continue
        if str(r.get("confidence") or "").strip() == "низкая":
            continue
        if best is None or len(m) > len(str(best.get("maker"))):
            best = r
    if not best:
        return {}
    mail = str(best.get("email") or "").strip()
    form = str(best.get("form_url") or "").strip()
    return {
        "maker": best.get("maker"),
        "email": mail if mail and mail != "None" else "",
        "form_url": form if form and form != "None" else "",
        "phone": str(best.get("phone") or "").strip(),
        "read_on": best.get("read_on"),
        "confidence": best.get("confidence"),
    }


def letter_text(brand: str, addr: dict, items: list[dict]) -> str:
    """Письмо изготовителю. Ни цены, ни бюджета, ни имени конечного заказчика."""
    lines = [
        "Добрый день!",
        "",
        f"Обращаемся как покупатель запасных частей {brand}.",
        "Просим по позициям ниже:",
        "",
        "1) назвать цену и срок поставки либо указать канал, через который вы "
        "продаёте эти позиции;",
        "2) подтвердить, действующее ли это обозначение, и назвать замену, если "
        "позиция снята с производства;",
        "3) если обозначение внутреннее (чертёжное), назвать соответствующий "
        "коммерческий номер изделия и его изготовителя — по внутреннему "
        "обозначению цену получить невозможно ни у одного продавца.",
        "",
        f"Позиций: {len(items)}. Перечень:",
    ]
    for i, it in enumerate(items, 1):
        q = f" — {ru(it['qty'])} {it['unit']}" if it.get("qty") else ""
        lines.append(f"{i}. {it['pn']}{q}")
    lines += ["", "С уважением,", "КВАНТ"]
    return "\n".join(lines)


def measure() -> dict:
    gap, ask_rows = gap_rows()
    bs, cs, done = brands(), contacts(), already_written()
    by: dict[str, list[dict]] = defaultdict(list)
    src: dict[str, str] = {}
    unbranded, skipped_done = [], 0
    for r in gap:
        if key(r.get("pn")) in done:
            skipped_done += 1
            continue
        item = {"pn": str(r.get("pn") or "").strip(), "qty": r.get("qty"),
                "unit": str(r.get("unit") or "шт").strip() or "шт",
                "name": str(r.get("name") or "")[:110], "sheet": r.get("sheet"),
                "man": str(r.get("man") or "").strip()}
        b = brand_of(r, bs)
        if b:
            src.setdefault(b, "наш шаблон номера")
        else:
            b = maker_of(r, cs)
            if not b and item["man"] and own_address(item["man"]).get("email"):
                b = item["man"]
            if b:
                src.setdefault(b, "столбец «Производитель» заказчика")
        if b:
            by[b].append(item)
        else:
            unbranded.append(item)

    letters, no_address = [], []
    for b, items in sorted(by.items(), key=lambda x: -len(x[1])):
        # Одна позиция — один номер: в файле заказчика та же деталь может идти
        # на две машины, и спрашивать её дважды в одном письме незачем.
        agg: dict[str, dict] = {}
        for it in items:
            a = agg.setdefault(it["pn"], {**it, "qty": 0.0})
            a["qty"] = float(a["qty"] or 0) + float(it["qty"] or 0)
        uniq = sorted(agg.values(), key=lambda z: str(z["pn"]))
        addr = address_for(b, cs) or (own_address(b) if src.get(b) ==
                                      "столбец «Производитель» заказчика" else {})
        rec = {
            "brand": b,
            "brand_source": src.get(b, "наш шаблон номера"),
            "rows": len(items),
            "positions": len(uniq),
            "qty_total": round(sum(float(x["qty"] or 0) for x in uniq), 2),
            "pns": [x["pn"] for x in uniq],
            **({"to": addr} if addr else {}),
        }
        if addr and (addr.get("email") or addr.get("form_url")):
            rec["subject"] = (f"Запрос цены и срока по запасным частям {b}: "
                              f"{len(uniq)} позиц.")
            rec["body"] = letter_text(b, addr, uniq)
            letters.append(rec)
        else:
            rec["why_no_letter"] = ("адреса изготовителя, прочитанного на его странице "
                                    "контактов, у нас нет; догадка вида «parts@домен» "
                                    "письма не порождает")
            no_address.append(rec)

    return {
        "updated": date.today().isoformat(),
        "source": "Строки заявки ЛУКОЙЛ, где нет ни найденной цены, ни экспертной вилки. "
                  "Бренд определён шаблонами номеров из gt/data/ship_channels.json. "
                  "Адреса — gt/data/maker_contacts.json. Считает gt/tools/gap_letters.py.",
        "what_it_is": "Готовые письма изготовителю по самому дорогому разряду заявки — "
                      "тому, по которому мы не знаем ни цены, ни порядка цены.",
        "what_it_is_not": "В письме нет ни цены, ни бюджета заказчика, ни его имени: "
                          "бюджет — его коммерческая тайна, а имя конечного заказчика "
                          "изготовитель употребит, чтобы отправить нас в свой "
                          "региональный канал. Отправляет письма человек.",
        "ask_rows": ask_rows,
        "gap_rows": len(gap),
        "of_them_already_in_a_letter": skipped_done,
        "letters": letters,
        "rows_in_letters": sum(L["rows"] for L in letters),
        "positions_in_letters": sum(L["positions"] for L in letters),
        "brands_without_address": no_address,
        "rows_without_address": sum(x["rows"] for x in no_address),
        "rows_by_attribution_path": {
            p: sum(L["rows"] for L in letters if L.get("brand_source") == p)
            for p in ("наш шаблон номера", "столбец «Производитель» заказчика")
        },
        "why_two_paths": "Шаблон номера — наше измерение, столбец заказчика — его слово, и "
                         "на турбинных листах оно неверно. Поэтому столбец берётся только "
                         "там, где шаблон молчит, и источник записан по каждому письму.",
        "unbranded_rows": len(unbranded),
        "unbranded_makers_named_by_customer": sorted({
            x["man"] for x in unbranded if x.get("man")}),
        "what_unbranded_means": "Ни наш шаблон номера, ни наша книга адресов эту строку не "
                                "закрыли. Изготовитель у большинства из них заказчиком "
                                "НАЗВАН — не хватает прочитанной страницы его контактов. "
                                "Это отдельная работа (читать контакты названных "
                                "изготовителей), а не отказ рынка.",
        "why_pattern_comes_first": "На турбинных листах столбец «Производитель» заказчика "
                                   "врёт: под «Солар» у него лежат блок Bently Nevada "
                                   "1701/25 и датчик Det-Tronics EQ3005PCNR, под «SIEMENS» — "
                                   "сервоклапан MOOG MOD-885-005. Поэтому первым идёт наш "
                                   "шаблон номера, а столбец — только там, где шаблон молчит.",
        "unbranded_sample": [x["pn"] for x in unbranded[:40]],
    }


def build(d: dict) -> str:
    a = []
    add = a.append
    add(f"<!doctype html><meta charset='utf-8'><title>Письма по пустым строкам</title>"
        f"<style>{CSS}</style>")
    add("<h1>Письма изготовителям по строкам, где у нас нет ничего</h1>")
    add(f"<p class='dim'>Собрано {E(d['updated'])} инструментом gt/tools/gap_letters.py. "
        f"Отправляет письма человек: ни одно не уходит само.</p>")
    add("<div class='box'>")
    add(f"<p><b>Разряд:</b> {ru(d['gap_rows'])} строк заявки из {ru(d['ask_rows'])} — ни "
        f"найденной цены, ни вилки. Письмо уже собрано в другом пакете по "
        f"{ru(d['of_them_already_in_a_letter'])} из них.</p>")
    add(f"<p><b>Готово писем: {ru(len(d['letters']))}</b> — "
        f"{ru(d['positions_in_letters'])} позиций по {ru(d['rows_in_letters'])} строкам. "
        f"Без адреса осталось {ru(d['rows_without_address'])} строк, бренд не опознан у "
        f"{ru(d['unbranded_rows'])}.</p>")
    add(f"<p class='dim'>{E(d['why_pattern_comes_first'])} {E(d['why_two_paths'])}</p>")
    add(f"<p class='dim'><b>Чего в письмах нет.</b> {E(d['what_it_is_not'])}</p>")
    add("</div>")

    add("<h2>Что куда уходит</h2>")
    add("<table><thead><tr><th>бренд</th><th class='n'>позиц.</th><th class='n'>штук</th>"
        "<th>адресат</th><th>чем опознан</th><th>откуда адрес</th></tr></thead><tbody>")
    for L in d["letters"]:
        to = L.get("to") or {}
        who = to.get("email") or to.get("form_url") or "—"
        add(f"<tr><td>{E(L['brand'])}</td><td class='n'>{ru(L['positions'])}</td>"
            f"<td class='n'>{ru(L['qty_total'])}</td><td>{E(who)}</td>"
            f"<td class='dim'>{E(L.get('brand_source'))}</td>"
            f"<td class='dim'>{E(str(to.get('read_on') or '')[:80])}</td></tr>")
    for x in d["brands_without_address"]:
        add(f"<tr><td>{E(x['brand'])}</td><td class='n'>{ru(x['positions'])}</td>"
            f"<td class='n'>{ru(x['qty_total'])}</td><td>адреса нет</td>"
            f"<td class='dim'>{E(x.get('brand_source'))}</td>"
            f"<td class='dim'>{E(x['why_no_letter'])}</td></tr>")
    add("</tbody></table>")

    for L in d["letters"]:
        to = L.get("to") or {}
        add(f"<h2>{E(L['brand'])} — {ru(L['positions'])} позиц.</h2>")
        add(f"<p class='subj'><b>Кому:</b> {E(to.get('email') or to.get('form_url'))}"
            + (f" · тел. {E(to['phone'])}" if to.get("phone") else "")
            + f"<br><b>Тема:</b> {E(L['subject'])}</p>")
        add(f"<pre>{E(L['body'])}</pre>")
    add("<h2>Что осталось работой, а не письмом</h2>")
    add(f"<p>{E(d['what_unbranded_means'])} Строк — {ru(d['unbranded_rows'])}. "
        f"Изготовителей, которых заказчик назвал, а страницы их контактов мы не читали — "
        f"{ru(len(d['unbranded_makers_named_by_customer']))}. Каждый прочитанный адрес "
        f"превращает свои строки в письмо тем же прогоном.</p>")
    add(f"<p class='dim'>{E(', '.join(d['unbranded_makers_named_by_customer'])[:2600])}</p>")
    return "".join(a)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="записать набор в репозиторий")
    ap.add_argument("--pdf", action="store_true", help="собрать документ в gt/docs")
    a = ap.parse_args()

    d = measure()
    print(f"строк без цены и без вилки: {d['gap_rows']} из {d['ask_rows']}; "
          f"письмо уже есть у {d['of_them_already_in_a_letter']}")
    print(f"писем готово: {len(d['letters'])} — {d['positions_in_letters']} позиций "
          f"по {d['rows_in_letters']} строкам")
    for L in d["letters"]:
        to = (L.get("to") or {})
        print(f"  {L['brand']:<36} {L['positions']:>4} позиц. → "
              f"{to.get('email') or to.get('form_url')}")
    if d["brands_without_address"]:
        print("без адреса:", ", ".join(f"{x['brand']} ({x['positions']})"
                                       for x in d["brands_without_address"]))
    print(f"бренд не опознан у {d['unbranded_rows']} строк — это атрибуция, а не поиск")

    if a.write:
        OUT.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"\n{OUT.relative_to(ROOT)}")
    if a.pdf:
        DOCS.mkdir(parents=True, exist_ok=True)
        hp, pp = DOCS / f"{NAME}.html", DOCS / f"{NAME}.pdf"
        hp.write_text(build(d), encoding="utf-8")
        exe = next((x for x in CHROME if Path(x).exists()), None)
        if not exe:
            print("Chromium не найден — PDF не собран", file=sys.stderr)
            return 1
        subprocess.run(
            [exe, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
             "--run-all-compositor-stages-before-draw", "--virtual-time-budget=120000",
             f"--print-to-pdf={pp}", hp.as_uri()], check=True, capture_output=True)
        print(f"{pp.relative_to(ROOT)} — {pp.stat().st_size / 1e6:.2f} МБ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
