#!/usr/bin/env python3
"""Сколько КОДОВ у нас с ценой и сколько без. Прямой ответ на вопрос владельца.

ЗАЧЕМ ОТДЕЛЬНЫЙ ЗАМЕР. Вопрос «сколько кодов с ценами и без» имеет три разных
ответа, и подменять один другим значит соврать:

  · КОДЫ, КОТОРЫЕ МЫ СПРАШИВАЛИ (спрос). Их и надо закрывать ценой — это наша
    работа. Код без цены здесь = позиция, по которой мы вышли на рынок и остались
    без ответа;
  · КОДЫ, ПО КОТОРЫМ ЕСТЬ ЦЕНА ПРЕДЛОЖЕНИЯ. Среди них бывают такие, которых мы не
    спрашивали: поставщик прислал прайс шире запроса;
  · КОДЫ КАТАЛОГА. Это наша библиотека деталей, и она пересекается с первыми двумя
    лишь частично.

Четыре ловушки, каждая даёт правдоподобное, но неверное число:

1. КОД — ЭТО КЛЮЧ, А НЕ СТРОКА. «AAA-111», «AAA 111» и «aaa111» — один код.
   Считая строки, получишь втрое больше кодов, чем есть (lib_pn_key).
2. СПРОС СЧИТАЕТСЯ ПО lib_demand_live, А НЕ lib_demand. Пункты договоров, попавшие
   в спрос и уже помеченные мусором, иначе станут «кодами без цены» и раздуют
   знаменатель.
3. ЦЕНА БЫВАЕТ НЕ ТОЛЬКО ИЗ ПРЕДЛОЖЕНИЯ. lib_prices держит и прайсы, и таможню.
   «Есть цена» по всем потокам и «есть цена от поставщика в ответ на наш запрос» —
   разные утверждения, и оба нужны: первое говорит, чем мы располагаем, второе —
   насколько закрыты заявки.
4. НЕ ВСЯКИЙ КЛЮЧ — АРТИКУЛ. Обрывок строки тоже даёт ключ. Правдоподобность
   оценивается отдельно (ключ с цифрой, от четырёх знаков), и это ОЦЕНКА, а не
   факт: ни одна строка по ней не отбрасывается.

Подготовка временными таблицами с индексами, а не коррелированными подзапросами
(CLAUDE.md, правило 8): иначе соединение читает полтора миллиона строк на каждый
из ста пятидесяти тысяч ключей.

По умолчанию ничего не пишет. С WRITE=1 добавляет ОДНУ строку агрегатов в
lib_metric_runs — журнал числовых замеров, из которого берётся динамика день ко
дню. До 22.09.2026 такой истории не существовало: цифры печатались в лог прогона
Actions и пропадали через девяносто дней, а сравнивать приходилось глазами.
Точка ключуется прогоном, поэтому повторный запуск того же прогона не плодит
точки, а перезаписывает свою.

В журнал прогона и в таблицу — только агрегаты (правило 17): счёт кодов и строк.

    SUPABASE_DB_URL=… python scripts/codes_with_prices.py
    SUPABASE_DB_URL=… WRITE=1 RUN_KEY=$GITHUB_RUN_ID python scripts/codes_with_prices.py
"""
from __future__ import annotations

import os

FEED_КП = "разбор КП"

# Имя замера в журнале. Оно же — ключ, по которому страница счётчика отбирает
# свои точки, поэтому менять его нельзя, не переписав историю.
ЗАМЕР = "коды_и_цены"

ЗАПИСЬ = """
insert into lib_metric_runs (metric, run_key, nums, note)
     values (%s, %s, %s::jsonb, %s)
on conflict (metric, run_key)
  do update set nums = excluded.nums, note = excluded.note,
                measured_at = now()
"""

ПОДГОТОВКА = """
create temp table коды_спроса as
  select lib_pn_key(d.part_number) as ключ,
         count(*)::bigint          as строк,
         count(distinct d.deal_id)::bigint as сделок
    from lib_demand_live d
   where length(lib_pn_key(d.part_number)) >= 2
   group by 1;
create index on коды_спроса (ключ);
analyze коды_спроса;

create temp table коды_кп as
  select lib_pn_key(p.part_number) as ключ, count(*)::bigint as строк
    from lib_prices p
   where p.feed = %s and length(lib_pn_key(p.part_number)) >= 2
   group by 1;
create index on коды_кп (ключ);
analyze коды_кп;

create temp table коды_цен_все as
  select lib_pn_key(p.part_number) as ключ,
         count(*)::bigint as строк,
         count(distinct coalesce(p.feed, '(без потока)'))::bigint as потоков
    from lib_prices p
   where length(lib_pn_key(p.part_number)) >= 2
   group by 1;
create index on коды_цен_все (ключ);
analyze коды_цен_все;

create temp table коды_каталога as
  select lib_pn_key(pt.catalog_no) as ключ
    from lib_parts pt
   where length(lib_pn_key(pt.catalog_no)) >= 2
   group by 1;
create index on коды_каталога (ключ);
analyze коды_каталога;
"""

