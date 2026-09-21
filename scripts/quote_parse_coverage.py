#!/usr/bin/env python3
"""Сколько котировок уже разобрано: замер по базе, без обращения к порталу.

ЗАЧЕМ. Замер 21.09.2026 показал, что цены на карточке запроса нет вовсе: сумма
равна нулю у всех 21 865 карточек, а КП живёт вложением — файл поставщика есть у
5 231 карточки. Значит хранение котировки строится от разбора файлов. Прежде чем
его строить, надо знать, что разбор УЖЕ дал: сколько этих файлов он видел,
сколько прочитал и сколько строк из них достал.

ПОЧЕМУ ПО БАЗЕ, А НЕ ПО ПОРТАЛУ. lib_files хранит имя UF-поля, из которого взят
файл, его статус и число найденных позиций. Всё нужное там уже есть, и лишний
проход по порталу (около пяти минут) не нужен.

НАШ ФАЙЛ ЗАПРОСА СЧИТАЕТСЯ ОТДЕЛЬНО. «Request file» — то, что отправили мы;
зачесть его в котировки значит объявить прокотированным собственный запрос
(та же ошибка, что ловилась в brand_suppliers).

В журнал идут только агрегаты (правило 17).

    SUPABASE_DB_URL=… python scripts/quote_parse_coverage.py
"""
from __future__ import annotations

import collections
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from quote_coverage import ПОЛЕ_ЗАПРОСА, ПОЛЯ_КП  # noqa: E402

SQL = """
select field, status, count(*), sum(coalesce(rows_found, 0)), sum(coalesce(chars, 0))
  from lib_files
 where field = any(%s)
 group by field, status
"""


def доля(часть: int, целое: int) -> str:
    return f"{часть}/{целое}" + (f" ({100 * часть / целое:.0f} %)" if целое else "")


def свод(строки) -> None:
    """Строки (field, status, файлов, позиций, символов) → сводка по видам."""
    по_полю: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    позиций: dict[str, int] = collections.defaultdict(int)
    for поле, статус, файлов, строк, _символов in строки:
        по_полю[поле][статус or "без статуса"] += файлов
        позиций[поле] += строк or 0

    кп_полей = [п for п in по_полю if п in ПОЛЯ_КП]
    всего_кп = sum(sum(c.values()) for п, c in по_полю.items() if п in ПОЛЯ_КП)
    разобрано_кп = sum(c.get("разобран", 0) for п, c in по_полю.items() if п in ПОЛЯ_КП)
    позиций_кп = sum(v for п, v in позиций.items() if п in ПОЛЯ_КП)

    print("ФАЙЛЫ КОТИРОВОК, КОТОРЫЕ РАЗБОР УЖЕ ВИДЕЛ")
    print(f"  всего в lib_files:    {всего_кп}")
    print(f"  из них разобрано:     {доля(разобрано_кп, всего_кп)}")
    print(f"  позиций получено:     {позиций_кп}")
    if разобрано_кп:
        print(f"  позиций на файл:      {позиций_кп / разобрано_кп:.1f} в среднем")
    print()

    print("ПО ПОЛЯМ И СТАТУСАМ")
    for поле in sorted(кп_полей, key=lambda p: -sum(по_полю[p].values())):
        имя = ПОЛЯ_КП[поле]
        print(f"  {имя} — файлов {sum(по_полю[поле].values())}, позиций {позиций[поле]}")
        for статус, n in по_полю[поле].most_common():
            print(f"      {статус:26} {n}")
    print()

    наш = по_полю.get(ПОЛЕ_ЗАПРОСА)
    if наш:
        print("НАШ «Request file» — для сравнения, в котировки НЕ идёт")
        print(f"  файлов {sum(наш.values())}, позиций {позиций[ПОЛЕ_ЗАПРОСА]}")
        for статус, n in наш.most_common():
            print(f"      {статус:26} {n}")
        print()

    print("Замер по базе, записи не было.")


def main() -> int:
    dsn = os.environ.get("SUPABASE_DB_URL", "")
    if not dsn:
        print("нет переменной SUPABASE_DB_URL", file=sys.stderr)
        return 2
    import psycopg2

    conn = psycopg2.connect(dsn, connect_timeout=20,
                            options="-c statement_timeout=120000")
    try:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cur:
            cur.execute(SQL, (list(ПОЛЯ_КП) + [ПОЛЕ_ЗАПРОСА],))
            строки = cur.fetchall()
    finally:
        conn.close()
    if not строки:
        print("В lib_files нет ни одного файла из полей КП.")
        print("Это значит, что разбор вложений до карточек запросов ещё не доходил,")
        print("а не что файлов нет: в портале их 5 231 (замер 21.09.2026).")
        return 0
    свод(строки)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
