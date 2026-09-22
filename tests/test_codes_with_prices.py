"""«Сколько кодов с ценой и без» — три разных ответа, и подменять их нельзя.

ЗАЧЕМ. Вопрос владельца, 22.09.2026. У него три прочтения, и каждое своё:

  · коды, которые МЫ СПРАШИВАЛИ — их и надо закрывать ценой, это наша работа;
  · коды, по которым ЦЕНА ЕСТЬ — среди них бывают такие, которых мы не спрашивали;
  · коды КАТАЛОГА — наша библиотека деталей, пересекается с первыми лишь частью.

Ответ уходит владельцу как факт, поэтому проверяется то, что даёт НЕВЕРНОЕ ЧИСЛО.

Четыре ловушки, каждая уже срабатывала в этом проекте:

1. КОД — ЭТО КЛЮЧ, А НЕ НАПИСАНИЕ. «AAA-111», «AAA 111» и «aaa111» — один код.
   По написаниям кодов выходит втрое больше, чем есть.
2. СПРОС ЧИТАЕТСЯ ИЗ lib_demand_live. Пункты договоров, попавшие в спрос и уже
   помеченные мусором, иначе станут «кодами без цены» и раздуют знаменатель.
   Ровно этой подменой замер перекрёстной системы однажды посчитал спрос.
3. ЦЕНА БЫВАЕТ НЕ ТОЛЬКО ИЗ ПРЕДЛОЖЕНИЯ. lib_prices держит и прайсы, и таможню.
   «Есть цена вообще» и «поставщик ответил на наш запрос» — разные утверждения.
4. РАЗРЯД, КОТОРЫЙ НЕ МОЖЕТ БЫТЬ НЕПУСТЫМ, — тавтология. Счётчик «без выбора и со
   спросом» однажды равнялся «без выбора» целиком.

Корпус придуман (CLAUDE.md, правило 18), ответы посчитаны руками.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМА = "тест_кодов_с_ценами"


def скрипт():
    spec = importlib.util.spec_from_file_location(
        "kvant_codes", ROOT / "scripts" / "codes_with_prices.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# КОРПУС. Считаем руками.
#
# СПРОС (lib_demand_live — строка 4 помечена мусором и не считается):
#   AAA-111 → ключ aaa111   · есть цена предложения          → с ценой КП
#   AAA 111 → ключ aaa111   · ТО ЖЕ САМОЕ, один код, не два
#   BBB222  → ключ bbb222   · цена только из потока «прайс»   → чужой поток
#   CCC333  → ключ ccc333   · ПОМЕЧЕН МУСОРОМ, в счёт не идёт
#   DDD444  → ключ ddd444   · цены нет вовсе                  → без цены
#   EEE555  → ключ eee555   · цены нет вовсе                  → без цены
#   xx      → ключ xx       · короткий ключ, но считается: отсев по длине >= 2
#
# Итого кодов спроса: aaa111, bbb222, ddd444, eee555, xx = ПЯТЬ
#   с ценой КП        — 1 (aaa111)
#   чужой поток       — 1 (bbb222)
#   без цены вовсе    — 3 (ddd444, eee555, xx)
#
# ЦЕНЫ ПРЕДЛОЖЕНИЙ: aaa111 (спрашивали, есть в каталоге), zzz999 (НЕ спрашивали)
# КАТАЛОГ: aaa111 (с ценой, спрашивали), kkk777 (ни цены, ни спроса)
КОРПУС = """
create function lib_pn_key(t text) returns text language sql immutable as $$
  select left(regexp_replace(replace(lower(coalesce(t, '')), 'ё', 'е'),
                             '[^0-9a-zа-я]', '', 'g'), 80)
$$;

create table lib_demand (
  id bigserial primary key, deal_id text, item_name text, part_number text);
create table lib_row_junk (
  demand_id bigint primary key, rule text, run_id text, revoked_at timestamptz);
create view lib_demand_live as
  select d.* from lib_demand d
   where not exists (select 1 from lib_row_junk j
                      where j.demand_id = d.id and j.revoked_at is null);
create table lib_prices (
  id bigserial primary key, feed text, part_number text, price numeric);
create table lib_parts (id text primary key, catalog_no text);

insert into lib_demand (id, deal_id, part_number) values
  (1, 'D1', 'AAA-111'), (2, 'D2', 'AAA 111'), (3, 'D1', 'BBB222'),
  (4, 'D1', 'CCC333'), (5, 'D2', 'DDD444'), (6, 'D2', 'EEE555'),
  (7, 'D1', 'xx');
insert into lib_row_junk (demand_id, rule, run_id) values (4, 'проза', 'прогон-1');

insert into lib_prices (feed, part_number, price) values
  ('разбор КП', 'AAA-111', 100), ('разбор КП', 'aaa111', 110),
  ('разбор КП', 'ZZZ999', 200),
  ('прайс',     'BBB-222', 300);

insert into lib_parts (id, catalog_no) values
  ('p1', 'AAA111'), ('p2', 'KKK777');
