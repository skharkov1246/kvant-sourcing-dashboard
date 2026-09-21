#!/usr/bin/env python3
"""Что дал разбор КП в ценах: замер по базе, без обращения к порталу.

ЗАЧЕМ. Разбор котировок затевался ради цены, и мерить его надо ценой, а не
числом разобранных файлов. Позиция без цены — это спрос, он у нас и так есть;
новое здесь только цена, валюта, срок и то, кто их назвал.

ЧТО СЧИТАЕТСЯ ДОСТИЖЕНИЕМ. Не «строк с числом», а строк, ПРИГОДНЫХ К РЕШЕНИЮ:
цена + валюта + поставщик. Без валюты число несравнимо, без поставщика адресата
у него нет. Поэтому счётчики идут воронкой, от общего к пригодному, а не одним
бодрым итогом.

ОТДЕЛЬНО — ДОЛЯ ВЫВЕДЕННЫХ ДЕЛЕНИЕМ. Такая цена получена из суммы строки, и
если её доля велика, значит разборщик чаще видит «Сумму», чем «Цену за ед.», —
это повод править колонки, а не радоваться охвату (CLAUDE.md: рядом с
производным числом обязана стоять оговорка).

В журнал идут только агрегаты: коды валют, счётчики, свои же константы
(правило 17). Ни наименований, ни номеров карточек, ни компаний.

    SUPABASE_DB_URL=… python scripts/quote_price_coverage.py
"""
from __future__ import annotations

import os
import sys

FEED = "разбор КП"

ВОРОНКА = """
select
  count(*)                                                        as всего,
  count(*) filter (where price is not null and price > 0)         as с_ценой,
  count(*) filter (where currency is not null)                    as с_валютой,
  count(*) filter (where rfq_company is not null)                 as с_поставщиком,
  count(*) filter (where lead_days is not null)                   as со_сроком,
  count(*) filter (where part_number is not null
                     and length(part_number) > 0)                 as с_артикулом,
  count(*) filter (where confidence = 'low')                      as низкой_уверенности,
  count(*) filter (where note ilike '%%делением%%')               as выведено_делением,
  count(distinct rfq_id)                                          as карточек,
  count(distinct rfq_company)                                     as компаний,
  count(distinct source_url)                                      as файлов
from lib_prices
where feed = %s
"""

ПО_ВАЛЮТАМ = """
select coalesce(currency, '(не названа)'), count(*)
  from lib_prices where feed = %s group by 1 order by 2 desc
"""

# Медиана по валюте: среднее по ценам разных порядков бессмысленно, а медиана
# показывает, на что похожи числа. Разброс — чтобы видеть, что попало мусора.
РАЗМАХ = """
select currency, count(*),
       percentile_disc(0.5) within group (order by price),
       min(price), max(price)
  from lib_prices
 where feed = %s and price is not null and currency is not null
 group by currency having count(*) >= 20 order by 2 desc
"""

ПРИГОДНЫЕ = """
select count(*) from lib_prices
 where feed = %s and price > 0 and currency is not null and rfq_company is not null
"""


def доля(часть: int, целое: int) -> str:
    return f"{часть}" + (f" ({100 * часть / целое:.0f} %)" if целое else "")


def main() -> int:
    dsn = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not dsn:
        print("нет SUPABASE_DB_URL — мерить нечего")
        return 2
    import psycopg2

    conn = psycopg2.connect(dsn, connect_timeout=20,
                            options="-c statement_timeout=120000")
    try:
        with conn.cursor() as cur:
            cur.execute(ВОРОНКА, (FEED,))
            (всего, с_ценой, с_валютой, с_поставщиком, со_сроком, с_артикулом,
             низкой, делением, карточек, компаний, файлов) = cur.fetchone()

            if not всего:
                print("строк из разбора КП в lib_prices нет — прогон ещё не шёл")
                return 0

            print(f"=== ЦЕНЫ ИЗ РАЗБОРА КП (feed «{FEED}») ===")
            print(f"строк:            {всего}")
            print(f"  с ценой:        {доля(с_ценой, всего)}")
            print(f"  с валютой:      {доля(с_валютой, всего)}")
            print(f"  с поставщиком:  {доля(с_поставщиком, всего)}")
            print(f"  со сроком:      {доля(со_сроком, всего)}")
            print(f"  с артикулом:    {доля(с_артикулом, всего)}")
            print(f"из них выведено делением суммы: {доля(делением, всего)}")
            print(f"низкой уверенности:             {доля(низкой, всего)}")
            print(f"источников: файлов {файлов}, карточек {карточек}, компаний {компаний}")

            cur.execute(ПРИГОДНЫЕ, (FEED,))
            пригодных = cur.fetchone()[0]
            print(f"\nПРИГОДНЫХ К РЕШЕНИЮ (цена + валюта + поставщик): "
                  f"{доля(пригодных, всего)}")

            cur.execute(ПО_ВАЛЮТАМ, (FEED,))
            print("\nпо валютам:")
            for код, n in cur.fetchall():
                print(f"    {код:14s} {n:>8d}")

            cur.execute(РАЗМАХ, (FEED,))
            строки = cur.fetchall()
            if строки:
                print("\nразмах цен (валюты от 20 строк; медиана, а не среднее —")
                print("цены разных порядков средним не описываются):")
                for вал, n, med, lo, hi in строки:
                    print(f"    {вал:5s} n={n:<7d} медиана {med:>12,.2f}"
                          f"   от {lo:,.2f} до {hi:,.2f}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
