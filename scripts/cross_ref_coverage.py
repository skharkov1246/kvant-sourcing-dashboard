#!/usr/bin/env python3
"""Перекрёстная система «позиция ↔ поставщик»: чем её можно наполнить сегодня.

ЗАЧЕМ. ТЗ владельца (22.09.2026): карточка товара показывает, КТО выдавал на
эту позицию предложение, кто изготовитель и какие есть аналоги; карточка
поставщика показывает, НА ЧТО и на какие бренды он предложения выдавал. Это
один и тот же набор связей, прочитанный с двух сторон.

Прежде чем рисовать оба экрана, надо знать, сколько в них попадёт строк. Экран
сравнения поставщиков по позиции, у которой поставщик один, — это не сравнение,
а одна строка в рамке. Поэтому главный счётчик здесь не «позиций всего», а
«позиций, у которых предложений от РАЗНЫХ компаний два и больше»: ровно на них
карточка отвечает на вопрос, ради которого её просили.

ЧТО НЕ СЧИТАЕТСЯ. Родовая связь «поставщик работает по такому классу» в расчёт
не идёт: в карточку позиции попадает только тот, кто дал предложение ИМЕННО на
неё (CLAUDE.md, «Не выдавай родовой адрес за адрес по детали»).

В журнал — только агрегаты: счётчики, доли, коды валют, свои же константы
(правило 17). Ни наименований позиций, ни названий компаний, ни номеров
карточек запроса запросы вернуть не могут.

    SUPABASE_DB_URL=… python scripts/cross_ref_coverage.py
"""
from __future__ import annotations

import os

FEED = "разбор КП"

# Минимум разных компаний, при котором карточка позиции показывает выбор, а не
# единственную строку. Два — не «хорошо», а «уже не бессмысленно».
МИН_ПОСТАВЩИКОВ = 2

# ПОЗИЦИЯ — ЭТО КЛЮЧ АРТИКУЛА, А НЕ ЕГО НАПИСАНИЕ. Один и тот же подшипник
# приходит от трёх поставщиков в трёх написаниях; считая по сырой строке, мы
# получим три позиции по одному предложению вместо одной по трём — то есть
# ровно обратную картину той, которую меряем. Ключ берём lib_pn_key: та же
# функция, что связывает спрос с каталогом. Своя копия правила разошлась бы
# тихо (расхождение SQL и Python на «Ё» уже было).
ПОЗИЦИИ = """
with строки as (
  select lib_pn_key(part_number) as ключ, rfq_company, oem, rfq_brands,
         price, currency
    from lib_prices
   where feed = %s and coalesce(btrim(part_number), '') <> ''
     and lib_pn_key(part_number) <> ''
), поз as (
  select ключ,
         count(*)                                    as строк,
         count(distinct rfq_company)                 as компаний,
         count(distinct oem) filter (where oem is not null)        as изготовителей,
         count(*) filter (where coalesce(btrim(rfq_brands), '') <> '') as с_брендом,
         count(distinct currency) filter (where price > 0
                                            and currency is not null) as валют,
         count(*) filter (where price > 0 and currency is not null)   as с_ценой
    from строки group by ключ
)
select count(*)                                                as позиций,
       count(*) filter (where компаний >= 1)                    as с_поставщиком,
       count(*) filter (where компаний >= %s)                   as с_выбором,
       count(*) filter (where изготовителей > 0)                as с_изготовителем,
       count(*) filter (where с_брендом > 0)                    as с_брендом,
       count(*) filter (where с_ценой >= %s and валют = 1)      as сравнимых,
       percentile_disc(0.5)  within group (order by компаний)   as медиана_компаний,
       percentile_disc(0.9)  within group (order by компаний)   as p90_компаний,
       max(компаний)                                            as максимум_компаний
  from поз
"""

