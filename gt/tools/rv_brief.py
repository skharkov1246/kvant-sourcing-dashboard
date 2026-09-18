#!/usr/bin/env python3
"""Задание для разведки собирается ИЗ ДАННЫХ, а не печатается руками.

ОПЛАЧЕНО ДВАЖДЫ. Первый раз 18.09.2026 утром: в задание ушли три строки с
наименованиями и вилками, которых в данных нет, — я переписал их по памяти.
Второй раз в тот же день: из двенадцати строк задания одиннадцать несли
НЕВЕРНУЮ вилку. Числа я перенёс глазами из собственной консольной выдачи, и все
одиннадцать оказались уже настоящих: 40–69 вместо 14–95, 45–185 вместо 30–200,
200–650 против напечатанных 150–700. Разведка считала вердикты против вилок,
которых в данных нет.

Уцелело по счастливой случайности: вердикты по цене вышли только у двух строк
из двенадцати, и у одной из них вилка совпала. Будь наоборот — в набор легли бы
ложные «занижена» и «завышена», и поймать их было бы нечем: проверка сверяет
вердикт с настоящей вилкой, но лишь там, где цена найдена.

ПРАВИЛО. Задание печатает этот инструмент, читая gt/data/ship_lukoil.json.
Руками в задание пишется только то, чего в данных нет: на что обратить
внимание, какие ловушки уже известны, что проверить особо.

    python gt/tools/rv_brief.py 346328041 180431-1 ... > задание.txt
    python gt/tools/rv_brief.py --top 20            # верх неразобранного по деньгам
    python gt/tools/rv_brief.py --maker Siemens --top 15
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUMMARY = ROOT / "gt/data/ship_lukoil.json"
REVERIFY = ROOT / "gt/data/ship_reverify.json"
INSIDE = ROOT / "gt/data/ship_inside_quotes.json"
PRICED = ROOT / "gt/data/ship_inside_priced.json"
QUESTIONS = ROOT / "gt/data/ship_questions.json"


def closed_elsewhere() -> tuple[set, set]:
    """Номера, которые закрываются НЕ поиском: наше вложение и вопрос заказчику.

    Добавлено 18.09.2026. Из 475 неразобранных строк по 303 цена лежит в
    предложении, которое нам уже прислали, а по девяти вопрос заказчику
    поставлен. Отправлять разведку искать такую цену в открытом доступе — значит
    списать её труд в ноль: тот же урок уже был получен на задании по добору
    цены, где четыре строки из двенадцати вернулись ответом «цена в присланном
    предложении, опись его давно разобрала».
    """
    # ИСПРАВЛЕНО 18.09.2026, второй заход. Набор ship_inside_quotes даёт АДРЕС
    # цены — файл, в котором есть хоть одна цена и есть наш номер, — а не цену по
    # нашей строке. Вычитая адрес, задание прятало 178 строк, по которым работы
    # ровно столько же, сколько по любой неразобранной: их адрес указывает на
    # Quotation p76057, где 316 значений и все до одного $0.00, либо на нашу же
    # рабочую таблицу. Вычитать можно только подтверждённую письменную цену —
    # её хранит ship_inside_priced (собирает gt/tools/tkp_tables.py --keys-out).
    inside, asked = set(), set()
    if PRICED.exists():
        inside = {key(r.get("pn"))
                  for r in json.loads(PRICED.read_text(encoding="utf-8")).get("parts", [])}
    elif INSIDE.exists():
        inside = {key(r.get("pn"))
                  for r in json.loads(INSIDE.read_text(encoding="utf-8")).get("rows", [])}
    if QUESTIONS.exists():
        q = json.loads(QUESTIONS.read_text(encoding="utf-8"))
        asked = {key(r.get("pn")) for r in (q.get("rows") or q.get("questions") or [])
                 if isinstance(r, dict) and r.get("pn")}
    return inside, asked


def key(x) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x or "").split("(")[0].upper())


def expo(r: dict) -> float:
    lo, hi, q = r.get("usd_lo"), r.get("usd_hi"), r.get("qty")
    if lo in (None, "") or hi in (None, "") or not q:
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(q)


def brief(rows: list[dict]) -> str:
    out = []
    for i, r in enumerate(rows, 1):
        lo, hi = r.get("usd_lo"), r.get("usd_hi")
        band = "вилки нет" if lo in (None, "") else f"{lo}–{hi} USD за штуку"
        out.append(
            f"{i}. НОМЕР (посимвольно как в заявке): {r.get('pn')}\n"
            f"   наименование в заявке дословно: {r.get('name')}\n"
            f"   количество: {r.get('qty')} {r.get('unit') or ''}\n"
            f"   наша вилка: {band}\n"
            f"   экспозиция по середине вилки: {expo(r):,.0f} USD\n".replace(",", " ") +
            f"   изготовитель по заявке: {r.get('man')}\n"
            f"   машина по заявке: {r.get('model')}\n"
            f"   лист: {r.get('sheet')}   категория: {r.get('cat')}\n"
            f"   прежний вердикт проверки: {r.get('verdict')}"
            f"{'   продавец: ' + str(r.get('seller')) if r.get('seller') else ''}")
    head = (f"СТРОКИ ЗАЯВКИ ({len(rows)}). Все числа и наименования ниже взяты из "
            f"gt/data/ship_lukoil.json инструментом gt/tools/rv_brief.py и НЕ переписаны "
            f"руками. Ни одно из них не меняй по памяти: если найденное противоречит "
            f"написанному здесь, так и напиши в разборе.\n")
    return head + "\n" + "\n\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pns", nargs="*", help="номера строк заявки")
    ap.add_argument("--top", type=int, default=0,
                    help="взять верх НЕРАЗОБРАННОГО остатка по деньгам")
    ap.add_argument("--maker", default="", help="ограничить изготовителем (по началу имени)")
    ap.add_argument("--sheet", default="", help="ограничить листом заявки")
    a = ap.parse_args()

    rows = json.loads(SUMMARY.read_text(encoding="utf-8"))["rows"]
    if a.pns:
        by = {key(r.get("pn")): r for r in rows}
        sel, missing = [], []
        for pn in a.pns:
            r = by.get(key(pn))
            (sel.append(r) if r else missing.append(pn))
        if missing:
            print(f"НЕТ В СВОДКЕ ЗАЯВКИ: {', '.join(missing)}", file=sys.stderr)
            return 1
    else:
        done = {key(r.get("pn")) for r in json.loads(REVERIFY.read_text(encoding="utf-8"))["rows"]}
        # Строка БЕЗ артикула в задание не идёт, и это не придирка. Ключ по
        # такой строке пуст, поэтому набор перепроверки её не примет НИКОГДА:
        # приёмник справедливо отбивает строку с пустым номером. Раз принять
        # нельзя, то и разбирать её разведкой бессмысленно — она возвращалась
        # бы в верх остатка после каждого прогона и съедала разведку заново.
        # Такие строки закрываются вопросом заказчику, а не поиском, и вопрос
        # по ним уже поставлен. Сколько их — печатается числом, чтобы отсев не
        # был молчаливым.
        keyless = [r for r in rows if not key(r.get("pn")) and expo(r) > 0]
        inside, asked = closed_elsewhere()
        sel = [r for r in rows
               if key(r.get("pn")) and key(r.get("pn")) not in done and expo(r) > 0
               and key(r.get("pn")) not in inside and key(r.get("pn")) not in asked
               and str(r.get("man") or "").startswith(a.maker)
               and (not a.sheet or r.get("sheet") == a.sheet)]
        off = [r for r in rows
               if key(r.get("pn")) and key(r.get("pn")) not in done and expo(r) > 0
               and (key(r.get("pn")) in inside or key(r.get("pn")) in asked)]
        if off:
            print(f"ОТСЕЯНО КАК ЗАКРЫТОЕ НЕ ПОИСКОМ: {len(off)} строк на "
                  f"{sum(map(expo, off)):,.0f} USD — по ним цена лежит в присланном нам "
                  f"предложении либо вопрос заказчику уже поставлен. Искать их в открытом "
                  f"доступе значит списать труд разведки в ноль.".replace(",", " "),
                  file=sys.stderr)
        sel.sort(key=expo, reverse=True)
        sel = sel[:a.top or 20]
        if keyless:
            print(f"ОТСЕЯНО БЕЗ АРТИКУЛА: {len(keyless)} строк на "
                  f"{sum(map(expo, keyless)):,.0f} USD — у них в поле артикула нет ни одной "
                  f"буквы и ни одной цифры, поэтому набор перепроверки их не примет и "
                  f"разбирать их разведкой бесполезно. Они закрываются вопросом заказчику: "
                  f"{', '.join(str(r.get('pn')) for r in keyless[:5])}".replace(",", " "),
                  file=sys.stderr)
    print(brief(sel))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
