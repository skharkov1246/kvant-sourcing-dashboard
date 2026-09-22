#!/usr/bin/env python3
"""Где теряются предложения поставщиков: воронка разбора по ФАЙЛАМ.

ЗАЧЕМ. Владелец, 22.09.2026: «предложения приходят с артикулированной чёткой
позицией, 99 % точно с номенклатурными позициями и отсылками к нашему запросу».
Замер это подтверждает с другой стороны: из предложений извлечено 404 420
позиций, а цена прочитана у 18 234 — четыре процента. Позиции читаются, цены
нет. Значит дело не в файлах, а в разборщике.

ЧТО СЧИТАЕТСЯ ЗДЕСЬ. Воронка по файлам, а не по строкам: строка без цены может
быть законной (заголовок раздела, итог), а вот ФАЙЛ коммерческого предложения,
не давший ни одной цены, — это непрочитанное предложение целиком.

ГЛАВНЫЙ РАЗРЕЗ — ПО ТОМУ, НАШЛАСЬ ЛИ ШАПКА ТАБЛИЦЫ. Ценовые колонки ищутся
только в строке заголовков (library/quotes.py, колонки_цены). Не опознали
шапку — позиции всё равно извлекутся запасным путём «самая длинная ячейка», а
цена не будет искаться вовсе. Если непрочитанные файлы собраны в клетке
«шапка не найдена», чинить надо опознание шапки, а не чтение цены.

Ничего не пишет. В журнал — только агрегаты (правило 17).

    SUPABASE_DB_URL=… python scripts/quote_parse_funnel.py
"""
from __future__ import annotations

import os

FEED = "разбор КП"

# Все файлы, которые видел разбор КП. Поле origin у них своё: вложения приходят
# из карточек запросов, и чужие файлы в эту таблицу не попадают.
ПО_СОСТОЯНИЮ = """
select status,
       count(*)                                        as файлов,
       sum(coalesce(rows_found, 0))                    as позиций,
       sum(coalesce(chars, 0))                         as знаков
  from lib_files
 group by status order by 2 desc
"""

# ЯДРО ЗАМЕРА. Разобранные файлы по пути разбора и по тому, нашлась ли шапка,
# и сколько из них дали хоть одну цену. Цена связана с файлом через source_url.
ПО_ШАПКЕ = """
with цены as (
  select source_url as file_id, count(*) as строк_цены
    from lib_prices where feed = %s and source_url is not null
   group by 1
)
select coalesce(f.parse_path, '(не указан)')            as путь,
       coalesce(f.header_found::text, '(не указано)')   as шапка,
       count(*)                                         as файлов,
       count(ц.file_id)                                 as дали_цену,
       sum(coalesce(f.rows_found, 0))                   as позиций,
       sum(coalesce(ц.строк_цены, 0))                   as строк_цены
  from lib_files f
  left join цены ц on ц.file_id = f.file_id
 where f.status = 'разобран'
 group by 1, 2 order by 3 desc
"""

# Файлы, давшие позиции, но ни одной цены. Это и есть непрочитанные предложения.
# Разрез по виду файла: если они все одного формата, чинить надо один путь.
НЕМЫЕ_ПО_ВИДУ = """
with цены as (
  select distinct source_url as file_id
    from lib_prices where feed = %s and source_url is not null
)
select coalesce(f.kind, '(не определён)')               as вид,
       count(*)                                         as файлов,
       sum(coalesce(f.rows_found, 0))                   as позиций,
       percentile_disc(0.5) within group (order by coalesce(f.rows_found, 0)) as медиана_позиций
  from lib_files f
  left join цены ц on ц.file_id = f.file_id
 where f.status = 'разобран' and coalesce(f.rows_found, 0) > 0 and ц.file_id is null
 group by 1 order by 2 desc
"""

# Сколько позиций на файл у тех, кто цену дал, против тех, кто не дал. Если у
# немых позиций столько же, файлы такие же — значит разборщик, а не файлы.
СРАВНЕНИЕ = """
with цены as (
  select distinct source_url as file_id
    from lib_prices where feed = %s and source_url is not null
)
select ц.file_id is not null                             as дал_цену,
       count(*)                                          as файлов,
       percentile_disc(0.5) within group (order by coalesce(f.rows_found, 0)) as медиана_позиций,
       percentile_disc(0.5) within group (order by coalesce(f.chars, 0))      as медиана_знаков,
       count(*) filter (where f.header_found)            as с_шапкой,
       count(*) filter (where f.parse_path = 'таблица')  as таблицей
  from lib_files f
  left join цены ц on ц.file_id = f.file_id
 where f.status = 'разобран' and coalesce(f.rows_found, 0) > 0
 group by 1 order by 1 desc
"""

