#!/usr/bin/env python3
"""Где теряются предложения поставщиков: воронка разбора по ФАЙЛАМ.

ЗАЧЕМ. Владелец, 22.09.2026: «предложения приходят с артикулированной чёткой
позицией, 99 % точно с номенклатурными позициями и отсылками к нашему запросу».
Из предложений извлечено много позиций, а цена прочитана у считанных процентов.
Позиции читаются, цены нет — значит дело не в файлах, а в разборщике. Замер
обязан сказать, в каком именно месте разборщика.

ПЕРВЫЙ РАЗРЕЗ — ЧЕЙ ЭТО ФАЙЛ. В lib_files лежат две разные вещи: вложения
карточек сделок (origin = «поле сделки») — это спецификации заказчика, наш
спрос; и вложения карточек запросов (origin = «поле запроса») — это предложения
поставщиков. Цена бывает только во вторых. Первая версия скрипта считала все
файлы подряд и выдала 31 865 файлов и 1,95 млн позиций — это вся база спроса, а
не предложения. Без разреза по origin любая цифра воронки бессмысленна.

ВТОРОЙ РАЗРЕЗ — КАКИМ КОДОМ ФАЙЛ РАЗОБРАН. parser_version и header_found
заполняет только текущий разборщик. Файл, разобранный до появления записи цен,
цены не дал не потому, что разборщик её не понял, а потому что тогда её никто не
искал. Такому файлу нужен переразбор (library/reparse.py), а не правка правил.
Смешивать эти две группы нельзя: вместе они дают «разборщик плох» там, где
верно «код до файла не доходил».

ТРЕТИЙ РАЗРЕЗ — ПУТЬ РАЗБОРА И ШАПКА. Ценовые колонки ищутся ТОЛЬКО в строке
заголовков таблицы (library/indexer.py: `цк = quotes.колонки_цены(rows[hi]) if
hi >= 0 else {}`). Отсюда два следствия, которые замер обязан подтвердить или
опровергнуть числом:
  · путь «текст» (а это весь pdf) цену не ищет вообще — ни одной строки;
  · путь «таблица» без опознанной шапки — тоже не ищет.
Если немые файлы собраны в этих двух клетках, чинить надо не чтение цены, а то,
что до чтения цены дело не доходит.

Ничего не пишет. В журнал — только агрегаты (правило 17).

    SUPABASE_DB_URL=… python scripts/quote_parse_funnel.py
"""
from __future__ import annotations

import os

FEED = "разбор КП"
ПРЕДЛОЖЕНИЕ = "поле запроса"

# Чьи файлы вообще лежат в базе. Без этой таблицы не видно, что предложения —
# меньшая часть lib_files, и не с чем сверить остальные разрезы.
ПО_ПРОИСХОЖДЕНИЮ = """
select coalesce(origin, '(не указано)')                 as откуда,
       count(*)::bigint                                 as файлов,
       sum(coalesce(rows_found, 0))::bigint             as позиций
  from lib_files
 group by 1 order by 2 desc
"""

# Состояние файлов предложений: сколько вообще дошло до разбора.
ПО_СОСТОЯНИЮ = """
select status,
       count(*)::bigint                                 as файлов,
       sum(coalesce(rows_found, 0))::bigint             as позиций,
       sum(coalesce(chars, 0))::bigint                  as знаков
  from lib_files
 where origin = %s
 group by 1 order by 2 desc
"""

