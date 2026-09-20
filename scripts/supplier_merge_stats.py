#!/usr/bin/env python3
"""Замер перед сведением поставщиков: во что обойдётся смена ключа. Только агрегаты.

ЗАЧЕМ. Сегодня поставщики агрегируются по ТЕКСТУ названия (base/suppliers.py,
supplier TEXT PRIMARY KEY), хотя стабильный идентификатор компании портала лежит
в той же таблице — rfq.supplier_id. Перевод ключа написать легко, а вот его
последствия зависят целиком от данных, и правило 3 CLAUDE.md запрещает применять
правило до замера. Этот скрипт отвечает на четыре вопроса, без которых перевод
делать нельзя:

  1. Сколько карточек с пустым supplier_id. Они схлопнутся в одну корзину, если
     не задать обратный ход на нормализованное имя.
  2. Сколько написаний названия приходится на один companyId. Это и есть выигрыш
     от перевода: во столько раз сократится число «поставщиков».
  3. Сколько написаний, наоборот, разделены между РАЗНЫМИ companyId. Опасный
     случай: одно имя — две компании, и слияние по имени их бы склеило.
  4. Насколько имена из соседних таблиц (цены, карточки документов) вообще
     находятся в rfq. От этого зависит, разорвёт ли перевод джойны base/quote.py.

ЧТО ПЕЧАТАЕТ. Только count, count(distinct) и распределения — ни одного названия
компании, ни одной почты, ни одного номера сделки. Репозиторий публичный, журнал
прогона в нём видит кто угодно (CLAUDE.md, правило 17). Запросы написаны так, что
вернуть содержимое строки они не могут: наружу выходят исключительно числа.

Читает выгрузку базы знаний в Supabase (kb_rfq, kb_suppliers, kb_brand_suppliers,
kb_supplier_prices) — локальная base/kvant.db не нужна и в репозиторий не входит.

    SUPABASE_DB_URL=... python scripts/supplier_merge_stats.py
    Actions → «Поставщики — замер сведения»
"""
from __future__ import annotations

import os
import sys

# Нормализация названия для обратного хода: тот же смысл, что у норм-функций
# library/load_suppliers.py:53 и pnw/tools/build_suppliers.py:38 — регистр вниз,
# схлопнутые пробелы. Здесь она живёт в SQL, чтобы замер считался одним проходом.
NORM = "lower(regexp_replace(btrim(coalesce(supplier, '')), '\\s+', ' ', 'g'))"

ОБЩЕЕ = f"""
select count(*)                                             as карточек,
       count(*) filter (where coalesce(supplier, '') <> '') as с_названием,
       count(*) filter (where coalesce(supplier_id, 0) > 0) as с_идентификатором,
       count(*) filter (where coalesce(supplier_id, 0) = 0
                          and coalesce(supplier, '') <> '') as имя_без_идентификатора,
       count(*) filter (where coalesce(supplier_id, 0) = 0
                          and coalesce(supplier, '') = '')  as ни_того_ни_другого,
       count(distinct nullif(btrim(coalesce(supplier, '')), ''))        as разных_написаний,
       count(distinct nullif(coalesce(supplier_id, 0), 0))              as разных_компаний,
       count(distinct nullif({NORM}, ''))                               as разных_норм_имён
from kb_rfq
"""

# Сколько написаний на одну компанию: распределение, а не список.
НАПИСАНИЙ_НА_КОМПАНИЮ = f"""
with по_компании as (
  select supplier_id, count(distinct nullif({NORM}, '')) as n
  from kb_rfq
  where coalesce(supplier_id, 0) > 0
  group by supplier_id
)
select case when n >= 4 then '4 и больше' else n::text end as написаний,
       count(*) as компаний
from по_компании group by 1 order by 1
"""

# Обратный, опасный случай: одно написание у разных компаний.
КОМПАНИЙ_НА_НАПИСАНИЕ = f"""
with по_имени as (
  select {NORM} as имя, count(distinct supplier_id) as n
  from kb_rfq
  where coalesce(supplier_id, 0) > 0 and coalesce(supplier, '') <> ''
  group by 1
)
select case when n >= 3 then '3 и больше' else n::text end as компаний_у_имени,
       count(*) as написаний
from по_имени group by 1 order by 1
"""

# Во что превратится число строк supplier_stats после перевода ключа.
ЭФФЕКТ = f"""
select (select count(distinct nullif(btrim(coalesce(supplier, '')), '')) from kb_rfq)
         as строк_сейчас_по_тексту,
       (select count(*) from (
          select distinct case when coalesce(supplier_id, 0) > 0
                               then 'id:' || supplier_id
                               else 'nm:' || {NORM} end as ключ
          from kb_rfq
          where coalesce(supplier, '') <> '' or coalesce(supplier_id, 0) > 0
        ) t) as строк_после_перевода
"""

