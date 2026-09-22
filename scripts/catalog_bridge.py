#!/usr/bin/env python3
"""Чем ещё связать артикул котировки с каталогом: замер по каждому мосту.

ЗАЧЕМ. По основному номеру из 2 300 артикулов котировок в каталоге нашлись 214
(9 %), и нормализация номера добавила РОВНО ОДИН. Значит номера действительно
другие, и искать надо не другую нормализацию, а другой ключ.

От этого зависят три раздела карточки товара: «чем закрыть», «где стоит», «кто
делает». Все три живут в каталоге, а не в котировке. Пока мост не найден, они
пусты у девяти позиций из десяти.

ЧТО СЧИТАЕТСЯ МОСТОМ. Четыре ключа, помимо основного номера:
  · lib_part_alt.alt_pn — номер изготовителя, замена, аналог, наш номер;
  · lib_parts.aliases — иные написания каталожного номера;
  · lib_parts.kv_no — наш внутренний номер («KV-000753-4»);
  · lib_parts.catalog_no сырым написанием — на случай, когда ключ его портит.

ГЛАВНОЕ ЗДЕСЬ — НЕ «СКОЛЬКО ДОБАВИТСЯ», А «СКОЛЬКО ОДНОЗНАЧНО». Один
альтернативный номер законно принадлежит нескольким деталям: «6205» есть у SKF,
FAG и ГПЗ. Такой мост привяжет котировку к чужой машине и чужому изготовителю,
и ошибка будет невидимой — раздел заполнится неверно, а не останется пустым.
Пустой раздел честнее. Поэтому каждый мост считается двумя числами: сколько
артикулов он связывает С ОДНОЙ деталью и сколько — с несколькими.

СУММА МОСТОВ НЕ РАВНА ОБЪЕДИНЕНИЮ. Один артикул может найтись и по альтернативе,
и по псевдониму. Поэтому рядом со вкладом каждого моста считается объединение —
иначе «плюс 300 и плюс 200» прочтётся как плюс 500 там, где их 350.

Ничего не пишет. В журнал — только агрегаты (правило 17).

    SUPABASE_DB_URL=… python scripts/catalog_bridge.py
"""
from __future__ import annotations

import os

FEED = "разбор КП"

# Артикулы котировок одним набором: дальше каждый мост примеряется к нему.
БАЗА = """
create temporary table если_есть_ключи as
  select distinct lib_pn_key(part_number) as ключ
    from lib_prices
   where feed = %s and coalesce(btrim(part_number), '') <> ''
     and lib_pn_key(part_number) <> ''
"""

# Мосты. Каждый отдаёт (ключ, part_id) и считается на однозначность.
МОСТЫ = {
    "основной номер (как сейчас)": """
        select к.ключ, p.id as part_id
          from если_есть_ключи к join lib_parts p on p.id = к.ключ
    """,
    "альтернативный номер (lib_part_alt)": """
        select к.ключ, a.part_id
          from если_есть_ключи к
          join lib_part_alt a on lib_pn_key(a.alt_pn) = к.ключ
    """,
    "псевдоним каталога (lib_parts.aliases)": """
        select к.ключ, p.id as part_id
          from если_есть_ключи к
          join lib_parts p on exists (
                 select 1 from unnest(coalesce(p.aliases, '{}'::text[])) as al
                  where lib_pn_key(al) = к.ключ)
    """,
    "наш внутренний номер (kv_no)": """
        select к.ключ, p.id as part_id
          from если_есть_ключи к
          join lib_parts p on lib_pn_key(p.kv_no) = к.ключ
    """,
    "каталожный номер сырым написанием": """
        select к.ключ, p.id as part_id
          from если_есть_ключи к
          join lib_parts p on lib_pn_key(p.catalog_no) = к.ключ
    """,
}

# Однозначность: сколько деталей приходится на артикул по этому мосту.
СЧЁТ = """
with мост as ({0}), по_ключу as (
  select ключ, count(distinct part_id) as деталей from мост group by ключ
)
select count(*)                                  as связано,
       count(*) filter (where деталей = 1)        as однозначно,
       count(*) filter (where деталей > 1)        as неоднозначно,
       max(деталей)                               as максимум_деталей
  from по_ключу
"""

