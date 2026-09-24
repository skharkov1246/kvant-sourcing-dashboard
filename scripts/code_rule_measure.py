#!/usr/bin/env python3
"""Замер правила «правдоподобный код» и читаемости количества по живой базе.

ЗАЧЕМ. 24.09.2026 карточка номенклатуры показала код «SS316» (марка стали),
«спрашивали 73 сделки» и количество «3 163 518 182,316». Правило
docfilter.код_правдоподобен (двойник в SQL — lib_pn_plausible) теперь отказывает
в коде марке, размеру и стандарту, а количество, которое не читается, не
показывается. Правило действует при чтении, то есть сразу на всю базу, — и
прежде чем верить итогу, надо знать, СКОЛЬКО и ЧЕГО оно сняло (правило 3) и
у скольких стало хуже (правило 0).

ЧТО ПЕЧАТАЕТ (только агрегаты, правило 17):
  · спрос (lib_demand_live): строк и ключей с кодом, из них отвергнуто по классам;
  · цены (lib_prices): то же, раздельно «разбор КП» и прочие потоки;
  · номенклатура (ключи потока «разбор КП» — карточки страницы /nomenclature):
    позиций было и осталось; строк спроса и сделок, которые ложные карточки
    собирали по ключу (снято с них);
  · СТАЛО ХУЖЕ: строки цены, чей единственный код отвергнут, — они уходят из
    номенклатуры. Из них — сколько вернул бы переразбор (в наименовании есть
    другой, правдоподобный код: docfilter.part_number_of), а сколько кода не
    имеют вовсе. Цены при этом остаются в базе: снимается только ключ;
  · КАТАЛОГ как контроль ложного обвинения: сколько каталожных номеров lib_parts
    правило отвергло бы. Каталог курирован — ненулевое число здесь значит, что
    закрытый список задел настоящие номера, и их надо смотреть поимённо;
  · образцы отвергнутых ключей по классам: ключ с цифрами, заменёнными на «9»
    («ss999», «99mm»), и число строк — без наименований и без самих номеров;
  · количество: строк цены и спроса больше quotes.МАКС_КОЛИЧЕСТВО, строк цены,
    где «количество × цена ≠ сумма», и строк с оговоркой разборщика о снятом
    количестве.

Только выборки: работает и при базе в режиме только чтения. Функция
lib_pn_plausible в базе НЕ нужна — выражение передаётся параметром из
docfilter, то есть мерится ровно то правило, что стоит в коде. Спрос —
частями по хешу ключа (PARTS, по умолчанию 16): счёт различных ключей между
частями складывается, потому что ключ живёт ровно в одной части.

    SUPABASE_DB_URL=… python scripts/code_rule_measure.py
"""
from __future__ import annotations

import collections
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from library import docfilter, quotes  # noqa: E402

FEED_КП = "разбор КП"
ОБРАЗЦОВ = 15
МАКС = int(quotes.МАКС_КОЛИЧЕСТВО)


def параметры() -> dict:
    """Выражения классов — из docfilter, буква в букву, как в lib_pn_plausible."""
    п = {"rx": docfilter.ВЫРАЖЕНИЕ_НЕ_КОДА, "макс": МАКС,
         "допуск": quotes.ДОПУСК_МИН, "на_ед": quotes.ДОПУСК_НА_ЕДИНИЦУ,
         "из_суммы": "%" + quotes.ОГОВОРКА_ИЗ_СУММЫ + "%",
         "кол_снято": "%" + quotes.КОЛ_НЕ_СОШЛОСЬ + "%",
         "кол_предел": "%" + quotes.КОЛ_НЕ_ЧИТАЕТСЯ + "%"}
    for класс, выр in docfilter._КЛАССЫ_НЕ_КОДА:
        п[класс] = "^(" + выр + ")$"
    return п


# Класс ключа в SQL — в том же порядке, что в docfilter.класс_не_кода.
КЛАСС = ("case when k ~ %(стандарт)s then 'стандарт'"
         " when k ~ %(размер)s then 'размер'"
         " when k ~ %(марка)s then 'марка' end")

СПРОС = f"""
select {КЛАСС}                                            as класс,
       count(*)::bigint                                   as строк,
       count(distinct k)::bigint                          as ключей,
       count(*) filter (where qty > %(макс)s)::bigint      as кол_больше_предела
  from (select lib_pn_key(d.part_number) as k, d.qty
          from lib_demand_live d
         where coalesce(btrim(d.part_number), '') <> ''
        offset 0) x
 where k <> '' and (hashtext(k) & 2147483647) %% %(n)s = %(i)s
 group by 1
"""

