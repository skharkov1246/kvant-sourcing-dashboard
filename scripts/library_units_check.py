#!/usr/bin/env python3
"""Точность разметки узлов: сверка правила с ручной разметкой инженеров.

ЗАЧЕМ. Правило отнесения позиции к узлу (library/equipment.py) размечает
двенадцать тысяч партномеров. Проверять его на глаз бессмысленно: выборка из
двадцати строк покажет что угодно. В pn_db 2 096 строк размечены инженерами
руками — это и есть эталон, на котором точность меряется, а не оценивается.

ЧТО СЧИТАЕТСЯ. Правило запускается ТОЛЬКО по описанию позиции, ручная метка при
этом скрыта и служит ответом. Считаются охват (на скольких правило вообще
ответило) и точность (сколько ответов совпало). Сравнение идёт по системе, а не
по компоненту: инженер пишет «горячий тракт», а правило может дойти до «жаровой
трубы» — это не ошибка.

    python scripts/library_units_check.py
    python scripts/library_units_check.py --min-precision 85   # для гейта

Код возврата 1, если точность ниже порога.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load():
    spec = importlib.util.spec_from_file_location(
        "kvant_equipment", ROOT / "library" / "equipment.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def корень(u: str | None) -> str | None:
    return (u or "").split(".")[0] or None


def main() -> int:
    ap = argparse.ArgumentParser(description="Точность разметки узлов по эталону инженеров")
    ap.add_argument("--min-precision", type=int, default=0,
                    help="ниже этой точности в процентах — код возврата 1")
    ap.add_argument("--min-coverage", type=int, default=0)
    args = ap.parse_args()

    eq = load()
    путь = ROOT / "gt" / "data" / "pn_db.json"
    if not путь.exists():
        print("нет gt/data/pn_db.json", file=sys.stderr)
        return 2
    rows = json.loads(путь.read_text(encoding="utf-8")).get("rows", [])

    эталон = верно = неверно = промолчало = 0
    путаница: Counter = Counter()
    for r in rows:
        метка = (r.get("seg") or "").strip()
        if not метка:
            continue
        ожидаемый = eq.unit_of_seg(метка)
        if ожидаемый is None:          # «Прочее / требует разметки» — не эталон
            continue
        эталон += 1
        ответ = корень(eq.unit_of(r.get("desc") or ""))
        ждали = корень(ожидаемый)
        if ответ is None:
            промолчало += 1
        elif ответ == ждали:
            верно += 1
        else:
            неверно += 1
            путаница[f"{ждали} → {ответ}"] += 1

    ответов = верно + неверно
    точность = верно / ответов * 100 if ответов else 0
    охват = ответов / эталон * 100 if эталон else 0
    print(f"эталонных строк (разметка инженеров): {эталон}")
    print(f"правило ответило:  {ответов:>5}   охват  {охват:.0f}%")
    print(f"   совпало:        {верно:>5}   точность {точность:.0f}%")
    print(f"   не совпало:     {неверно:>5}")
    print(f"правило промолчало:{промолчало:>5}")
    if путаница:
        print("\nкуда уходит ошибка:")
        for k, n in путаница.most_common(8):
            print(f"   {k:34}{n}")
    плохо = (args.min_precision and точность < args.min_precision) or \
            (args.min_coverage and охват < args.min_coverage)
    if плохо:
        print(f"\n✗ ниже порога (точность ≥ {args.min_precision}%, охват ≥ {args.min_coverage}%)",
              file=sys.stderr)
        return 1
    print("\n✓ разметка узлов в норме")
    return 0


if __name__ == "__main__":
    sys.exit(main())