# ЯДРО ЗАМЕРА. Только предложения, только разобранные. Три оси: каким кодом
# разобран, каким путём, нашлась ли шапка. Цена связана с файлом через
# source_url. Считаются ФАЙЛЫ: строка без цены бывает законной (заголовок
# раздела, итог), а файл КП без единой цены — непрочитанное предложение целиком.
# ВЕДОМОСТЬ ПОТЕРЬ ПО ВИДУ ФАЙЛА, НА ВСЮ БАЗУ. Цель владельца — читать 99,9 %
# файлов: каждый непрочитанный файл это потерянные деньги. Мерить эту цель по
# одним предложениям нельзя — в сделках файлов втрое больше, и вид у них другой.
#
# «Не дал ни одной позиции» — вот отказ, который считается. Файл, давший позиции
# без цены, недочитан, но не потерян; файл с нулём позиций потерян целиком.
# Вид файла у нескачанных не определён (sniff до байтов не дошёл) — такие идут
# отдельной строкой, а не растворяются в прочих.
ПОТЕРИ_ПО_ВИДУ = """
select coalesce(kind, '(вид не определён)')                        as вид,
       count(*)::bigint                                            as файлов,
       count(*) filter (where coalesce(rows_found, 0) = 0)::bigint  as без_позиций,
       count(*) filter (where status = 'не скачался')::bigint       as не_скачался,
       count(*) filter (where status = 'формат не читаем')::bigint  as не_читаем,
       count(*) filter (where status = 'пусто')::bigint             as пусто,
       count(*) filter (where status = 'текст без спецификации')::bigint as без_спец,
       sum(coalesce(rows_found, 0))::bigint                         as позиций
  from lib_files
 group by 1 order by 3 desc, 2 desc
"""

# ПРИЧИНЫ НЕЧИТАЕМОСТИ ПОИМЁННО. `reason` у этого статуса хранит ТИП исключения
# (имя класса), а не текст с данными, — печатать его можно (правило 17).
ПРИЧИНЫ_ОТКАЗА = """
select coalesce(kind, '(вид не определён)')  as вид,
       coalesce(reason, '(без причины)')     as причина,
       count(*)::bigint                      as файлов
  from lib_files
 where status in ('формат не читаем', 'не скачался', 'пусто')
 group by 1, 2 order by 3 desc limit 40
"""

ЯДРО = """
with цены as (
  select source_url as file_id, count(*)::bigint as строк_цены
    from lib_prices where feed = %s and source_url is not null
   group by 1
)
select case when f.parser_version is null then 'до записи цен'
            else 'v' || f.parser_version::text end     as код,
       coalesce(f.parse_path, '(не указан)')            as путь,
       case when f.header_found is null then '(нет данных)'
            when f.header_found then 'найдена'
            else 'НЕ найдена' end                       as шапка,
       count(*)::bigint                                 as файлов,
       count(ц.file_id)::bigint                         as дали_цену,
       sum(coalesce(f.rows_found, 0))::bigint           as позиций,
       sum(coalesce(ц.строк_цены, 0))::bigint           as строк_цены
  from lib_files f
  left join цены ц on ц.file_id = f.file_id
 where f.origin = %s and f.status = 'разобран'
 group by 1, 2, 3 order by 4 desc
"""

# Немые предложения по виду файла. Если они одного формата, чинить надо один путь.
НЕМЫЕ_ПО_ВИДУ = """
with цены as (
  select distinct source_url as file_id
    from lib_prices where feed = %s and source_url is not null
)
select coalesce(f.kind, '(не определён)')               as вид,
       count(*)::bigint                                 as файлов,
       sum(coalesce(f.rows_found, 0))::bigint           as позиций,
       percentile_disc(0.5) within group
         (order by coalesce(f.rows_found, 0))            as медиана_позиций
  from lib_files f
  left join цены ц on ц.file_id = f.file_id
 where f.origin = %s and f.status = 'разобран'
   and coalesce(f.rows_found, 0) > 0 and ц.file_id is null
 group by 1 order by 2 desc
"""

# Немые против говорящих среди предложений: похожи ли файлы. Если да — дело в
# разборщике, а не в том, что немые файлы какие-то особенные.
СРАВНЕНИЕ = """
with цены as (
  select distinct source_url as file_id
    from lib_prices where feed = %s and source_url is not null
)
select ц.file_id is not null                             as дал_цену,
       count(*)::bigint                                  as файлов,
       percentile_disc(0.5) within group
         (order by coalesce(f.rows_found, 0))             as медиана_позиций,
       percentile_disc(0.5) within group
         (order by coalesce(f.chars, 0))                  as медиана_знаков,
       count(*) filter (where f.header_found)::bigint     as с_шапкой,
       count(*) filter (where f.parse_path = 'таблица')::bigint as таблицей,
       count(*) filter (where f.kind = 'pdf')::bigint     as пдф
  from lib_files f
  left join цены ц on ц.file_id = f.file_id
 where f.origin = %s and f.status = 'разобран'
   and coalesce(f.rows_found, 0) > 0
 group by 1 order by 1 desc
"""