ОБЪЁМ = """
select (select count(*)::bigint from коды_спроса)     as кодов_спроса,
       (select sum(строк)::bigint from коды_спроса)    as строк_спроса,
       (select count(*)::bigint from коды_кп)          as кодов_кп,
       (select sum(строк)::bigint from коды_кп)        as строк_кп,
       (select count(*)::bigint from коды_цен_все)     as кодов_цен_все,
       (select count(*)::bigint from коды_каталога)    as кодов_каталога
"""

# ГЛАВНЫЙ ОТВЕТ: коды, которые мы спрашивали, — с ценой и без.
СПРОС_С_ЦЕНОЙ = """
select count(*)::bigint                                          as всего,
       count(*) filter (where кп)::bigint                         as с_ценой_кп,
       count(*) filter (where not кп and любая)::bigint           as только_чужой_поток,
       count(*) filter (where not кп and not любая)::bigint        as без_цены_вовсе,
       sum(строк) filter (where кп)::bigint                        as строк_с_ценой_кп,
       sum(строк) filter (where not кп and not любая)::bigint      as строк_без_цены
  from (select с.ключ, с.строк,
               exists (select 1 from коды_кп к where к.ключ = с.ключ)       as кп,
               exists (select 1 from коды_цен_все ц where ц.ключ = с.ключ)  as любая
          from коды_спроса с) x
"""

# Обратная сторона: коды, по которым цена есть, — спрашивали ли мы их.
ЦЕНЫ_ПРОТИВ_СПРОСА = """
select count(*)::bigint                                           as кодов_с_ценой_кп,
       count(*) filter (where спрос)::bigint                        as спрашивали,
       count(*) filter (where not спрос)::bigint                    as не_спрашивали,
       count(*) filter (where каталог)::bigint                      as есть_в_каталоге
  from (select к.ключ,
               exists (select 1 from коды_спроса с where с.ключ = к.ключ)   as спрос,
               exists (select 1 from коды_каталога т where т.ключ = к.ключ) as каталог
          from коды_кп к) x
"""

КАТАЛОГ = """
select count(*)::bigint                                           as кодов_каталога,
       count(*) filter (where кп)::bigint                           as с_ценой_кп,
       count(*) filter (where спрос)::bigint                        as спрашивали
  from (select т.ключ,
               exists (select 1 from коды_кп к where к.ключ = т.ключ)     as кп,
               exists (select 1 from коды_спроса с where с.ключ = т.ключ) as спрос
          from коды_каталога т) x
"""

# ПРАВДОПОДОБНОСТЬ КЛЮЧА — ОЦЕНКА, А НЕ ФАКТ. Обрывок строки тоже даёт ключ.
# Признаки те же, что у профиля спроса: есть цифра, от четырёх знаков, не длиннее
# двадцати пяти. Ни одна строка по этой оценке не отбрасывается.
ПРАВДОПОДОБНОСТЬ = """
select count(*)::bigint                                           as всего,
       count(*) filter (where ключ ~ '[0-9]'
                          and length(ключ) between 4 and 25)::bigint as правдоподобных,
       count(*) filter (where ключ !~ '[0-9]')::bigint              as без_цифры,
       count(*) filter (where length(ключ) < 4)::bigint             as короче_четырёх,
       count(*) filter (where length(ключ) > 25)::bigint            as длиннее_25
  from коды_спроса
"""


def ч(x) -> int:
    return int(x or 0)


def доля(часть, целое) -> str:
    часть, целое = ч(часть), ч(целое)
    return f"{часть:>8d}" + (f"  {100 * часть / целое:5.1f} %" if целое else "")


def ключ_прогона() -> str:
    """Ключ точки. Прогон Actions, если мы в нём, иначе метка времени UTC.

    Прогон нужен именно номером: по нему точка находится в логах, а повторный
    запуск того же прогона перезаписывает свою строку вместо новой.
    """
    из_среды = (os.environ.get("RUN_KEY") or os.environ.get("GITHUB_RUN_ID") or "").strip()
    if из_среды:
        return из_среды
    from datetime import datetime, timezone
    return "вручную-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def записать(cur, числа: dict, оговорка: str | None) -> None:
    import json

    ключ = ключ_прогона()
    cur.execute(ЗАПИСЬ, (ЗАМЕР, ключ, json.dumps(числа, ensure_ascii=False), оговорка))
    print(f"\n✓ точка истории записана: замер «{ЗАМЕР}», прогон {ключ},"
          f" чисел {len(числа)}")


