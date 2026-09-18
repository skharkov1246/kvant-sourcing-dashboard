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
MAKER_CONTACTS = ROOT / "gt/data/maker_contacts.json"
OTHER_SETS = ("ship_rfq_letters.json", "ship_volume_letters.json", "ship_lists_rfq.json")
OUT = ROOT / "gt/data/ship_quote_letters.json"
DOCS = ROOT / "gt/docs"
NAME = "ЗАПРОС-ЦЕНЫ-И-НАЛИЧИЯ-ЛУКОЙЛ"
CHROME = ["/opt/pw-browsers/chromium/chrome-linux/chrome",
          "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
          "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]
MAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")
# Канал без почты. Замер 18.09.2026: из 48 строк, у которых почты нет, у 25 в
# разборе стоит ссылка и ещё у 6 — телефон, всего на 122 494 USD. Пока они
# лежали в графе «адреса нет», по ним не было названо никакого действия, хотя
# спросить было куда: отчёт писал «работа найти адрес», а адрес был прочитан.
SELLER_URL = re.compile(r"https?://[^\s,;)»\"']+")
SELLER_TEL = re.compile(r"\+\d[\d\s().-]{7,}\d")

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
/* Письмо ТЕЧЁТ через страницы. Было page-break-inside: avoid — и тело письма,
   не влезающее на остаток страницы, уезжало целиком на следующую, оставляя
   строку «Тема:» одну на пустой странице: замер 18.09.2026 — страница 67 из 77
   на 48 символов, scripts/pdf_check.py выдал «полупустые страницы». Запрет
   разрыва работает только для блока меньше страницы, а длина письма зависит от
   числа позиций в нём и не ограничена ничем. Держать вместе надо не письмо, а
   заголовок с началом письма — это делает .subj ниже. */
pre { background: #f6f6f6; border: 0.4pt solid #bbb; padding: 2.5mm; white-space: pre-wrap;
      font-family: "DejaVu Sans Mono", monospace; font-size: 8pt; line-height: 1.45;
      page-break-inside: auto; orphans: 3; widows: 3; }
.subj { page-break-after: avoid; }
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


def maker_key(t) -> str:
    """Ключ изготовителя: только буквы и цифры, регистр снят.

    Пояснение в СКОБКАХ снимается: «Fleetguard (Cummins Filtration)» и
    «Fleetguard» — один изготовитель, и без этого три строки Fleetguard не
    нашли своего адреса, хотя он был найден. А вот «A / B» остаётся как есть:
    «Drillmec S.p.A. / Oleobi S.r.l.» — ДВА изготовителя, и адрес у них разный,
    склеивать их значило бы отправить письмо не туда.

    ПРОВЕРЕНО И ОТКЛОНЕНО 18.09.2026: сопоставление по ВЛОЖЕНИЮ имени (адрес
    изготовителя берётся, если его имя входит в имя строки). Замер на остатке
    без адресата: закрылось бы 6 строк на 37 100 USD, но правильной из них одна
    («Siemens Energy» ⊃ «Siemens»), а пять — ошибочные: «Drillmec S.p.A. /
    Oleobi S.r.l.» ⊃ «Drillmec», то есть письмо по деталям Oleobi ушло бы в
    Drillmec. Правило отклонено: одна верная пара не оправдывает пяти писем не
    по адресу. Повторять этот заход не нужно.
    """
    t = re.sub(r"\s*\([^)]*\)", " ", str(t or ""))
    return re.sub(r"[^a-z0-9а-яё]", "", t.lower())


def maker_forms() -> dict[str, dict]:
    """Изготовители, у которых почты нет, но есть форма обращения.

    Форма — не «адреса нет», а другой способ отправки: текст тот же, вставляется
    в поле формы. Замер 18.09.2026: так закрываются 90 строк на 604 083 USD, и
    среди них ABB (39 строк) и Rockwell (342 600 USD) — то есть отнести форму к
    безадресному остатку значило бы спрятать шестьсот тысяч в графу «работа не
    определена».

    Саму форму заполняет человек: отправка чего-либо наружу от нашего имени —
    не то, что агент делает сам.
    """
    if not MAKER_CONTACTS.exists():
        return {}
    out = {}
    for r in json.loads(MAKER_CONTACTS.read_text(encoding="utf-8")).get("rows") or []:
        if MAIL.findall(str(r.get("email") or "")):
            continue
        if not str(r.get("form_url") or "").strip():
            continue
        out[maker_key(r.get("maker"))] = r
    return out


def maker_contacts() -> dict[str, dict]:
    """Адреса служб запчастей изготовителей — только с прочитанной страницы.

    Запись без поля read_on не берётся: адрес, о котором не сказано, где он
    напечатан, — это догадка вида «parts@домен», и письмо по ней уходит в
    никуда. То же правило, что и для цен: значение без названного
    происхождения в дело не идёт.
    """
    if not MAKER_CONTACTS.exists():
        return {}
    out = {}
    for r in json.loads(MAKER_CONTACTS.read_text(encoding="utf-8")).get("rows") or []:
        if not str(r.get("read_on") or "").strip():
            continue
        mails = MAIL.findall(str(r.get("email") or ""))
        if not mails:
            continue
        # Низкая уверенность письма не порождает. Адрес с такой пометкой —
        # обычно общая приёмная или канал ПО КЛАССУ изделий: по свече
        # промышленного газового двигателя разведка нашла автомобильный
        # послепродажный канал изготовителя. Такая запись остаётся знанием в
        # наборе, но письмо по ней не собирается.
        if str(r.get("confidence") or "").strip().lower() == "низкая":
            continue
        out[maker_key(r.get("maker"))] = dict(r, email=mails[0])
    return out


def maker_body(maker: str, items: list[dict]) -> str:
    """Письмо ИЗГОТОВИТЕЛЮ. Спрашивается другое, чем у продавца.

    Главный вопрос здесь — не цена, а расшифровка внутреннего номера в
    коммерческий: по обозначениям вида SP1xxxxx, CT9xxxx, RM13xxx у Siemens и
    по чертёжным позициям Bornemann в открытом доступе нет ни одной цены, и
    разведка это установила по шести перечням. Пока номер не переведён в
    изделие поставщика-изготовителя узла, цена недостижима ни у кого.
    """
    lines = [f"Добрый день!", "",
             f"Обращаемся как покупатель запасных частей {maker}.",
             "Просим по позициям ниже:", "",
             "1) назвать цену и срок поставки либо указать авторизованный канал, "
             "через который вы продаёте эти позиции;",
             "2) подтвердить, действующее ли это обозначение, и назвать замену, "
             "если позиция снята;",
             "3) если обозначение внутреннее, назвать соответствующий "
             "коммерческий номер изделия и его изготовителя — по внутренним "
             "обозначениям цену получить невозможно ни у одного продавца.", "",
             "Позиции:"]
    for i, it in enumerate(items, 1):
        qty = f" — {it['qty']} {it['unit']}" if it.get("qty") else ""
        lines.append(f"{i}. {it['pn']}{qty}")
    lines += ["", "С уважением,", "КВАНТ"]
    return "\n".join(lines)


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
        # Канал продавца, которым можно воспользоваться БЕЗ почты: ссылка или
        # телефон, прочитанные разбором. Берём как напечатано, не достраивая
        # домен и не подбирая почту по домену: правило репозитория «не выдавай
        # родовой адрес за адрес по детали» и запрет додумывать адрес.
        chan = f"{x.get('contacts') or ''} {x.get('channel') or ''}"
        url = SELLER_URL.search(chan)
        tel = SELLER_TEL.search(chan)
        item = {"pn": x.get("pn"), "qty": a.get("qty"), "unit": a.get("unit") or "шт",
                "maker": short_maker(x.get("maker_short"), a.get("man")),
                "our_exposure": round(expo(a), 2),
                "mail": mails[0] if mails else "",
                "seller_url": url.group(0).rstrip(".,;") if url else "",
                "seller_tel": tel.group(0).strip() if tel else "",
                "seller_note": str(x.get("contacts") or "")}
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
    # Строки без адреса продавца: если у изготовителя адрес известен и прочитан,
    # письмо идёт ему — с другим вопросом (см. maker_body).
    mc = maker_contacts()
    forms = maker_forms()
    to_maker: dict[str, list] = collections.defaultdict(list)
    to_form: dict[str, list] = collections.defaultdict(list)
    still_without = []
    for it in without:
        k = maker_key(it.get("maker"))
        rec = mc.get(k)
        if rec:
            to_maker[rec["email"]].append(dict(it, _maker_rec=rec))
            continue
        frec = forms.get(k)
        if frec:
            to_form[str(frec.get("form_url"))].append(dict(it, _maker_rec=frec))
            continue
        still_without.append(it)
    without = still_without
    # Пятый путь: у продавца нет почты, но разбор прочитал его страницу или
    # телефон. Такие строки лежали в графе «адреса нет» и не получали никакого
    # действия — 31 строка на 122 494 USD, по которым спросить было куда.
    # Отдельный путь, а не письмо: отправляет человек, и вопрос он задаёт
    # голосом или через чужую форму, поэтому текст тот же, а способ другой.
    to_seller_channel, no_address = [], []
    for it in without:
        if it.get("seller_url") or it.get("seller_tel"):
            to_seller_channel.append(it)
        else:
            no_address.append(it)
    to_seller_channel.sort(key=lambda z: -z["our_exposure"])
    def leak_mark(url: str) -> str:
        """Предупреждение, если страница принадлежит адресату, уже опубликовавшему
        нашу заявку. Для писем такая пометка есть с самого начала; для нового
        пути её сперва не было — и первое же задание послало сорсера на перечень
        именно такого домена (строка 1071411-1). Guard один и тот же, потому что
        риск один и тот же: обращение к нему — осознанное решение, а не рассылка.
        """
        m = re.search(r"https?://(?:www\.)?([^/]+)", str(url or ""))
        if not m:
            return ""
        dom = m.group(1).lower()
        who = next((v for k, v in leaks.items() if dom == k or dom.endswith("." + k)), "")
        return (f"ЭТОТ АДРЕСАТ УЖЕ ОПУБЛИКОВАЛ НАШУ ЗАЯВКУ в открытом доступе ({who}). "
                f"Обращение к нему — осознанное решение, а не рассылка." if who else "")

    seller_tasks = [{
        "pn": it["pn"], "qty": it.get("qty"), "unit": it.get("unit"),
        "maker": it.get("maker"),
        "our_exposure": it["our_exposure"],
        "page": it.get("seller_url"),
        "phone": it.get("seller_tel"),
        "warning": leak_mark(it.get("seller_url")),
        "how": ("открыть страницу продавца и задать пять вопросов через её форму"
                if it.get("seller_url") else "позвонить и задать пять вопросов"),
        # Адрес канала приводится ЦЕЛИКОМ, как он записан разбором: там же
        # стоят оговорки вроде «форма без выбора России» и «продавец класса, а
        # не по детали». Обрезка унесла бы ровно их.
        "what_the_analysis_read": it.get("seller_note"),
        "text": body([it]),
    } for it in to_seller_channel]
    without = no_address
    form_tasks = []
    for url, items in sorted(to_form.items(),
                             key=lambda kv: -sum(x["our_exposure"] for x in kv[1])):
        items.sort(key=lambda x: -x["our_exposure"])
        rec = items[0]["_maker_rec"]
        clean = [{k2: v for k2, v in it.items() if k2 != "_maker_rec"} for it in items]
        form_tasks.append({
            "maker": rec.get("maker"),
            "form_url": url,
            "phone": rec.get("phone"),
            "rows": len(clean),
            "our_exposure": round(sum(x["our_exposure"] for x in clean), 2),
            "pns": [x["pn"] for x in clean],
            "read_on": rec.get("read_on"),
            "text": maker_body(str(rec.get("maker")), clean),
        })
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
            "kind": "продавцу",
            "body": body(items),
        })
    for mail, items in sorted(to_maker.items(),
                              key=lambda kv: -sum(x["our_exposure"] for x in kv[1])):
        items.sort(key=lambda x: -x["our_exposure"])
        rec = items[0]["_maker_rec"]
        clean = [{k: v for k, v in it.items() if k != "_maker_rec"} for it in items]
        letters.append({
            "to": mail,
            "warning": "",
            "subject": f"Запрос по запасным частям {rec.get('maker')}: {len(clean)} позиц.",
            "rows": len(clean),
            "our_exposure": round(sum(x["our_exposure"] for x in clean), 2),
            "pns": [x["pn"] for x in clean],
            "kind": "изготовителю",
            "maker": rec.get("maker"),
            "address_read_on": rec.get("read_on"),
            "address_kind": rec.get("email_kind"),
            # Что известно про канал — целиком из разбора адреса, без разбора
            # прозы. Там живут ограничения, которые решают судьбу письма: у
            # Solar в обязательном поле формы 194 страны, и России среди них
            # нет. Сорсер обязан это видеть ДО отправки, а не после молчания.
            # БЕЗ ОБРЕЗКИ. Первая редакция резала на 1200 знаках — и отрезала
            # ровно решающее: у Solar в обязательном поле формы 194 страны, и
            # России среди них нет. Правило репозитория запрещает обрезку
            # текста вида x[:150] именно поэтому: обрезается всегда хвост, а
            # оговорка живёт в хвосте.
            "what_is_known_about_channel": str(rec.get("note") or ""),
            "confidence": rec.get("confidence"),
            "body": maker_body(str(rec.get("maker")), clean),
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
        # Итог считается как сумма ТРЁХ частей. Прежняя редакция брала
        # len(with_mail) + len(without) уже ПОСЛЕ того, как часть строк ушла в
        # письма изготовителям, и целое молча теряло эти строки: 267 + 50 + 205
        # против объявленных 472. Тест на сходимость это и поймал.
        "rows_total": (len(with_mail) + sum(len(v) for v in to_maker.values())
                       + sum(len(v) for v in to_form.values())
                       + len(to_seller_channel) + len(without)),
        "usd_total": round(sum(x["our_exposure"] for x in with_mail + without)
                           + sum(x["our_exposure"] for v in to_maker.values() for x in v)
                           + sum(x["our_exposure"] for v in to_form.values() for x in v)
                           + sum(x["our_exposure"] for x in to_seller_channel), 2),
        "rows_with_address": len(with_mail),
        "usd_with_address": round(sum(x["our_exposure"] for x in with_mail), 2),
        "rows_without_address": len(without),
        "usd_without_address": round(sum(x["our_exposure"] for x in without), 2),
        "how_rows_split": ("rows_total = rows_with_address + rows_to_maker + rows_to_form + "
                           "rows_to_seller_channel + rows_without_address. Пять путей: "
                           "письмо продавцу (цена и наличие), письмо изготовителю "
                           "(расшифровка номера и авторизованный канал), обращение через "
                           "форму изготовителя — тем же текстом, но вставляет его человек, "
                           "— обращение к продавцу без почты по прочитанной странице или "
                           "телефону, и остаток, по которому адресата нет вовсе."),
        "rows_to_seller_channel": len(to_seller_channel),
        "usd_to_seller_channel": round(sum(x["our_exposure"] for x in to_seller_channel), 2),
        "seller_tasks": seller_tasks,
        "why_seller_channel_is_not_nothing": (
            "Замер 18.09.2026: у 48 строк почты продавца нет, но у 25 из них разбор "
            "прочитал страницу, а ещё у 6 — телефон, всего на 122 494 USD. Пока они "
            "считались безадресными, отчёт называл по ним работой «найти адрес» — адрес "
            "при этом был уже найден и записан. Почта по домену не додумывается: "
            "спрашивают тем каналом, который прочитан."),
        "rows_to_form": sum(len(v) for v in to_form.values()),
        "usd_to_form": round(sum(x["our_exposure"] for v in to_form.values() for x in v), 2),
        "form_tasks": form_tasks,
        "why_form_is_not_nothing": ("Форма — не «адреса нет», а другой способ отправки. Среди "
                                    "таких изготовителей ABB (39 строк) и Rockwell "
                                    "(342 600 USD): отнести форму к безадресному остатку "
                                    "значило бы спрятать шестьсот тысяч в графу «работа не "
                                    "определена». Отправляет человек: посылать что-либо "
                                    "наружу от нашего имени агент сам не станет."),
        "rows_to_maker": sum(len(v) for v in to_maker.values()),
        "usd_to_maker": round(sum(x["our_exposure"] for v in to_maker.values() for x in v), 2),
        "what_maker_letter_asks": ("Изготовителю задаётся не цена, а расшифровка внутреннего "
                                  "обозначения в коммерческий номер: по SP1xxxxx, CT9xxxx, "
                                  "RM13xxx и чертёжным позициям в открытом доступе нет ни "
                                  "одной цены — это установлено по шести перечням. Пока номер "
                                  "не переведён, цена недостижима ни у одного продавца."),
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
        if L.get("what_is_known_about_channel"):
            a(f"<p class='dim'><b>Что известно про этот канал</b> (из разбора адреса, "
              f"уверенность «{E(L.get('confidence'))}»): "
              f"{E(L['what_is_known_about_channel'])}</p>")
        a(f"<p class='subj'><b>Тема:</b> {E(L['subject'])}</p>")
        a(f"<pre>{E(L['body'])}</pre>")
    if d.get("form_tasks"):
        a(f"<h2>Через форму обращения: {ru(d['rows_to_form'])} строк на "
          f"{ru(d['usd_to_form'])} долларов США</h2>")
        a(f"<p>{E(d['why_form_is_not_nothing'])}</p>")
        for i, t in enumerate(d["form_tasks"], 1):
            a(f"<p class='k'>{i}. {E(t['maker'])} — {ru(t['rows'])} позиц., "
              f"{ru(t['our_exposure'])} USD нашей экспозиции</p>")
            a(f"<p>Форма: {E(t['form_url'])}"
              + (f"<br>Телефон: {E(t['phone'])}" if t.get("phone") else "") + "</p>")
            a(f"<pre>{E(t['text'])}</pre>")
    if d.get("seller_tasks"):
        a(f"<h2>Продавец без почты — страница или телефон: {ru(d['rows_to_seller_channel'])} "
          f"строк на {ru(d['usd_to_seller_channel'])} долларов США</h2>")
        a(f"<p>{E(d['why_seller_channel_is_not_nothing'])}</p>")
        for i, t in enumerate(d["seller_tasks"], 1):
            a(f"<p class='k'>{i}. {E(t['pn'])}"
              + (f" — {ru(t['qty'])} {E(t['unit'])}" if t.get("qty") else "")
              + (f", изготовитель {E(t['maker'])}" if t.get("maker") else "")
              + f" — {ru(t['our_exposure'])} USD нашей экспозиции</p>")
            if t.get("warning"):
                a(f"<p class='warn'>{E(t['warning'])}</p>")
            a(f"<p>Как спрашивать: {E(t['how'])}."
              + (f"<br>Страница: {E(t['page'])}" if t.get("page") else "")
              + (f"<br>Телефон: {E(t['phone'])}" if t.get("phone") else "")
              + f"<br>Что прочитал разбор: {E(t['what_the_analysis_read'])}</p>")
            a(f"<pre>{E(t['text'])}</pre>")

    a("<h2>Что делать с этим документом</h2>")
    a("<p><b>1. Отправлять сверху вниз.</b> Порядок — по нашей экспозиции. Первые десять "
      "адресатов закрывают большую часть суммы, и каждый из них несёт по нескольку "
      "позиций: одно письмо закрывает не одну строку.</p>")
    a("<p><b>2. «Нет» — полноценный ответ.</b> Письмо прямо это говорит. Отказ снимает "
      "строку с канала и отправляет её искать другого продавца, а молчание держит её в "
      "работе месяцами.</p>")
    a("<p><b>3. Ответ записывать числом и с датой.</b> Цена без срока действия и остаток "
      "без даты в сумму закупки не идут — это правило уже стоило нам разбора.</p>")
    if not d["rows_without_address"]:
        # Ноль здесь — состояние фронта, и говорить его надо словами: «по 0 строкам
        # адреса нет — это следующая работа» читается как сбой счётчика.
        a("<p><b>4. Строк без адресата не осталось ни одной.</b> Каждая строка этого "
          "пакета получила, кому писать: продавцу с почтой, изготовителю, через форму "
          "изготовителя либо по прочитанной странице или телефону продавца. Часть адресов "
          "родовые — это сказано в самой строке, и обращение по родовому адресу начинается "
          "с вопроса «ведёте ли вы эту позицию», а не с запроса цены.</p>")
    if d["rows_without_address"]:
        a(f"<p><b>4. По {ru(d['rows_without_address'])} строкам на "
          f"{ru(d['usd_without_address'])} долларов США адреса нет — и это следующая "
          f"работа, а не пропуск.</b> Адрес ищется у изготовителя и в его сети "
          f"обслуживания. Проверять нашу базу поставщиков по словам названия бесполезно: "
          f"такая сверка уже дала заведомо ложные пары — фильтр Fleetguard к торговцу "
          f"программируемыми контроллерами, — то есть ровно «родовой адрес вместо адреса "
          f"по детали».</p>")
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