# Сколько позиций с ценой внутри файлов, которые цену всё-таки дали. Если и там
# доля мала, шапка — не единственная беда: цена не читается и там, где ищется.
ВНУТРИ_ПРОЧИТАННЫХ = """
with цены as (
  select source_url as file_id, count(*)::bigint as строк_цены
    from lib_prices where feed = %s and source_url is not null
   group by 1
)
select count(*)::bigint                                  as файлов,
       sum(coalesce(f.rows_found, 0))::bigint            as позиций,
       sum(ц.строк_цены)::bigint                         as строк_цены,
       percentile_disc(0.5) within group
         (order by ц.строк_цены::numeric
                   / nullif(coalesce(f.rows_found, 0), 0)) as медиана_доли
  from lib_files f
  join цены ц on ц.file_id = f.file_id
 where f.origin = %s and f.status = 'разобран'
   and coalesce(f.rows_found, 0) > 0
"""


def доля(часть, целое) -> str:
    """Число со долей. Всё приводится к int: sum(bigint) в PostgreSQL даёт
    numeric, psycopg2 отдаёт Decimal, а формат `d` к Decimal не применяется и
    падает с «invalid format string» — уже уронило прогон 35753524371."""
    часть = int(часть or 0)
    целое = int(целое or 0)
    return f"{часть}" + (f" ({100 * часть / целое:.1f} %)" if целое else "")


