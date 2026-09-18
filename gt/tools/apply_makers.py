#!/usr/bin/env python3
"""Перенос вскрытого изготовителя из перепроверки в базу номеров.

ЗАЧЕМ. Звено «у кого спрашивать» читают не только документы по заявке: на
gt/data/pn_db.json стоят сборщики библиотеки (build_dict, build_node_map,
build_chain_coverage, library_units_check). Пока в нём остаётся опровергнутая
догадка, она расходится по всей библиотеке. Масляному фильтру Fleetguard там
приписаны четыре фильтровых дома «по типу», наконечнику свечи Jenbacher —
авиационный поставщик зажигания «типично», подшипнику RENK — «SKF / Timken /
RENK (по типу)».

ЧТО ПЕРЕНОСИТСЯ. Только явное короткое имя (поле maker_short в строке
перепроверки), то есть только там, где изготовителя назвал ВНЕШНИЙ каталог.
Проза поля real_maker не разбирается: на разборе прозы замер уже ошибся в обе
стороны. Вместе с именем пишется основание «П0 — каталог изготовителя,
перепроверка» — оно сильнее всех прежних кодов и именно так читается замером.

    python gt/tools/apply_makers.py             # показать, что изменится
    python gt/tools/apply_makers.py --write     # применить
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "gt/data/pn_db.json"
REVERIFY = ROOT / "gt/data/ship_reverify.json"
EV = "П0 — каталог изготовителя, перепроверка 18.09.2026"


def key(pn) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(pn or "").split("(")[0].upper())


def makers() -> dict[str, str]:
    doc = json.loads(REVERIFY.read_text(encoding="utf-8"))
    out = {}
    for r in (doc.get("rows") or []):
        mk = str(r.get("maker_short") or "").strip()
        if mk:
            out[key(r.get("pn"))] = mk
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    if not DB.exists() or not REVERIFY.exists():
        print("нет базы номеров или набора перепроверки", file=sys.stderr)
        return 1

    mk = makers()
    doc = json.loads(DB.read_text(encoding="utf-8"))
    changed, same, shown = 0, 0, 0
    for r in doc["rows"]:
        name = mk.get(key(r.get("pn")))
        if not name:
            continue
        if r.get("mk") == name and r.get("ev") == EV:
            same += 1
            continue
        if shown < 25:
            print(f"  {str(r.get('pn'))[:24]:24} «{str(r.get('mk'))[:34]}» → «{name[:34]}»")
            shown += 1
        r["mk"] = name
        r["ev"] = EV
        changed += 1
    print(f"строк базы к правке: {changed}, уже верных: {same}, "
          f"номеров с вскрытым изготовителем: {len(mk)}")
    if a.write and changed:
        DB.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"записано в {DB.relative_to(ROOT)}")
    elif not a.write:
        print("это холостой прогон: чтобы применить, добавьте --write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