# СКОЛЬКО ИЗ ПОЗИЦИЙ ДОБИРАЕТСЯ ДО КАТАЛОГА. Аналоги и машина живут в каталоге
# (lib_part_alt, lib_part_models), а котировка — нет. Позиция, не нашедшаяся в
# каталоге, покажет в карточке цену и поставщиков, но не покажет ни аналога,
# ни машины: раздел «чем заменить» останется пустым, сколько его ни рисуй.
КАТАЛОГ = """
with ключи as (
  select distinct lib_pn_key(part_number) as ключ
    from lib_prices
   where feed = %s and coalesce(btrim(part_number), '') <> ''
     and lib_pn_key(part_number) <> ''
), сцепка as (
  select к.ключ, p.id as part_id, p.oem
    from ключи к join lib_parts p on p.id = к.ключ
)
select (select count(*) from ключи)                                  as позиций,
       (select count(*) from сцепка)                                 as в_каталоге,
       (select count(*) from сцепка where coalesce(btrim(oem),'') <> '') as с_оем_каталога,
       (select count(distinct s.ключ) from сцепка s
          join lib_part_alt a on a.part_id = s.part_id)              as с_аналогом,
       (select count(distinct s.ключ) from сцепка s
          join lib_part_models m on m.part_id = s.part_id)           as с_машиной,
       (select count(distinct s.ключ) from сцепка s
          join lib_part_suppliers ps on ps.part_id = s.part_id)      as с_исполнителем
"""

# ОТСЕВ ПУСТОГО БРЕНДА — НЕ ПЕРЕСТРАХОВКА. Полностью пустое значение опасности
# не несёт: string_to_array('', ',') в PostgreSQL даёт ПУСТОЙ массив, и unnest
# по нему не выдаёт ни строки (проверено на 16.13). Фильтр нужен ради другого:
# rfq_brands собирается сцепкой ключей, и «FAG,» с запятой в хвосте, «A,,B» с
# двойным разделителем и значение из одного пробела дают элемент-пустышку. Без
# btrim она считается брендом, и у компании без брендов их оказывается один.
#
# ПОСТАВЩИК — ЭТО КЛЮЧ КОМПАНИИ ПОРТАЛА. Имя компании ключом быть не может:
# «ABC GmbH» и «ABC Germany» — две строки и одна фирма. Сведение живёт в
# sup_identifier (kind='bitrix'), и здесь же меряется, какая доля котирующих
# компаний до реестра доведена: не доведённая карточку не откроет.
ПОСТАВЩИКИ = """
with строки as (
  select rfq_company, lib_pn_key(part_number) as ключ, rfq_brands
    from lib_prices
   where feed = %s and rfq_company is not null
), комп as (
  select rfq_company,
         count(*)                                              as строк,
         count(distinct ключ) filter (where ключ <> '')         as позиций,
         count(distinct b)                                      as брендов
    from строки
    left join lateral unnest(string_to_array(coalesce(rfq_brands, ''), ',')) as b
           on btrim(b) <> ''
   group by rfq_company
)
select count(*)                                                 as компаний,
       count(*) filter (where позиций >= %s)                     as с_несколькими,
       count(*) filter (where брендов > 0)                       as с_брендами,
       percentile_disc(0.5) within group (order by позиций)      as медиана_позиций,
       percentile_disc(0.9) within group (order by позиций)      as p90_позиций,
       max(позиций)                                              as максимум_позиций,
       percentile_disc(0.5) within group (order by брендов)      as медиана_брендов,
       max(брендов)                                              as максимум_брендов
  from комп
"""

СВЕДЕНИЕ = """
select count(distinct p.rfq_company)                              as всего,
       count(distinct p.rfq_company) filter (where i.sup_id is not null) as сведено
  from lib_prices p
  left join sup_identifier i
         on i.kind = 'bitrix' and i.status <> 'rejected'
        and i.value_norm = p.rfq_company
 where p.feed = %s and p.rfq_company is not null
"""

