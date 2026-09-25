"""Замер правила «правдоподобный код» выполняется на PostgreSQL и считает верно.

Скрипт читает живую базу из Actions, и опечатка в запросе видна там только
упавшим прогоном. Здесь все его запросы идут на придуманном корпусе (правило 18),
ответы посчитаны руками. Проверяется и главное свойство замера: журнал несёт
только агрегаты — ни наименований, ни самих номеров (правило 17).
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМА = "тест_замера_правдоподобного_кода"

КОРПУС = """
create table lib_demand (id bigserial primary key, deal_id text, item_name text,
                         part_number text, qty numeric);
create table lib_row_junk (demand_id bigint, revoked_at timestamptz);
create view lib_demand_live as
  select d.* from lib_demand d
   where not exists (select 1 from lib_row_junk j
                      where j.demand_id = d.id and j.revoked_at is null);
create table lib_prices (id bigserial primary key, feed text, part_number text,
                         item_name text, price numeric, qty numeric, total numeric,
                         note text, part_id text);
create table lib_parts (id text primary key, catalog_no text);

insert into lib_demand (deal_id, item_name, part_number, qty) values
  ('D-1', 'Учебная труба', 'SS316', 8),
  ('D-2', 'Учебный фланец', 'ss 316', 2),
  ('D-3', 'Учебный лист', 'AISI 304', 1),
  ('D-4', 'Учебная прокладка', 'DN50', 3),
  ('D-5', 'Учебный болт', 'DIN 933', 100),
  ('D-6', 'Учебный подшипник', '6205-2RS', 3163518182.316),
  ('D-7', 'Учебный подшипник', 'NU 316 ECP', 1),
  -- Стандарт с размером — номер детали; тот же ключ слитно — голый стандарт.
  ('D-8', 'Учебное кольцо', 'ГОСТ 9833-73 020-025-30', 4),
  ('D-9', 'Учебное кольцо', 'DIN 471 25', 10),
  ('D-10', 'Учебное кольцо', 'DIN 471 25', 5),
  ('D-11', 'Учебное кольцо', 'DIN47125', 2),
  -- «Стандарт + год» через пробел: сомнительный возврат, хвост — год.
  ('D-12', 'Учебная система', 'ISO 9001 2015', 1);

insert into lib_prices (feed, part_number, item_name, price, qty, total, note) values
  -- Код отвергнут, другого кода в наименовании нет: ушла из номенклатуры.
  ('разбор КП', 'SS316', '(19mm) SS316 8 3200.0 25600.0', 316, 3163518182.316, 316, null),
  -- Код отвергнут, но в наименовании есть настоящий: переразбор вернёт.
  ('разбор КП', '12Х18Н10Т', 'Клапан 12Х18Н10Т KV-4417-B', 10, 2, 20, null),
  -- Годный код, тройка не сошлась: 8 × 100 ≠ 900.
  ('разбор КП', '6205-2RS', 'Учебный подшипник', 100, 8, 900, null),
  -- Годный код, количество снято разбором (оговорка разборщика).
  ('разбор КП', 'NU 316 ECP', 'Учебный подшипник', 5, null, 5,
   'количество не сошлось с ценой и суммой — не записано'),
  -- Чужой поток.
  ('прайс', 'DN50', 'Учебная прокладка', 1, 1, 1, null);

-- Стандарт с размером в КП: карточка номенклатуры вернётся; у строки с деталью
-- каталога код сменится с part_id на ключ номера — «стало хуже».
insert into lib_prices (feed, part_number, item_name, price, qty, total, note, part_id) values
  ('разбор КП', 'DIN 471 25', 'Учебное кольцо', 3, 10, 30, null, 'выдум-кольцо'),
  ('разбор КП', 'GB 276 6205', 'Учебный подшипник', 7, 1, 7, null, null),
  ('разбор КП', 'GB 276 6205', 'Учебный подшипник', 8, 1, 8, null, null);

insert into lib_parts values ('6205', '6205-2RS'), ('nu316', 'NU 316 ECP'),
  ('выдум-кольцо', 'ВЫДУМ-КОЛЬЦО'), ('gost983373', 'ГОСТ 9833-73');
