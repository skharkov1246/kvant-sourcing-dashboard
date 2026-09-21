#!/usr/bin/env python3
"""Кириллические буквы-двойники внутри латинского обозначения детали.

ЧТО ЭТО. В обозначении «1794-IВ10XOB6XT» буква «В» — КИРИЛЛИЧЕСКАЯ. На вид она
неотличима от латинской B, но это другой символ, и по такому номеру поиск не
находит ничего ни у одного продавца, ни в нашем собственном индексе. Сорсер
вводит номер, получает ноль и заключает, что детали не существует.

ОТКУДА БЕРЁТСЯ. Обозначение набирали в русской раскладке либо копировали из
документа, где часть символов уже была кириллической. Заметить это глазом
нельзя: начертание совпадает.

ПОЧЕМУ НАШ ИНДЕКС ЭТО НЕ ЛОВИЛ. Ключ поиска строился как «оставить только
A–Z и 0–9». Кириллическая буква под это правило не попадала и просто
ВЫБРАСЫВАЛАСЬ: «1794-IВ10XOB6XT» превращалось в «179410XOB6XT». Тот же номер в
правильном латинском написании давал «1794IB10XOB6XT» — другой ключ. То есть
номер лежал в индексе, но найти его по верному написанию было невозможно.
Исправлено сведением двойников к латинице ДО отсева символов, в
scripts/build_index.py и scripts/lookup.py.

ЗАМЕНА ДЕЛАЕТСЯ ТОЛЬКО В КЛЮЧЕ ПОИСКА. Само поле артикула в данных не
правится: там записано то, что прислал заказчик, и менять это — его право, а
не наше. Здесь список таких строк и считается.

    python gt/tools/homoglyphs.py [--write]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUMMARY = ROOT / "gt/data/ship_lukoil.json"
OUT = ROOT / "gt/data/ship_homoglyphs.json"

# Кириллические буквы, начертание которых совпадает с латинскими.
HOMOGLYPHS = {
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X",
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
}
CYR = re.compile("[А-Яа-яЁё]")
LAT = re.compile("[A-Za-z]")
DIG = re.compile(r"\d")


def fold(s: str) -> str:
    """Сводит кириллические двойники к латинице. Прочую кириллицу не трогает."""
    return "".join(HOMOGLYPHS.get(c, c) for c in str(s or ""))


def expo(r: dict) -> float:
    lo, hi, q = r.get("usd_lo"), r.get("usd_hi"), r.get("qty")
    if lo in (None, "") or hi in (None, "") or not q:
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(q)


def measure() -> dict:
    rows = json.loads(SUMMARY.read_text(encoding="utf-8"))["rows"]
    items = []
    for r in rows:
        pn = str(r.get("pn") or "")
        if not pn or not DIG.search(pn) or not LAT.search(pn):
            continue
        cyr = [c for c in pn if CYR.match(c)]
        # интересует только случай, когда ВСЯ кириллица в номере — двойники:
        # русское слово в поле артикула это другая беда, и она уже посчитана
        if not cyr or not all(c in HOMOGLYPHS for c in cyr):
            continue
        items.append({
            "pn": pn,
            "pn_latin": fold(pn),
            "letters": "".join(sorted(set(cyr))),
            "name": str(r.get("name") or "")[:120],
            "qty": r.get("qty"),
            "usd_exposure": round(expo(r), 2),
        })
    items.sort(key=lambda x: -x["usd_exposure"])
    return {
        "rows_checked": len(rows),
        "pns_with_homoglyphs": len(items),
        "usd_exposure": round(sum(x["usd_exposure"] for x in items), 2),
        "items": items,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    m = measure()
    print(f"строк заявки проверено {m['rows_checked']}, номеров с кириллическими "
          f"буквами-двойниками {m['pns_with_homoglyphs']} на "
          f"{m['usd_exposure']:,.0f} USD".replace(",", " "))
    for x in m["items"]:
        print(f"  {x['usd_exposure']:>8,.0f} | {x['pn']:<28} → {x['pn_latin']:<28} "
              f"буквы: {x['letters']}".replace(",", " "))
    if a.write:
        OUT.write_text(json.dumps({
            "updated": "2026-09-18",
            "source": "Разбор поля артикула сводки заявки. Считает gt/tools/homoglyphs.py.",
            "what_it_is": ("Номера, в которых кириллическая буква стоит на месте латинской и "
                           "неотличима от неё на вид. По такому номеру поиск не находит "
                           "ничего — ни у продавца, ни в нашем индексе."),
            "what_we_did": ("Ключ поиска в scripts/build_index.py и scripts/lookup.py теперь "
                            "сводит двойники к латинице ДО отсева символов, поэтому номер "
                            "находится и в том, и в другом написании. Само поле артикула в "
                            "данных НЕ правится: там записано то, что прислал заказчик."),
            **{k: v for k, v in m.items() if k != "items"},
            "items": m["items"],
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"замер записан в {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