"""


@pytest.fixture()
def cur():
    import psycopg2
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    c = conn.cursor()
    c.execute(f'drop schema if exists "{СХЕМА}" cascade')
    c.execute(f'create schema "{СХЕМА}"')
    c.execute(f'set search_path to "{СХЕМА}"')
    c.execute(КОРПУС)
    yield c
    c.execute(f'drop schema if exists "{СХЕМА}" cascade')
    c.close()
    conn.close()


def подготовь(cur, м):
    cur.execute(м.ПОДГОТОВКА, (м.FEED_КП,))


def test_разные_написания_одного_номера_дают_один_код(cur):
    """Ловушка 1: по написаниям кодов выходит больше, чем есть."""
    м = скрипт()
    подготовь(cur, м)
    cur.execute(м.ОБЪЁМ)
    кс, стрс, ккп, стркп, кц, кт = [int(x) for x in cur.fetchone()]
    # Пять кодов спроса из шести живых строк: AAA-111 и AAA 111 — один код.
    assert кс == 5, "разные написания посчитаны как разные коды"
    assert стрс == 6, "строк спроса шесть: помеченная мусором не считается"
    # Цены предложений: aaa111 (две строки) и zzz999 — два кода, три строки.
    assert (ккп, стркп) == (2, 3)
    # Цены всех потоков: aaa111, zzz999, bbb222 — три кода.
    assert кц == 3
    assert кт == 2


def test_помеченный_мусор_не_становится_кодом_без_цены(cur):
    """Ловушка 2: иначе знаменатель раздут, а доля закрытых занижена."""
    м = скрипт()
    подготовь(cur, м)
    cur.execute("select count(*) from коды_спроса where ключ = 'ccc333'")
    assert cur.fetchone()[0] == 0, "код из помеченной мусором строки попал в счёт"


def test_главный_ответ_разложен_на_три_разряда(cur):
    """Сколько спрошенных кодов с ценой поставщика, сколько без цены вовсе."""
    м = скрипт()
    подготовь(cur, м)
    cur.execute(м.СПРОС_С_ЦЕНОЙ)
    всего, с_кп, чужой, без, стр_с, стр_без = [int(x) for x in cur.fetchone()]
    assert всего == 5
    assert с_кп == 1, "цена предложения только у aaa111"
    assert чужой == 1, "у bbb222 цена есть, но из потока «прайс» — это не ответ нам"
    assert без == 3, "ddd444, eee555, xx — ни одной цены"
    # Разряды взаимоисключающие и покрывают всё.
    assert с_кп + чужой + без == всего
    # Строки спроса: за aaa111 их две (два написания).
    assert стр_с == 2
    assert стр_без == 3


def test_обратная_сторона_коды_с_ценой(cur):
    """Поставщик присылает прайс шире запроса — это отдельный разряд."""
    м = скрипт()
    подготовь(cur, м)
    cur.execute(м.ЦЕНЫ_ПРОТИВ_СПРОСА)
    кодов, спраш, не_спраш, в_кат = [int(x) for x in cur.fetchone()]
    assert кодов == 2
    assert спраш == 1, "aaa111 спрашивали"
    assert не_спраш == 1, "zzz999 не спрашивали — прайс шире запроса"
    assert спраш + не_спраш == кодов
    assert в_кат == 1, "в каталоге есть только aaa111"


def test_каталог_считается_отдельной_вселенной(cur):
    м = скрипт()
    подготовь(cur, м)
    cur.execute(м.КАТАЛОГ)
    кат, с_ценой, спраш = [int(x) for x in cur.fetchone()]
    assert кат == 2
    assert с_ценой == 1
    assert спраш == 1
    # kkk777 — ни цены, ни спроса: каталог шире того, что мы спрашиваем.
    assert кат - спраш == 1


def test_ни_один_разряд_не_тавтологичен(cur):
    """Ловушка 4: разряд, который не может быть непустым, ничего не измеряет.

    Корпус нарочно даёт непустое значение КАЖДОМУ разряду. Если разряд окажется
    нулём здесь, значит он не может быть непустым вообще, и его надо убирать или
    переписывать, а не оставлять украшением.
    """
    м = скрипт()
    подготовь(cur, м)
    cur.execute(м.СПРОС_С_ЦЕНОЙ)
    всего, с_кп, чужой, без, _, _ = [int(x) for x in cur.fetchone()]
    for имя, v in (("всего", всего), ("с ценой КП", с_кп),
                   ("чужой поток", чужой), ("без цены", без)):
        assert v > 0, f"разряд «{имя}» пуст — он не может быть непустым?"
    cur.execute(м.ЦЕНЫ_ПРОТИВ_СПРОСА)
    for имя, v in zip(("кодов", "спрашивали", "не спрашивали", "в каталоге"),
                      [int(x) for x in cur.fetchone()]):
        assert v > 0, f"разряд «{имя}» пуст"


def test_оценка_правдоподобия_не_отбрасывает_строк(cur):
    """Это ОЦЕНКА, а не факт: счётчик описывает, но ничего не выкидывает."""
    м = скрипт()
    подготовь(cur, м)
    cur.execute(м.ПРАВДОПОДОБНОСТЬ)
    в, прав, без_ц, кор, длин = [int(x) for x in cur.fetchone()]
    # Всего — столько же кодов, сколько в главном ответе: оценка ничего не режет.
    assert в == 5
    # Правдоподобны четыре: aaa111, bbb222, ddd444, eee555. «xx» — нет.
    assert прав == 4
    assert кор == 1, "«xx» короче четырёх знаков"
    assert без_ц == 1, "«xx» без единой цифры"
    assert длин == 0
