#!/usr/bin/env python3
"""Письма на ПОДТВЕРЖДЕНИЕ ОСТАТКА: продавец назвал число, но верить ему нельзя.

ЗАЧЕМ ОТДЕЛЬНЫЙ ПАКЕТ. Письма из gt/tools/quote_letters.py спрашивают цену там,
где её нет. Здесь другой случай: цена и число есть, а числу нельзя верить, и
спросить надо ровно то, чего не хватает.

ЧТО ЭТО ЗА СЛУЧАЙ, ИЗМЕРЕНО 18.09.2026. На сток-лист одного перепродавца
опираются 152 строки заявки, и его «Npcs» остатком не является:
  338 из 437 строк листа несут ровно 5, 10 или 100 pcs — три круглые ступени;
  на странице SHOP того же оператора количества некруглые (44, 46, 51, 1069,
    3264) — это положительный контроль: так у него выглядит настоящий счёт;
  65 строк несут количество при цене «auf Anfrage» — количество без цены
    складом быть не может;
  даты нет ни в тексте, ни в заголовке Last-Modified, снимков в веб-архиве нет
    вовсе — возраст листа непроверяем;
  один номер напечатан трижды с разными количествами и ценами (1023031-1:
    10 шт по 1 400, 10 шт по 2 100, 30 шт по 1 050 EUR).
Живой склад не несёт трёх цен и двух количеств на один номер. Это брекет партии.

ПОЧЕМУ ЭТО ПИСЬМО, А НЕ РАЗВЕДКА. Страница открыта, прочитана и измерена —
добавить к ней нечего. Недостающее знает только продавец: остаток числом на
дату, покрытие нашего количества и какая из спорящих записей действующая.

Письмо спрашивает четыре вещи и ничего больше. Наша вилка, наша сумма и имя
заказчика в него не идут — это наша коммерческая информация.

    python gt/tools/stock_letters.py
    python gt/tools/stock_letters.py --write
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASK = ROOT / "gt/data/ship_lukoil.json"
RV = ROOT / "gt/data/ship_reverify.json"
OUT = ROOT / "gt/data/ship_stock_letters.json"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ship_coverage import STOCK_DENY_RE, stock_state  # noqa: E402

MAIL = re.compile(r"[a-z0-9][a-z0-9._%+-]*@[a-z0-9.-]+\.[a-z]{2,}", re.I)
# Что НИКОГДА не должно попасть в письмо продавцу: наша вилка, наша экспозиция,
# имя заказчика и имена его листов. Замер 18.09.2026: одно письмо из 76 унесло
# их, потому что «что указано» цитировалось из нашей же прозы, а она пишется для
# нас, а не для контрагента.
FORBIDDEN = re.compile(r"вилк|экспозиц|ЛУКОЙЛ|лукойл|Энергосети|энергосети|НВН"
                       r"|наша цена|выставлен|заказчик", re.I)
# Из нашей прозы в письмо идёт ТОЛЬКО заявленное продавцом количество.
SEEN_QTY = re.compile(r"(\d[\d\s]{0,6})\s*(шт|pcs|pce|pieces|ea\b|штук)", re.I)


def key(x) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x or "").split("(")[0].upper())


def expo(r: dict) -> float:
    lo, hi, q = r.get("usd_lo"), r.get("usd_hi"), r.get("qty")
    if lo in (None, "") or hi in (None, "") or not q:
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(q)


def addressee(x: dict) -> tuple[str, str]:
    """Почта продавца из полей строки. Берётся ПЕРВАЯ — она же основной канал.

    Домен не додумывается: если почты в строке нет, письмо не собирается, и
    строка уходит в отдельный счёт «адресата нет».

    Возвращается и ПОЛЕ, из которого адрес взят. Почта из «контактов» относится к
    продавцу по этой детали; почта, выловленная из описания канала или из
    источника цены, может оказаться родовым адресом фирмы — а «этот поставщик
    работает по такому классу» и «у него есть эта деталь» разные утверждения
    (правило из разбора выкладки 12.09.2026). Письмо с таким адресом помечается.
    """
    for f in ("contacts", "channel", "price_source"):
        m = MAIL.search(str(x.get(f) or ""))
        if m:
            return m.group(0).lower(), f
    return "", ""


def body(items: list[dict]) -> str:
    lines = ["Добрый день!", "",
             "По позициям ниже у нас есть ваша цена и указанное количество, но "
             "подтверждения остатка нет. Просим ответить по каждой позиции на четыре "
             "вопроса:", "",
             "1) остаток на складе ЧИСЛОМ на сегодняшнюю дату;",
             "2) закрывает ли он указанное нами количество целиком, и если нет — "
             "сколько закрывает и каким сроком добирается остальное;",
             "3) на какую дату составлен ваш перечень остатков;",
             "4) если по позиции в перечне несколько записей с разными количествами "
             "или ценами — какая из них действующая.", "",
             "Позиции:"]
    for i, it in enumerate(items, 1):
        q = f" — требуется {it['qty']} шт" if it.get("qty") else ""
        seen = (f"; в ваших данных по этой позиции указано {it['seen']}"
                if it.get("seen") else "")
        lines.append(f"{i}. {it['pn']}{q}{seen}")
    lines += ["", "Если позиции на складе нет, достаточно одного слова «нет» — это тоже "
                  "ответ, и он для нас так же полезен, как число.", "",
              "С уважением,", "КВАНТ"]
    return "\n".join(lines)


def build() -> dict:
    ask = json.loads(ASK.read_text(encoding="utf-8"))["rows"]
    rv = {key(r.get("pn")): r for r in json.loads(RV.read_text(encoding="utf-8"))["rows"]}
    by_mail: dict[str, list] = defaultdict(list)
    homeless: list[dict] = []
    for a in ask:
        x = rv.get(key(a.get("pn")))
        if not x:
            continue
        st = str(x.get("stock") or "")
        # ОТБОР ШИРЕ, ЧЕМ РАЗРЯД «СПОРНО». Спрашивать надо у всякого продавца,
        # который НАПЕЧАТАЛ ЧИСЛО, а мы это число остатком не считаем: и там, где
        # поле само себе противоречит («спорно»), и там, где разбор его уже
        # отверг («нет» при числе в тексте) — брекет партии, срок поставки, число
        # по соседнему исполнению. Замер 18.09.2026: «спорно» даёт 81 строку на
        # 218 044 USD, а весь разряд «число есть, остатком не считается» — 302
        # строки на 4 337 937 USD, и вопрос к продавцу по ним ровно один и тот же.
        if not re.search(r"\d", st) or stock_state(st) == "числом":
            continue
        # ПРОДАВЦУ ВОЗВРАЩАЕТСЯ ТОЛЬКО ЕГО СОБСТВЕННАЯ ЦИФРА, а не наша проза.
        # Поле остатка — наша внутренняя запись: там встречаются и наша вилка, и
        # имя заказчика, и слово «выставлено». Поэтому из него берётся ровно одно:
        # заявленное количество. Не нашлось — в письме про него ничего не будет.
        # Цифра берётся ТОЛЬКО из первой фразы и только если та фраза ничего не
        # отрицает. Иначе в письмо уходит число ЧУЖОЙ позиции: «По -10 —
        # ничего. По соседнему -200 напечатано 3 pcs» превратилось бы в «в ваших
        # данных по -10 указано 3 pcs», и продавец стал бы отвечать не о том.
        first = re.split(r"(?<=[.;])\s", st, maxsplit=1)[0]
        m = SEEN_QTY.search(first)
        seen = (f"{m.group(1).strip()} {m.group(2)}"
                if m and not STOCK_DENY_RE.search(first) else "")
        it = {"pn": a.get("pn"), "qty": a.get("qty"), "seen": seen, "expo": expo(a),
              "state": stock_state(st)}
        mail, field = addressee(x)
        it["mail_from"] = field
        (by_mail[mail] if mail else homeless).append(it)
    letters = []
    leaked: list[str] = []
    disputed = sum(1 for items in list(by_mail.values()) + [homeless]
                   for i in items if i["state"] == "спорно")
    for mail, items in sorted(by_mail.items(), key=lambda kv: -sum(i["expo"] for i in kv[1])):
        items.sort(key=lambda i: -i["expo"])
        letters.append({
            "to": mail,
            "subject": (f"Подтверждение остатка: {len(items)} позиц. "
                        f"({', '.join(str(i['pn']) for i in items[:3])}"
                        f"{' и др.' if len(items) > 3 else ''})"),
            "rows": len(items),
            "our_exposure": round(sum(i["expo"] for i in items), 2),
            "pns": [i["pn"] for i in items],
            "rows_disputed": sum(1 for i in items if i["state"] == "спорно"),
            "address_check": ("" if all(i["mail_from"] == "contacts" for i in items) else
                              "адрес выловлен из описания канала или источника цены, а не из "
                              "контактов по детали: проверить получателя перед отправкой"),
            "body": body(items),
        })
        # СТРАЖ, А НЕ НАДЕЖДА. Письмо с нашей вилкой или именем заказчика не
        # уходит вовсе: такая ошибка стоит дороже пропущенного письма.
        if FORBIDDEN.search(letters[-1]["body"]):
            leaked.append(mail)
            letters.pop()
    return {
        "updated": "2026-09-18",
        "source": ("Письма на подтверждение остатка: продавец назвал число, а остатком оно "
                   "не считается, и подтверждения нет. Считает "
                   "gt/tools/stock_letters.py по gt/data/ship_lukoil.json и "
                   "gt/data/ship_reverify.json; руками не заполнять."),
        "what_is_not_in_the_letter": ("Наша вилка, наша сумма экспозиции и имя заказчика. "
                                      "Письмо спрашивает четыре вещи и ничего больше."),
        "why_these_rows": ("Строки, где продавец НАПЕЧАТАЛ ЧИСЛО, а мы это число остатком не "
                           "считаем: брекет партии в недатированном листе, срок поставки, "
                           "число по соседнему исполнению, колонка QTY заводской ведомости. "
                           "Страницы по ним открыты и измерены, добавить к ним нечего — "
                           "недостающее знает только продавец. Как читается остаток и семь "
                           "ловушек подмены — docs/ПРАВИЛА-ОСТАТКА.md."),
        "rows_total": sum(len(letter["pns"]) for letter in letters) + len(homeless),
        "rows_disputed": disputed,
        "letters_withheld_for_leak": leaked,
        "rows_with_address": sum(len(letter["pns"]) for letter in letters),
        "rows_without_address": len(homeless),
        "usd_with_address": round(sum(letter["our_exposure"] for letter in letters), 2),
        "usd_without_address": round(sum(i["expo"] for i in homeless), 2),
        "letters": letters,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    d = build()
    print(f"строк, где число напечатано, но остатком не считается: {d['rows_total']} "
          f"(из них поле само себе противоречит у {d['rows_disputed']}) · с адресом продавца "
          f"{d['rows_with_address']} на {d['usd_with_address']:,.0f} USD · без адреса "
          f"{d['rows_without_address']} на {d['usd_without_address']:,.0f} USD"
          .replace(",", " "))
    print(f"писем: {len(d['letters'])}")
    if d["letters_withheld_for_leak"]:
        print(f"  НЕ СОБРАНО из-за нашей информации в тексте: "
              f"{', '.join(d['letters_withheld_for_leak'])}")
    for letter in d["letters"][:12]:
        mark = "  ⚠ адрес проверить" if letter["address_check"] else ""
        print(f"  {letter['to']:<34} {letter['rows']:>3} позиц. | "
              f"{letter['our_exposure']:>10,.0f} USD{mark}".replace(",", " "))
    if a.write:
        OUT.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"записано в {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
