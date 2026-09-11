#!/usr/bin/env python3
"""Сводка по базе знаний: сколько чего накоплено. Только агрегаты.

ЗАЧЕМ. Ответ на вопрос «насколько выросла база» до этого собирался разбором
журналов прогонов: цифры приходилось складывать вручную по двенадцати частям
индексатора, а часть показателей (сколько позиций осталось без сегмента, сколько
разных парт-номеров, наполняются ли lib_suppliers и lib_prices) из журналов не
видна вовсе. Этот скрипт спрашивает у базы напрямую.

ЧТО ПЕЧАТАЕТ. Только count, count(distinct) и названия сегментов. Ни одного
наименования позиции, ни одной почты, ни одного куска текста заметки: журнал
прогона в публичном репозитории видит кто угодно, а в базе лежат коммерческие
данные заказчиков. Ровно по этой причине запросы ниже написаны так, что вернуть
содержимое строки они физически не могут.

    SUPABASE_DB_URL=... python scripts/library_stats.py
    Actions → «Библиотека — сводка и переклассификация» → job: stats
"""
from __future__ import annotations

import os
import sys

# Таблицы библиотеки и правок. Порядок — от самых наполненных к служебным.
TABLES = [
    ("lib_demand", "спрос: позиции спецификаций из файлов сделок"),
    ("lib_files", "реестр разобранных вложений Битрикса"),
    ("lib_knowledge", "статьи: устройство, подбор, отказы, аналоги"),
    ("lib_suppliers", "поставщики по сегментам"),
    ("lib_prices", "цены с базисом и источником"),
    ("lib_losses", "разбор проигранных сделок"),
    ("lib_segments", "справочник сегментов"),
    ("gt_notes", "правки инженеров в библиотеке ГТУ"),
]

DEMAND_SQL = """
select count(*)                                              as всего,
       count(segment_id)                                     as с_сегментом,
       count(*) - count(segment_id)                          as без_сегмента,
       count(distinct nullif(btrim(part_number), ''))        as парт_номеров,
       count(distinct nullif(btrim(oem), ''))                as изготовителей,
       count(distinct nullif(btrim(deal_id), ''))            as сделок,
       count(distinct nullif(btrim(source_file), ''))        as файлов_источников
from lib_demand"""

BY_SEGMENT_SQL = """
select coalesce(d.segment_id, '—') as sid, count(*) as n
from lib_demand d group by 1 order by 2 desc"""

FILES_SQL = "select status, count(*), coalesce(sum(rows_found), 0) from lib_files group by 1 order by 2 desc"
KINDS_SQL = "select coalesce(kind, '—'), count(*) from lib_files group by 1 order by 2 desc"

# Правки инженеров: сколько их, сколько авторов, по каким страницам.
# Текст заметок и почты авторов не запрашиваются — только счётчики.
NOTES_SQL = """
select count(*)                        as всего,
       count(*) filter (where removed) as снятых,
       count(distinct author)          as авторов,
       count(distinct scope)           as страниц
from gt_notes"""
NOTES_BY_SCOPE_SQL = "select scope, count(*) from gt_notes where not removed group by 1 order by 2 desc"


def table_exists(cur, name: str) -> bool:
    cur.execute("select to_regclass(%s) is not null", (f"public.{name}",))
    return bool(cur.fetchone()[0])


def one(cur, sql: str):
    cur.execute(sql)
    return cur.fetchone()


def rows(cur, sql: str):
    cur.execute(sql)
    return cur.fetchall()


def block(head: str) -> None:
    print(f"\n=== {head} ===", flush=True)


def num(value, width: int = 12) -> str:
    """Число с неразрывным пробелом в разряде тысяч.

    Форматируем именно число, а не готовую строку: замена запятой во всей строке
    съедала запятые в пояснениях («устройство, подбор, отказы» превращалось в
    «устройство  подбор  отказы»)."""
    return f"{value:,}".replace(",", " ").rjust(width)


def main() -> int:
    url = os.environ.get("SUPABASE_DB_URL", "")
    if not url:
        print("нет переменной SUPABASE_DB_URL", file=sys.stderr)
        return 2
    import psycopg2

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "library"))
    from segments import name_of

    conn = psycopg2.connect(url, connect_timeout=20)
    conn.autocommit = True
    with conn.cursor() as cur:
        block("объём базы")
        print(f"{'таблица':16}{'строк':>12}  что внутри")
        present = {}
        for name, note in TABLES:
            if not table_exists(cur, name):
                print(f"{name:16}{'нет таблицы':>12}  {note}")
                continue
            n = one(cur, f"select count(*) from {name}")[0]
            present[name] = n
            print(f"{name:16}{num(n)}  {note}")

        if present.get("lib_demand"):
            block("спрос: что именно у нас спрашивали")
            cols = ("всего позиций", "с сегментом", "без сегмента",
                    "разных парт-номеров", "разных изготовителей", "сделок", "файлов-источников")
            for label, value in zip(cols, one(cur, DEMAND_SQL)):
                print(f"  {label:24}{num(value)}")
            total = present["lib_demand"]

            block("спрос по сегментам")
            print(f"  {'сегмент':34}{'позиций':>12}{'доля':>9}")
            for sid, n in rows(cur, BY_SEGMENT_SQL):
                nm = name_of(None if sid == "—" else sid)
                print(f"  {nm:34}{num(n)}{n / total * 100:>8.1f}%")

        if present.get("lib_files"):
            block("вложения Битрикса")
            print(f"  {'состояние':28}{'файлов':>10}{'позиций из них':>16}")
            for status, n, found in rows(cur, FILES_SQL):
                print(f"  {status or '—':28}{num(n, 10)}{num(found, 16)}")
            print(f"  {'':28}{'':>10}")
            print(f"  {'формат':28}{'файлов':>10}")
            for kind, n in rows(cur, KINDS_SQL):
                print(f"  {kind:28}{num(n, 10)}")

        if "gt_notes" in present:
            block("правки инженеров ГТУ")
            for label, value in zip(("всего правок", "снятых", "авторов", "страниц"), one(cur, NOTES_SQL)):
                print(f"  {label:24}{num(value)}")
            for scope, n in rows(cur, NOTES_BY_SCOPE_SQL):
                print(f"    страница {scope:20}{num(n, 8)}")
    conn.close()
    print("\n✓ сводка снята")
    return 0


if __name__ == "__main__":
    sys.exit(main())