СПРОС_ОБРАЗЦЫ = f"""
select {КЛАСС}                                            as класс,
       regexp_replace(k, '[0-9]', '9', 'g')               as образец,
       count(*)::bigint                                   as строк,
       count(distinct k)::bigint                          as ключей
  from (select lib_pn_key(d.part_number) as k
          from lib_demand_live d
         where coalesce(btrim(d.part_number), '') <> ''
        offset 0) x
 where k <> '' and (hashtext(k) & 2147483647) %% %(n)s = %(i)s
   and k ~ %(rx)s
 group by 1, 2
"""


def цены_sql(есть_total: bool, есть_note: bool) -> str:
    """Цены по классам. Колонки total и note спрашиваются у базы заранее."""
    total = "p.total" if есть_total else "null::numeric"
    note = "coalesce(p.note, '')" if есть_note else "''"
    return f"""
select (p.feed = %(feed)s)                                as поток_кп,
       {КЛАСС}                                            as класс,
       count(*)::bigint                                   as строк,
       count(distinct k)::bigint                          as ключей,
       count(*) filter (where p.qty > %(макс)s)::bigint    as кол_больше_предела,
       count(*) filter (where p.price > 0 and p.qty > 0 and p.qty <= %(макс)s
                          and {total} > 0 and {note} not like %(из_суммы)s
                          and abs(p.price * p.qty - {total})
                              > greatest(%(допуск)s, p.qty * %(на_ед)s))::bigint
                                                          as тройка_не_сошлась,
       count(*) filter (where {note} like %(кол_снято)s
                           or {note} like %(кол_предел)s)::bigint as кол_снято_разбором
  from (select p.*, lib_pn_key(p.part_number) as k from lib_prices p
         where coalesce(btrim(p.part_number), '') <> '') p
 where k <> ''
 group by 1, 2
"""


ЦЕНЫ_ОБРАЗЦЫ = f"""
select {КЛАСС}                                            as класс,
       regexp_replace(k, '[0-9]', '9', 'g')               as образец,
       count(*)::bigint                                   as строк,
       count(distinct k)::bigint                          as ключей
  from (select lib_pn_key(part_number) as k from lib_prices
         where feed = %(feed)s and coalesce(btrim(part_number), '') <> '') x
 where k ~ %(rx)s
 group by 1, 2
"""

# Спрос, который ложные карточки собирали по ключу: строки и сделки. Отбор
# ключей — независимым подзапросом (правило 8), индекс по lib_pn_key есть.
СНЯТЫЙ_СПРОС = """
with ложные as materialized (
  select distinct lib_pn_key(part_number) as k from lib_prices
   where feed = %(feed)s and coalesce(btrim(part_number), '') <> ''
     and lib_pn_key(part_number) ~ %(rx)s
)
select count(*)::bigint                                   as ключей,
       coalesce(sum(строк), 0)::bigint                    as строк_спроса,
       coalesce(sum(сделок), 0)::bigint                   as сделок_по_карточкам,
       coalesce(max(сделок), 0)::bigint                   as сделок_у_худшей
  from (select л.k, count(d.id) as строк, count(distinct d.deal_id) as сделок
          from ложные л
          left join lib_demand_live d on lib_pn_key(d.part_number) = л.k
         group by л.k) z
"""

# Строки цены, чей код отвергнут, — наименования читает ТОЛЬКО этот процесс,
# чтобы спросить docfilter.part_number_of; в журнал они не попадают.
ОТВЕРГНУТЫЕ_ЦЕНЫ = """
select item_name from lib_prices
 where feed = %(feed)s and coalesce(btrim(part_number), '') <> ''
   and lib_pn_key(part_number) ~ %(rx)s
"""

КАТАЛОГ = f"""
select {КЛАСС}                                            as класс,
       regexp_replace(k, '[0-9]', '9', 'g')               as образец,
       count(*)::bigint                                   as номеров
  from (select lib_pn_key(catalog_no) as k from lib_parts) x
 where k ~ %(rx)s
 group by 1, 2
"""