def main() -> int:
    dsn = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not dsn:
        print("нет SUPABASE_DB_URL — мерить нечего")
        return 2
    import psycopg2

    conn = psycopg2.connect(dsn, connect_timeout=20,
                            options="-c statement_timeout=600000")
    # Числа собираются по ходу печати, а не вторым проходом запросов: второй
    # проход по полутора миллионам строк спроса дал бы ДРУГИЕ числа, если между
    # проходами идёт разбор, и точка истории разошлась бы с напечатанной.
    числа: dict[str, int] = {}
    try:
        with conn.cursor() as cur:
            print("=== КОДЫ С ЦЕНОЙ И БЕЗ ЦЕНЫ ===")
            print("Код — это КЛЮЧ номера (регистр, дефисы и пробелы сняты), а не")
            print("написание: «AAA-111», «AAA 111» и «aaa111» — один код.\n")
            cur.execute(ПОДГОТОВКА, (FEED_КП,))

            cur.execute(ОБЪЁМ)
            кс, стрс, ккп, стркп, кц, кт = cur.fetchone()
            print("ОБЪЁМ:")
            числа.update(asked=ч(кс), rows_asked=ч(стрс), price_codes=ч(ккп),
                         price_rows=ч(стркп), price_codes_any=ч(кц), catalog=ч(кт))
            print(f"    кодов в спросе (что мы спрашивали)  {ч(кс):>8d}"
                  f"  · строк {ч(стрс)}")
            print(f"    кодов в ценах предложений           {ч(ккп):>8d}"
                  f"  · строк {ч(стркп)}")
            print(f"    кодов в ценах ВСЕХ потоков          {ч(кц):>8d}")
            print(f"    кодов в каталоге деталей            {ч(кт):>8d}")

            cur.execute(СПРОС_С_ЦЕНОЙ)
            всего, с_кп, чужой, без, стр_с, стр_без = cur.fetchone()
            числа.update(with_kp=ч(с_кп), other_feed=ч(чужой), no_price=ч(без),
                         rows_with=ч(стр_с), rows_without=ч(стр_без))
            print("\nГЛАВНОЕ — КОДЫ, КОТОРЫЕ МЫ СПРАШИВАЛИ:")
            print(f"    всего кодов                         {ч(всего):>8d}")
            print(f"    с ценой ОТ ПОСТАВЩИКА (разбор КП)   {доля(с_кп, всего)}")
            print(f"    цена есть, но из другого потока     {доля(чужой, всего)}")
            print(f"    БЕЗ ЦЕНЫ ВОВСЕ                      {доля(без, всего)}")
            print(f"    строк спроса за кодами с ценой      {ч(стр_с):>8d}")
            print(f"    строк спроса за кодами без цены     {ч(стр_без):>8d}")

            cur.execute(ЦЕНЫ_ПРОТИВ_СПРОСА)
            кодов, спраш, не_спраш, в_кат = cur.fetchone()
            числа.update(price_asked=ч(спраш), price_not_asked=ч(не_спраш),
                         price_in_catalog=ч(в_кат))
            print("\nОБРАТНАЯ СТОРОНА — КОДЫ, ПО КОТОРЫМ ЦЕНА ЕСТЬ:")
            print(f"    всего кодов с ценой предложения     {ч(кодов):>8d}")
            print(f"    из них мы спрашивали                {доля(спраш, кодов)}")
            print(f"    НЕ спрашивали (прайс шире запроса)  {доля(не_спраш, кодов)}")
            print(f"    есть в каталоге деталей             {доля(в_кат, кодов)}")

            cur.execute(КАТАЛОГ)
            кат, кат_кп, кат_спрос = cur.fetchone()
            числа.update(catalog_priced=ч(кат_кп), catalog_asked=ч(кат_спрос))
            print("\nКАТАЛОГ ДЕТАЛЕЙ:")
            print(f"    всего кодов                         {ч(кат):>8d}")
            print(f"    с ценой предложения                 {доля(кат_кп, кат)}")
            print(f"    спрашивали хоть раз                 {доля(кат_спрос, кат)}")

            cur.execute(ПРАВДОПОДОБНОСТЬ)
            в, прав, без_ц, кор, длин = cur.fetchone()
            print("\nОГОВОРКА О КАЧЕСТВЕ КОДОВ СПРОСА (это ОЦЕНКА, а не факт —")
            print("ни одна строка по ней не отбрасывается):")
            print(f"    правдоподобных (есть цифра, 4–25 знаков) {доля(прав, в)}")
            print(f"    без единой цифры                         {доля(без_ц, в)}")
            print(f"    короче четырёх знаков                    {доля(кор, в)}")
            числа.update(plausible=ч(прав), no_digit=ч(без_ц),
                         shorter_than_four=ч(кор), longer_than_25=ч(длин))
            print(f"    длиннее двадцати пяти знаков             {доля(длин, в)}")

            if os.environ.get("WRITE", "").strip():
                записать(cur, числа, os.environ.get("NOTE", "").strip() or None)
                conn.commit()
            else:
                print("\nточка истории НЕ записана — для записи WRITE=1")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