# ПАРА «ПОЗИЦИЯ — ПОСТАВЩИК» — ЭТО И ЕСТЬ РЕБРО ПЕРЕКРЁСТНОЙ СИСТЕМЫ. Число
# рёбер, а не вершин, показывает, насколько граф связен: сто позиций и сто
# поставщиков при ста рёбрах — это сто не связанных ни с чем пар.
РЁБРА = """
select count(*) from (
  select distinct lib_pn_key(part_number) as ключ, rfq_company
    from lib_prices
   where feed = %s and rfq_company is not null
     and coalesce(btrim(part_number), '') <> ''
     and lib_pn_key(part_number) <> ''
) t
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
                            options="-c statement_timeout=180000")
    try:
        with conn.cursor() as cur:
            cur.execute(ПОЗИЦИИ, (FEED, МИН_ПОСТАВЩИКОВ, МИН_ПОСТАВЩИКОВ))
            (позиций, с_поставщиком, с_выбором, с_изготовителем, с_брендом,
             сравнимых, мед_комп, p90_комп, макс_комп) = cur.fetchone()

            if not позиций:
                print("котировок с артикулом в базе нет — разбор ещё не проходил")
                return 0

            print("=== КАРТОЧКА ТОВАРА: чем её наполнять ===")
            print(f"позиций (по ключу артикула):     {позиций}")
            print(f"  у которых назван поставщик:    {доля(с_поставщиком, позиций)}")
            print(f"  предложений от {МИН_ПОСТАВЩИКОВ}+ компаний:   "
                  f"{доля(с_выбором, позиций)}   ← ради чего карточка")
            print(f"  сравнимых по цене в одной валюте: {доля(сравнимых, позиций)}")
            print(f"  с изготовителем из файла:      {доля(с_изготовителем, позиций)}")
            print(f"  с брендом с карточки запроса:  {доля(с_брендом, позиций)}")
            print(f"компаний на позицию: медиана {мед_комп} · p90 {p90_комп} · "
                  f"максимум {макс_комп}")

            cur.execute(КАТАЛОГ, (FEED,))
            (кп, в_каталоге, с_оем, с_аналогом, с_машиной, с_исп) = cur.fetchone()
            print("\nчто из этого дотягивается до каталога (аналоги и машина")
            print("живут там, а котировка — нет):")
            print(f"  нашлось в lib_parts:           {доля(в_каталоге, кп)}")
            print(f"    с изготовителем в каталоге:  {доля(с_оем, кп)}")
            print(f"    с аналогом (lib_part_alt):   {доля(с_аналогом, кп)}")
            print(f"    с машиной (lib_part_models): {доля(с_машиной, кп)}")
            print(f"    с исполнителем (lib_part_suppliers): {доля(с_исп, кп)}")

            cur.execute(ПОСТАВЩИКИ, (FEED, МИН_ПОСТАВЩИКОВ))
            (компаний, с_неск, с_брендами, мед_поз, p90_поз, макс_поз,
             мед_бр, макс_бр) = cur.fetchone()
            print("\n=== КАРТОЧКА ПОСТАВЩИКА: чем её наполнять ===")
            print(f"компаний, давших котировку:      {компаний}")
            print(f"  у которых позиций {МИН_ПОСТАВЩИКОВ}+:         {доля(с_неск, компаний)}")
            print(f"  у которых назван бренд:        {доля(с_брендами, компаний)}")
            print(f"позиций на компанию: медиана {мед_поз} · p90 {p90_поз} · "
                  f"максимум {макс_поз}")
            print(f"брендов на компанию: медиана {мед_бр} · максимум {макс_бр}")

            cur.execute(СВЕДЕНИЕ, (FEED,))
            всего_к, сведено = cur.fetchone()
            print(f"сведено с реестром поставщиков:  {доля(сведено, всего_к)}"
                  "  ← у остальных карточка не откроется")

            cur.execute(РЁБРА, (FEED,))
            рёбер = cur.fetchone()[0]
            print(f"\nсвязей «позиция — поставщик»:    {рёбер}")
            if позиций and компаний:
                print(f"  на позицию {рёбер / позиций:.2f} · "
                      f"на компанию {рёбер / компаний:.2f}")
            print("\nЧИТАТЬ ТАК: карточка позиции отвечает на вопрос «у кого брать»")
            print("только там, где компаний две и больше. Остальные показывают")
            print("одно предложение — это справка, а не выбор.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