# Вклад моста ПОВЕРХ основного номера — только однозначные связи, только те
# артикулы, которых основной номер не нашёл, и только те, что реально приводят
# к машине или аналогу. Мост, добавляющий деталь без связей, ничего не даёт
# карточке: разделы останутся пустыми, просто по другой причине.
ВКЛАД = """
with основной as (
  select к.ключ from если_есть_ключи к join lib_parts p on p.id = к.ключ
), мост as ({0}), однозначные as (
  select ключ, min(part_id) as part_id
    from мост group by ключ having count(distinct part_id) = 1
), новые as (
  select о.ключ, о.part_id from однозначные о
   where о.ключ not in (select ключ from основной)
)
select count(*)                                                     as новых,
       count(*) filter (where exists (select 1 from lib_part_models m
                                       where m.part_id = н.part_id))  as с_машиной,
       count(*) filter (where exists (select 1 from lib_part_alt a
                                       where a.part_id = н.part_id))  as с_аналогом,
       count(*) filter (where exists (select 1 from lib_part_suppliers s
                                       where s.part_id = н.part_id))  as с_изготовителем
  from новые н
"""

# Объединение однозначных мостов: сумма вкладов её завышает.
ОБЪЕДИНЕНИЕ = """
with основной as (
  select к.ключ from если_есть_ключи к join lib_parts p on p.id = к.ключ
), все_мосты as ({0}
), однозначные as (
  select ключ, min(part_id) as part_id
    from все_мосты group by ключ having count(distinct part_id) = 1
), новые as (
  select о.ключ, о.part_id from однозначные о
   where о.ключ not in (select ключ from основной)
)
select (select count(*) from если_есть_ключи)                        as артикулов,
       (select count(*) from основной)                               as по_основному,
       (select count(*) from новые)                                  as добавят_мосты,
       (select count(*) from новые н
         where exists (select 1 from lib_part_models m
                        where m.part_id = н.part_id))                as из_них_с_машиной
"""


def доля(часть, целое) -> str:
    часть = часть or 0
    return f"{часть}" + (f" ({100 * часть / целое:.0f} %)" if целое else "")


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
            cur.execute(БАЗА, (FEED,))
            cur.execute("select count(*) from если_есть_ключи")
            артикулов = cur.fetchone()[0]
            if not артикулов:
                print("артикулов в котировках нет — разбор ещё не проходил")
                return 0
            print("=== МОСТЫ «АРТИКУЛ КОТИРОВКИ → КАТАЛОГ» ===")
            print(f"артикулов в котировках: {артикулов}\n")

            print("каждый мост сам по себе (однозначно = ведёт РОВНО к одной детали;")
            print("неоднозначная связь хуже отсутствия: раздел заполнится неверно):")
            for имя, sql in МОСТЫ.items():
                cur.execute(СЧЁТ.format(sql))
                связано, однозначно, неоднозначно, максимум = cur.fetchone()
                print(f"  {имя}")
                print(f"      связано {доля(связано, артикулов)}"
                      f" · однозначно {доля(однозначно, артикулов)}"
                      f" · неоднозначно {неоднозначно}"
                      f" (до {максимум or 0} деталей на артикул)")

            print("\nвклад ПОВЕРХ основного номера, только однозначные связи:")
            for имя, sql in МОСТЫ.items():
                if имя.startswith("основной"):
                    continue
                cur.execute(ВКЛАД.format(sql))
                новых, с_машиной, с_аналогом, с_изг = cur.fetchone()
                print(f"  {имя}: +{новых}"
                      f"  из них с машиной {с_машиной}, с аналогом {с_аналогом},"
                      f" с изготовителем {с_изг}")

            союз = "\n        union all\n".join(
                sql for имя, sql in МОСТЫ.items() if not имя.startswith("основной"))
            cur.execute(ОБЪЕДИНЕНИЕ.format(союз))
            всего, по_основному, добавят, с_машиной = cur.fetchone()
            print("\nОБЪЕДИНЕНИЕМ (сумма вкладов выше её завышает — мосты пересекаются):")
            print(f"  по основному номеру:  {доля(по_основному, всего)}")
            print(f"  добавят мосты:        +{добавят}")
            print(f"  станет:               {доля((по_основному or 0) + (добавят or 0), всего)}")
            print(f"  из добавленных ведут к машине: {доля(с_машиной, добавят)}")
            print("\nЧИТАТЬ ТАК: применять стоит тот мост, у которого велик вклад И")
            print("мала неоднозначность. Мост с большой неоднозначностью не применяется")
            print("вовсе: он привяжет котировку к чужой машине, и это будет не видно.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