КОЛОНКИ = """
select column_name from information_schema.columns
 where table_name = 'lib_prices' and column_name in ('total', 'note')
   and table_schema = any(current_schemas(false))
"""


def ч(v) -> int:
    return int(v or 0)


def печать_образцов(заголовок: str, строки) -> None:
    по_классу = collections.defaultdict(collections.Counter)
    ключи = collections.defaultdict(collections.Counter)
    for класс, образец, n, *хвост in строки:
        по_классу[класс or "?"][образец] += ч(n)
        if хвост:
            ключи[класс or "?"][образец] += ч(хвост[0])
    print(f"  {заголовок}:")
    if not по_классу:
        print("    (нет)")
    for класс in sorted(по_классу):
        всего = sum(по_классу[класс].values())
        print(f"    {класс}: {всего} строк, образцов {len(по_классу[класс])}")
        for образец, n in по_классу[класс].most_common(ОБРАЗЦОВ):
            к = ключи[класс].get(образец)
            print(f"      {образец:<24s} {n:>9d}" + (f"  ключей {к}" if к is not None else ""))


def замер(cur, частей: int) -> dict:
    """Все выборки; возвращает сводку (её же проверяет тест на придуманной базе)."""
    п = параметры() | {"feed": FEED_КП}
    итог: dict = {}

    # ── спрос частями ────────────────────────────────────────────────────────
    спрос = collections.defaultdict(lambda: [0, 0, 0])
    образцы_спроса = []
    for i in range(частей):
        cur.execute(СПРОС, п | {"n": частей, "i": i})
        for класс, строк, ключей, больше in cur.fetchall():
            с = спрос[класс or "годен"]
            с[0] += ч(строк)
            с[1] += ч(ключей)
            с[2] += ч(больше)
        cur.execute(СПРОС_ОБРАЗЦЫ, п | {"n": частей, "i": i})
        образцы_спроса.extend(cur.fetchall())
    итог["спрос"] = {к: {"строк": v[0], "ключей": v[1], "кол_больше_предела": v[2]}
                     for к, v in спрос.items()}

    # ── цены ─────────────────────────────────────────────────────────────────
    cur.execute(КОЛОНКИ)
    есть = {r[0] for r in cur.fetchall()}
    cur.execute(цены_sql("total" in есть, "note" in есть), п)
    цены = {}
    for кп, класс, строк, ключей, больше, тройка, снято in cur.fetchall():
        цены[("кп" if кп else "прочие", класс or "годен")] = {
            "строк": ч(строк), "ключей": ч(ключей), "кол_больше_предела": ч(больше),
            "тройка_не_сошлась": ч(тройка), "кол_снято_разбором": ч(снято)}
    итог["цены"] = цены
    cur.execute(ЦЕНЫ_ОБРАЗЦЫ, п)
    образцы_цен = cur.fetchall()

    # ── номенклатура и спрос, снятый с ложных карточек ───────────────────────
    cur.execute(СНЯТЫЙ_СПРОС, п)
    ключей, строк_спроса, сделок, худшая = cur.fetchone()
    итог["снятый_спрос"] = {"карточек": ч(ключей), "строк_спроса": ч(строк_спроса),
                            "сделок_по_карточкам": ч(сделок), "сделок_у_худшей": ч(худшая)}

    # ── стало хуже: строки цены без единственного кода ───────────────────────
    cur.execute(ОТВЕРГНУТЫЕ_ЦЕНЫ, п)
    вернёт = нет_кода = 0
    for (имя,) in cur.fetchall():
        if docfilter.part_number_of(имя or ""):
            вернёт += 1
        else:
            нет_кода += 1
    итог["хуже"] = {"строк_без_кода": вернёт + нет_кода, "вернёт_переразбор": вернёт,
                    "кода_нет_вовсе": нет_кода}

    # ── каталог: контроль ложного обвинения ──────────────────────────────────
    cur.execute(КАТАЛОГ, п)
    каталог = cur.fetchall()
    итог["каталог_отвергнуто"] = sum(ч(r[2]) for r in каталог)

    итог["_образцы"] = (образцы_спроса, образцы_цен, каталог)
    return итог