# Сколько строк цены приходится на позицию у файлов, которые цену дали. Если
# и там доля мала, беда не только в шапке: цена не читается и там, где ищется.
ВНУТРИ_ПРОЧИТАННЫХ = """
with цены as (
  select source_url as file_id, count(*) as строк_цены
    from lib_prices where feed = %s and source_url is not null
   group by 1
)
select count(*)                                          as файлов,
       sum(coalesce(f.rows_found, 0))                    as позиций,
       sum(ц.строк_цены)                                 as строк_цены,
       percentile_disc(0.5) within group
         (order by ц.строк_цены::numeric
                   / nullif(coalesce(f.rows_found, 0), 0))  as медиана_доли
  from lib_files f
  join цены ц on ц.file_id = f.file_id
 where f.status = 'разобран' and coalesce(f.rows_found, 0) > 0
"""


def доля(часть, целое) -> str:
    часть = часть or 0
    return f"{часть}" + (f" ({100 * часть / целое:.1f} %)" if целое else "")


def main() -> int:
    dsn = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not dsn:
        print("нет SUPABASE_DB_URL — мерить нечего")
        return 2
    import psycopg2

    conn = psycopg2.connect(dsn, connect_timeout=20,
                            options="-c statement_timeout=300000")
    try:
        with conn.cursor() as cur:
            print("=== ВОРОНКА РАЗБОРА ПРЕДЛОЖЕНИЙ, ПО ФАЙЛАМ ===")
            cur.execute(ПО_СОСТОЯНИЮ)
            строки = cur.fetchall()
            всего = sum(r[1] for r in строки)
            print(f"файлов всего: {всего}")
            for статус, ф, поз, зн in строки:
                print(f"    {статус[:28]:28s} {доля(ф, всего):>16s}"
                      f" · позиций {поз or 0:>8d} · знаков {зн or 0:>10d}")

            print("\nРАЗОБРАННЫЕ ПО ПУТИ И ПО ШАПКЕ (цена ищется ТОЛЬКО в строке")
            print("заголовков: не опознали шапку — цена не ищется вовсе):")
            cur.execute(ПО_ШАПКЕ, (FEED,))
            for путь, шапка, ф, дали, поз, сц in cur.fetchall():
                print(f"    путь {путь:10s} шапка {шапка:12s} файлов {ф:>6d}"
                      f" · дали цену {доля(дали, ф):>16s}"
                      f" · позиций {поз or 0:>8d} · строк цены {сц or 0:>7d}")

            cur.execute(СРАВНЕНИЕ, (FEED,))
            строки = cur.fetchall()
            if строки:
                print("\nНЕМЫЕ ПРОТИВ ГОВОРЯЩИХ (если файлы похожи, дело в разборщике,")
                print("а не в файлах):")
                for дал, ф, мп, мз, шап, табл in строки:
                    имя = "дали цену" if дал else "НЕ дали цену"
                    print(f"    {имя:14s} файлов {ф:>6d} · медиана позиций {мп:>5d}"
                          f" · медиана знаков {мз:>7d} · с шапкой {доля(шап, ф):>15s}"
                          f" · таблицей {доля(табл, ф):>15s}")

            cur.execute(НЕМЫЕ_ПО_ВИДУ, (FEED,))
            строки = cur.fetchall()
            if строки:
                print("\nНЕМЫЕ ФАЙЛЫ ПО ВИДУ (позиции есть, цены нет ни одной):")
                for вид, ф, поз, мп in строки:
                    print(f"    {вид[:20]:20s} файлов {ф:>6d} · позиций {поз or 0:>8d}"
                          f" · медиана позиций на файл {мп:>5d}")

            cur.execute(ВНУТРИ_ПРОЧИТАННЫХ, (FEED,))
            ф, поз, сц, мд = cur.fetchone()
            if ф:
                print("\nВНУТРИ ФАЙЛОВ, ГДЕ ЦЕНА ВСЁ-ТАКИ ПРОЧИТАНА:")
                print(f"    файлов {ф} · позиций {поз} · строк цены {сц}"
                      f" · {доля(сц, поз)} позиций с ценой")
                print(f"    медиана доли позиций с ценой в файле: "
                      f"{float(мд or 0) * 100:.0f} %")
                print("    Если и здесь доля мала, шапка — не единственная беда:")
                print("    цена не читается и там, где её ищут.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