"""


def модуль():
    spec = importlib.util.spec_from_file_location(
        "kvant_code_rule_measure_t", ROOT / "scripts" / "code_rule_measure.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def функция_ключа() -> str:
    import re
    текст = (ROOT / "library" / "supabase" / "schema.sql").read_text(encoding="utf-8")
    m = re.search(r"create or replace function lib_pn_key\(.*?\$\$;", текст, re.S | re.I)
    assert m
    return m.group(0)


@pytest.fixture(scope="module")
def итог():
    import psycopg2
    мод = модуль()
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'drop schema if exists "{СХЕМА}" cascade')
            cur.execute(f'create schema "{СХЕМА}"')
            cur.execute(f'set search_path to "{СХЕМА}"')
            # Только lib_pn_key: замер не зависит от того, применена ли функция
            # lib_pn_plausible, — выражение приходит параметром из docfilter.
            cur.execute(функция_ключа())
            cur.execute(КОРПУС)
            yield мод, мод.замер(cur, 3)
    finally:
        with conn.cursor() as cur:
            cur.execute(f'drop schema if exists "{СХЕМА}" cascade')
        conn.close()


def test_спрос_по_классам(итог):
    _, и = итог
    с = и["спрос"]
    assert с["марка"] == {"строк": 3, "ключей": 2, "кол_больше_предела": 0}
    # Стандарт — только голый: «DIN 933» и слитное «DIN47125».
    assert с["размер"]["строк"] == 1 and с["стандарт"] == {"строк": 2, "ключей": 2,
                                                         "кол_больше_предела": 0}
    # Годны два номера подшипника и три стандарта с размером (четыре строки).
    assert с["годен"] == {"строк": 6, "ключей": 5, "кол_больше_предела": 1}


def test_цены_номенклатура_и_стало_хуже(итог):
    _, и = итог
    ц = и["цены"]
    assert ц[("кп", "марка")]["строк"] == 2
    assert ц[("кп", "марка")]["кол_больше_предела"] == 1
    assert ц[("кп", "годен")]["тройка_не_сошлась"] == 1
    assert ц[("кп", "годен")]["кол_снято_разбором"] == 1
    assert ц[("прочие", "размер")]["строк"] == 1
    # Две ложные карточки (ss316, 12х18н10т); ss316 собирала две сделки спроса.
    assert и["снятый_спрос"] == {"карточек": 2, "строк_спроса": 2,
                                 "сделок_по_карточкам": 2, "сделок_у_худшей": 2}
    assert и["хуже"] == {"строк_без_кода": 2, "вернёт_переразбор": 1, "кода_нет_вовсе": 1}
    # Голый «ГОСТ 9833-73» в каталоге правило отвергло бы — контроль его видит.
    assert и["каталог_отвергнуто"] == 1


def test_стандарт_с_размером(итог):
    _, и = итог
    ст = и["стандарт"]
    с = ст["спрос"]["счёт"]
    assert с["ключей"] == 4                       # гост…, din47125, din933, iso90012015
    assert с["ключей_только_голых"] == 1 and с["ключей_смешанных"] == 1
    assert с["ключей_только_размер"] == 2
    assert с["вернулось_ключей"] == 3 and с["вернулось_строк"] == 4
    # Две сделки у din47125 по написанию с размером (слитное — третья сделка).
    assert с["сделок_у_худшего"] == 2 and с["сделок_у_вернувшихся"] == 4
    assert с["хвост_год"] == 1 and с["строк_голых_у_вернувшихся"] == 1
    к = ст["цены"]["кп"]["счёт"]
    assert к["вернулось_ключей"] == 2 and к["вернулось_строк"] == 3
    assert к["уходят_от_детали"] == 1
    # «gost983373» в каталоге — голый стандарт, защищён всегда.
    assert ст["каталог_ключей"] == 1
    # Сетка длины: у din голый din933 (3 цифры) и din47125 с размером (5) —
    # предел 3 разделяет их без ошибок; у iso один ключ с размером.
    assert ст["сетка"]["din"]["ложный_код"] == 0 and ст["сетка"]["din"]["выброшен"] == 0


def test_журнал_только_агрегаты(итог, capsys):
    мод, и = итог
    мод.печать(и)
    журнал = capsys.readouterr().out
    assert "СТАЛО ХУЖЕ" in журнал and "ss999" in журнал
    assert "СТАНДАРТ С РАЗМЕРОМ" in журнал and "din 999 99" in журнал
    for запрещено in ("Учебн", "KV-4417", "SS316", "Клапан", "(19mm)", "471", "DIN 471",
                      "9833", "ВЫДУМ"):
        assert запрещено not in журнал, запрещено