def печать(итог: dict) -> None:
    print("=== ПРАВДОПОДОБНЫЙ КОД: что снимает правило (марка, размер, стандарт) ===")
    print("Код — ключ номера (lib_pn_key). Строки не удаляются: правило действует при"
          " чтении.")
    с = итог["спрос"]
    всего = sum(v["строк"] for v in с.values())
    ключей = sum(v["ключей"] for v in с.values())
    print(f"\nСПРОС (lib_demand_live): строк с кодом {всего}, ключей {ключей}")
    for класс in ("стандарт", "размер", "марка"):
        v = с.get(класс, {"строк": 0, "ключей": 0})
        print(f"  отвергнуто, {класс:<9s}: строк {v['строк']:>9d}, ключей {v['ключей']:>7d}")
    print(f"  строк спроса с количеством больше {МАКС}: "
          f"{sum(v['кол_больше_предела'] for v in с.values())}")

    print("\nЦЕНЫ (lib_prices):")
    for поток in ("кп", "прочие"):
        строки = {к: v for (п, к), v in итог["цены"].items() if п == поток}
        всего = sum(v["строк"] for v in строки.values())
        print(f"  поток {'«' + FEED_КП + '»' if поток == 'кп' else 'прочие'}: строк с кодом {всего},"
              f" ключей {sum(v['ключей'] for v in строки.values())}")
        for класс in ("стандарт", "размер", "марка"):
            v = строки.get(класс, {"строк": 0, "ключей": 0})
            print(f"    отвергнуто, {класс:<9s}: строк {v['строк']:>7d}, ключей {v['ключей']:>6d}")
        print(f"    количество больше {МАКС}: "
              f"{sum(v['кол_больше_предела'] for v in строки.values())};"
              f" «кол-во × цена ≠ сумма»: {sum(v['тройка_не_сошлась'] for v in строки.values())};"
              f" количество снято разбором: "
              f"{sum(v['кол_снято_разбором'] for v in строки.values())}")

    кп = {к: v for (п, к), v in итог["цены"].items() if п == "кп"}
    было = sum(v["ключей"] for v in кп.values())
    снято = sum(v["ключей"] for к, v in кп.items() if к != "годен")
    сн = итог["снятый_спрос"]
    print("\nНОМЕНКЛАТУРА (карточки = ключи потока «разбор КП»):")
    print(f"  карточек было {было}, стало {было - снято}, снято {снято}")
    print(f"  спрос, который ложные карточки собирали по ключу: строк {сн['строк_спроса']},"
          f" сделок (сумма по карточкам) {сн['сделок_по_карточкам']},"
          f" у худшей карточки {сн['сделок_у_худшей']} сделок")

    х = итог["хуже"]
    print("\nСТАЛО ХУЖЕ (правило 0): строки цены, чей единственный код отвергнут, уходят"
          " из номенклатуры")
    print(f"  строк: {х['строк_без_кода']}; из них в наименовании есть другой правдоподобный"
          f" код (вернёт переразбор): {х['вернёт_переразбор']}; кода нет вовсе: "
          f"{х['кода_нет_вовсе']}. Цены этих строк в базе остаются.")

    print(f"\nКАТАЛОГ (контроль ложного обвинения): номеров lib_parts, отвергнутых правилом:"
          f" {итог['каталог_отвергнуто']}"
          + ("" if not итог["каталог_отвергнуто"] else
             " — ПРОВЕРИТЬ: каталог курирован, закрытый список мог задеть настоящие номера"))

    спрос_о, цены_о, каталог_о = итог["_образцы"]
    print("\nОБРАЗЦЫ ОТВЕРГНУТЫХ КЛЮЧЕЙ (цифры → 9; только формы и числа):")
    печать_образцов("спрос", спрос_о)
    печать_образцов("цены «разбор КП»", цены_о)
    печать_образцов("каталог", [(к, о, n) for к, о, n in каталог_о])


def main() -> int:
    dsn = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not dsn:
        print("нет SUPABASE_DB_URL — мерить нечего")
        return 2
    import psycopg2

    частей = max(1, int(os.environ.get("PARTS", "16") or 16))
    conn = psycopg2.connect(dsn, connect_timeout=20,
                            options="-c statement_timeout=1500000 -c work_mem=64MB"
                                    " -c max_parallel_workers_per_gather=0")
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute("select lower('ШАЙБА') = 'шайба'")
            if not cur.fetchone()[0]:
                print("база не складывает регистр кириллицы (локаль C) — ключи считаются"
                      " иначе, чем на живой; замер бессмыслен (CLAUDE.md, правило 21а)")
                return 3
            печать(замер(cur, частей))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