# Джойны base/quote.py идут по тексту имени. Сколько имён соседних таблиц
# вообще встречается в kb_rfq — столько джойн и переживёт перевод.
СОСЕДИ = f"""
with имена as (select distinct {NORM} as имя from kb_rfq where coalesce(supplier,'') <> '')
select 'kb_brand_suppliers' as таблица,
       count(distinct lower(regexp_replace(btrim(supplier), '\\s+', ' ', 'g'))) as имён,
       count(distinct lower(regexp_replace(btrim(supplier), '\\s+', ' ', 'g')))
         filter (where lower(regexp_replace(btrim(supplier), '\\s+', ' ', 'g'))
                       in (select имя from имена))                              as нашлось_в_rfq
from kb_brand_suppliers where coalesce(supplier, '') <> ''
union all
select 'kb_suppliers',
       count(distinct lower(regexp_replace(btrim(supplier), '\\s+', ' ', 'g'))),
       count(distinct lower(regexp_replace(btrim(supplier), '\\s+', ' ', 'g')))
         filter (where lower(regexp_replace(btrim(supplier), '\\s+', ' ', 'g'))
                       in (select имя from имена))
from kb_suppliers where coalesce(supplier, '') <> ''
"""


def таблица(cur, sql: str) -> tuple[list[str], list[tuple]]:
    cur.execute(sql)
    return [d[0] for d in cur.description], cur.fetchall()


def печать(заголовок: str, cols: list[str], rows: list[tuple]) -> None:
    print(f"\n{заголовок}")
    ширина = [max(len(str(c)), *(len(str(r[i])) for r in rows)) if rows else len(str(c))
              for i, c in enumerate(cols)]
    print("  " + "  ".join(str(c).ljust(ширина[i]) for i, c in enumerate(cols)))
    for r in rows:
        print("  " + "  ".join(str(v).ljust(ширина[i]) for i, v in enumerate(r)))


def main() -> int:
    url = os.environ.get("SUPABASE_DB_URL", "")
    if not url:
        print("нет переменной SUPABASE_DB_URL", file=sys.stderr)
        return 2
    import psycopg2

    # Таймаут задаётся в строке подключения, а не через SET: SET внутри
    # транзакции откатывается вместе с ней (CLAUDE.md, правило 9).
    sep = "&" if "?" in url else "?"
    conn = psycopg2.connect(f"{url}{sep}options=-c%20statement_timeout%3D120000",
                            connect_timeout=20)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("select to_regclass('public.kb_rfq')")
        if cur.fetchone()[0] is None:
            print("таблицы kb_rfq в базе нет: сначала base/export_kb.py и base/load_kb.py",
                  file=sys.stderr)
            return 3

        печать("ЗАПРОСЫ ПОСТАВЩИКАМ: чем вообще опознаётся поставщик",
               *таблица(cur, ОБЩЕЕ))
        печать("НАПИСАНИЙ НАЗВАНИЯ НА ОДНУ КОМПАНИЮ (выигрыш от перевода ключа)",
               *таблица(cur, НАПИСАНИЙ_НА_КОМПАНИЮ))
        печать("КОМПАНИЙ НА ОДНО НАПИСАНИЕ (опасный случай: слияние по имени склеит разные)",
               *таблица(cur, КОМПАНИЙ_НА_НАПИСАНИЕ))
        печать("ЭФФЕКТ ПЕРЕВОДА: сколько строк станет в supplier_stats",
               *таблица(cur, ЭФФЕКТ))
        печать("СОСЕДНИЕ ТАБЛИЦЫ: сколько их имён находится в запросах (джойны quote.py)",
               *таблица(cur, СОСЕДИ))

    print("\nЧитать так:")
    print("  • «имя_без_идентификатора» > 0 — обратный ход на имя обязателен, иначе")
    print("    эти карточки схлопнутся в одну корзину;")
    print("  • «написаний на компанию» 2 и больше — во столько раз сегодня расщеплена")
    print("    статистика этих поставщиков;")
    print("  • «компаний у имени» 2 и больше — эти имена сливать по тексту НЕЛЬЗЯ;")
    print("  • разница «нашлось_в_rfq» и «имён» — джойны quote.py, которые перевод")
    print("    разорвёт, если не завести таблицу соответствия написаний.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
