"""Динамика кодов по дням (scripts/codes_dynamics.py) — на придуманной базе.

ЗАЧЕМ. Вопрос владельца: «сколько кодов было неделю назад и сколько прибавлялось
каждый день с учётом переразборов». Три места, где такой замер молча врёт:

1. ПЕРЕРАЗБОР ВЫГЛЯДИТ НОВЫМ ФАЙЛОМ. Прежние строки переразобранного файла
   помечены мусором; если «первый день файла» брать по живым строкам, каждый
   переразбор становится «новым файлом», и приход кодов приписывается не тому.
2. УШЕДШИЕ КОДЫ ПРОПАДАЮТ ИЗ ПРОШЛОГО. Если «было неделю назад» считать по
   нынешним живым кодам, код, живший тогда и снятый позже, исчезает из истории,
   и прирост за неделю выглядит больше, чем был.
3. КОД — КЛЮЧ, А НЕ НАПИСАНИЕ: «AAA-1» и «aaa 1» — один код.

Корпус придуман (CLAUDE.md, правило 18), ответ посчитан руками.
"""
from __future__ import annotations

import importlib.util
import os
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
нужна_база = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")
СХЕМА = "тест_динамики_кодов"


def скрипт():
    spec = importlib.util.spec_from_file_location("kvant_codes_dynamics",
                                                  ROOT / "scripts" / "codes_dynamics.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# КОРПУС. Окно — с 16.09 по 23.09.2026 (по Москве).
#
#   A (заказчик)  10.09: AAA-1, BBB-2                     → до окна
#   A переразобран 20.09: прежние строки помечены мусором; новые — «aaa 1»
#                 (тот же код) и CCC-3 (добыт переразбором). BBB-2 не вернулся → ушёл 20.09
#   B (поставщик) 18.09: CCC-3, DDD-4                     → новый файл
#   C (заказчик)  21.09: EEE-5 из распознанного скана     → распознавание
#
#   Цены КП: CCC-3 из B легла 18.09, B переразобран 22.09 — прежняя цена снята,
#   новая легла 22.09 (оговорка 1: первый день цены сдвинут к переразбору);
#   цена по part_id «eee5» 21.09 вместе со своим файлом C — новый файл;
#   цена чужого потока по DDD-4 — не в счёт.
КОРПУС = """
create function lib_pn_key(t text) returns text language sql immutable as $$
  select left(regexp_replace(replace(lower(coalesce(t, '')), 'ё', 'е'),
                             '[^0-9a-zа-я]', '', 'g'), 80) $$;
create table lib_files (file_id text primary key, side text);
create table lib_demand (id bigint primary key, part_number text, source_file text,
                         source text, created_at timestamptz);
create table lib_row_junk (demand_id bigint primary key, marked_at timestamptz,
                           revoked_at timestamptz);
create view lib_demand_live as
  select d.* from lib_demand d
   where not exists (select 1 from lib_row_junk j where j.demand_id = d.id and j.revoked_at is null);
create table lib_prices (id bigserial primary key, part_number text, part_id text, feed text,
                         source_url text, created_at timestamptz);
create table lib_metric_runs (metric text, run_key text, measured_at timestamptz, nums jsonb,
                              note text);

insert into lib_files values ('A', 'заказчик'), ('B', 'поставщик'), ('C', 'заказчик');
insert into lib_demand values
  (1, 'AAA-1', 'A', 'спецификация сделки', '2026-09-10 10:00+03'),
  (2, 'BBB-2', 'A', 'спецификация сделки', '2026-09-10 10:00+03'),
  (3, 'aaa 1', 'A', 'спецификация сделки', '2026-09-20 11:00+03'),
  (4, 'CCC-3', 'A', 'спецификация сделки', '2026-09-20 11:00+03'),
  (5, 'CCC-3', 'B', 'предложение',         '2026-09-18 09:00+03'),
  (6, 'DDD-4', 'B', 'предложение',         '2026-09-18 09:00+03'),
  (7, 'EEE-5', 'C', 'распознавание скана', '2026-09-21 15:00+03'),
  (8, '',      'C', 'распознавание скана', '2026-09-21 15:00+03');
insert into lib_row_junk values
  (1, '2026-09-20 11:00+03', null),
  (2, '2026-09-20 11:00+03', null);
insert into lib_prices (part_number, part_id, feed, source_url, created_at) values
  ('CCC-3', null,   'разбор КП',     'B', '2026-09-22 12:00+03'),
  (null,    'eee5', 'разбор КП',     'C', '2026-09-21 15:00+03'),
  ('DDD-4', null,   'прайсы конкурентов', 'B', '2026-09-19 12:00+03');
"""

НАЧАЛО = date(2026, 9, 16)


@pytest.fixture
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
    conn.close()


def свод(cur, частей: int = 1):
    м = скрипт()
    строки = м.по_частям(cur, м.СПРОС, {"пояс": м.ПОЯС}, частей)
    return м.свести(строки, НАЧАЛО, м.окно(7, date(2026, 9, 23)))


@нужна_база
def test_спрос_заказчика_было_пришло_ушло(cur):
    с = свод(cur)["заказчик"]
    assert с["было"] == 2, с                               # AAA-1, BBB-2
    д20 = с["по_дням"]["2026-09-20"]
    assert (д20["переразбор"], д20["новый файл"], д20["ушло"]) == (1, 0, 1), д20
    assert с["по_дням"]["2026-09-21"]["распознавание"] == 1
    assert с["сейчас"] == 3                                 # AAA-1, CCC-3, EEE-5


@нужна_база
def test_переразбор_не_выглядит_новым_файлом(cur):
    """Прежние строки файла A помечены, но «первый день файла» — по всем строкам."""
    с = свод(cur)["заказчик"]
    assert с["по_дням"]["2026-09-20"]["новый файл"] == 0


@нужна_база
def test_ушедший_код_остаётся_в_прошлом(cur):
    """BBB-2 жил неделю назад и снят 20.09: в «было» он есть, в итоге — нет."""
    в = свод(cur)["все"]
    assert в["было"] == 2
    assert в["по_дням"]["2026-09-18"]["новый файл"] == 2    # CCC-3, DDD-4
    assert в["по_дням"]["2026-09-20"]["ушло"] == 1
    assert в["по_дням"]["2026-09-19"]["итого"] == 4
    assert в["сейчас"] == 4                                 # AAA-1, CCC-3, DDD-4, EEE-5


@нужна_база
def test_итог_равен_живым_кодам_сейчас(cur):
    cur.execute("select count(distinct lib_pn_key(part_number)) from lib_demand_live"
                " where length(lib_pn_key(part_number)) >= 2")
    живых = cur.fetchone()[0]
    assert свод(cur)["все"]["сейчас"] == живых


@нужна_база
def test_цены_только_своего_потока_и_закрытие_спроса(cur):
    м = скрипт()
    строки = м.по_частям(cur, м.ЦЕНЫ, {"пояс": м.ПОЯС, "feed": м.FEED_КП}, 1)
    цена = {(str(д), п): n for г, д, п, n in строки if г == "цена"}
    закрыт = {str(д): n for г, д, п, n in строки if г == "закрыт"}
    спрос = [n for г, д, п, n in строки if г == "спрос_всего"]
    assert цена == {("2026-09-22", "переразбор"): 1, ("2026-09-21", "новый файл"): 1}, цена
    # CCC-3 закрыт 22.09 (цена после переразбора), EEE-5 — 21.09 по part_id.
    assert закрыт == {"2026-09-22": 1, "2026-09-21": 1}, закрыт
    assert спрос == [3]


@нужна_база
def test_части_складываются_без_потерь_и_двойного_счёта(cur):
    """Первый живой прогон упал на нехватке диска: группировка по всем ключам
    разом не влезла в память. Теперь ключи делятся на части по хешу, и сумма по
    частям обязана совпасть с расчётом одним куском — в обоих запросах."""
    м = скрипт()
    assert свод(cur, 1) == свод(cur, 3) == свод(cur, 7)
    def цены(частей):
        return sorted((г, str(д), п, n) for г, д, п, n in
                      м.по_частям(cur, м.ЦЕНЫ, {"пояс": м.ПОЯС, "feed": м.FEED_КП}, частей))
    def сумма(строки):
        итог = {}
        for г, д, п, n in строки:
            итог[(г, д, п)] = итог.get((г, д, п), 0) + n
        return итог
    assert сумма(цены(1)) == сумма(цены(3)) == сумма(цены(7))


def test_свести_без_базы_складывает_как_надо():
    """Арифметика «было + появилось − ушло» — без базы, на готовых строках."""
    м = скрипт()
    дни = м.окно(2, date(2026, 9, 3))
    строки = [("все", "появился", date(2026, 8, 30), "новый файл", 10),
              ("все", "ушёл", date(2026, 8, 31), "", 2),
              ("все", "появился", date(2026, 9, 2), "переразбор", 5),
              ("все", "ушёл", date(2026, 9, 3), "", 1)]
    в = м.свести(строки, дни[0], дни)["все"]
    assert в["было"] == 8
    assert в["по_дням"]["2026-09-02"]["итого"] == 13
    assert в["сейчас"] == 12