def ц(x) -> int:
    return int(x or 0)


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

            cur.execute(ПО_ПРОИСХОЖДЕНИЮ)
            строки = cur.fetchall()
            всего = sum(ц(r[1]) for r in строки)
            print(f"\nЧЬИ ФАЙЛЫ В БАЗЕ (всего {всего}). Цена бывает только у")
            print(f"вложений запросов — «{ПРЕДЛОЖЕНИЕ}»:")
            for откуда, ф, поз in строки:
                print(f"    {откуда[:20]:20s} {доля(ф, всего):>16s}"
                      f" · позиций {ц(поз):>9d}")

            cur.execute(ПО_СОСТОЯНИЮ, (ПРЕДЛОЖЕНИЕ,))
            строки = cur.fetchall()
            предложений = sum(ц(r[1]) for r in строки)
            print(f"\nСОСТОЯНИЕ ФАЙЛОВ ПРЕДЛОЖЕНИЙ (всего {предложений}):")
            for статус, ф, поз, зн in строки:
                print(f"    {статус[:28]:28s} {доля(ф, предложений):>16s}"
                      f" · позиций {ц(поз):>8d} · знаков {ц(зн):>10d}")

            # ВЕДОМОСТЬ ПОТЕРЬ — ПЕРВОЙ, ПОТОМУ ЧТО ЭТО ЦЕЛЬ. Остальное — про
            # качество чтения, а это про то, прочитан файл вообще или нет.
            cur.execute(ПОТЕРИ_ПО_ВИДУ)
            потери = cur.fetchall()
            всего_ф = sum(ц(r[1]) for r in потери)
            всего_б = sum(ц(r[2]) for r in потери)
            print(f"\nПОТЕРИ ПО ВИДУ ФАЙЛА, ВСЯ БАЗА ({ц(всего_ф)} файлов)."
                  f" Не дали ни одной позиции: {ц(всего_б)}"
                  f" ({100 * всего_б / max(всего_ф, 1):.1f} %)")
            print("    вид файла              файлов  без позиций   доля"
                  "   не скачался  не читаем   пусто  без спец.")
            for вид, ф, без, нс, нч, пу, бс, поз in потери:
                print(f"    {вид[:20]:20s} {ц(ф):>8d} {ц(без):>12d}"
                      f" {100 * ц(без) / max(ц(ф), 1):6.1f} %"
                      f" {ц(нс):>12d} {ц(нч):>10d} {ц(пу):>7d} {ц(бс):>9d}")
            print(f"    до цели 99,9 % осталось вернуть: {ц(max(0, всего_б - всего_ф // 1000))}"
                  f" файлов из {ц(всего_б)}")

            cur.execute(ПРИЧИНЫ_ОТКАЗА)
            причины = cur.fetchall()
            if причины:
                print("\nПОЧЕМУ ФАЙЛ НЕ ПРОЧИТАН (вид · причина · файлов):")
                for вид, причина, ф in причины:
                    print(f"    {вид[:20]:20s} {причина[:46]:46s} {ц(ф):>7d}")

            print("\nЯДРО: КАКИМ КОДОМ · КАКИМ ПУТЁМ · НАШЛАСЬ ЛИ ШАПКА.")
            print("Цена ищется ТОЛЬКО в строке заголовков таблицы: путь «текст»")
            print("(это весь pdf) и таблица без шапки цену не ищут вовсе.")
            cur.execute(ЯДРО, (FEED, ПРЕДЛОЖЕНИЕ))
            ядро = cur.fetchall()
            for код, путь, шапка, ф, дали, поз, сц in ядро:
                print(f"    {код:14s} {путь:10s} шапка {шапка:12s}"
                      f" файлов {ц(ф):>6d} · дали цену {доля(дали, ф):>15s}"
                      f" · позиций {ц(поз):>8d} · строк цены {ц(сц):>7d}")
            if ядро:
                ф_в = sum(ц(r[3]) for r in ядро)
                д_в = sum(ц(r[4]) for r in ядро)
                п_в = sum(ц(r[5]) for r in ядро)
                с_в = sum(ц(r[6]) for r in ядро)
                print(f"    {'ИТОГО':14s} {'':10s} {'':18s}"
                      f" файлов {ф_в:>6d} · дали цену {доля(д_в, ф_в):>15s}"
                      f" · позиций {п_в:>8d} · строк цены {с_в:>7d}")
                # Где цена не ищется по устройству кода, а не по качеству файла.
                глухо = [r for r in ядро
                         if r[1] == "текст" or r[2] in ("НЕ найдена", "(нет данных)")]
                print(f"\n    в клетках, где цена НЕ ИЩЕТСЯ по устройству кода: "
                      f"файлов {sum(ц(r[3]) for r in глухо)}, "
                      f"позиций {sum(ц(r[5]) for r in глухо)}")

            cur.execute(СРАВНЕНИЕ, (FEED, ПРЕДЛОЖЕНИЕ))
            строки = cur.fetchall()
            if строки:
                print("\nНЕМЫЕ ПРОТИВ ГОВОРЯЩИХ (если файлы похожи, дело в разборщике):")
                for дал, ф, мп, мз, шап, табл, пдф in строки:
                    имя = "дали цену" if дал else "НЕ дали цену"
                    print(f"    {имя:14s} файлов {ц(ф):>6d}"
                          f" · медиана позиций {ц(мп):>5d}"
                          f" · медиана знаков {ц(мз):>7d}"
                          f" · с шапкой {доля(шап, ф):>14s}"
                          f" · таблицей {доля(табл, ф):>14s}"
                          f" · pdf {доля(пдф, ф):>14s}")

            cur.execute(НЕМЫЕ_ПО_ВИДУ, (FEED, ПРЕДЛОЖЕНИЕ))
            строки = cur.fetchall()
            if строки:
                print("\nНЕМЫЕ ПРЕДЛОЖЕНИЯ ПО ВИДУ ФАЙЛА (позиции есть, цены нет):")
                for вид, ф, поз, мп in строки:
                    print(f"    {вид[:20]:20s} файлов {ц(ф):>6d}"
                          f" · позиций {ц(поз):>8d}"
                          f" · медиана позиций на файл {ц(мп):>5d}")

            cur.execute(ВНУТРИ_ПРОЧИТАННЫХ, (FEED, ПРЕДЛОЖЕНИЕ))
            ф, поз, сц, мд = cur.fetchone()
            if ц(ф):
                print("\nВНУТРИ ФАЙЛОВ, ГДЕ ЦЕНА ВСЁ-ТАКИ ПРОЧИТАНА:")
                print(f"    файлов {ц(ф)} · позиций {ц(поз)} · строк цены {ц(сц)}"
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
