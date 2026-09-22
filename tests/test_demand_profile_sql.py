"""Профиль спроса считается на настоящем PostgreSQL, ответы посчитаны руками.

ЗАЧЕМ. По этим числам владелец решает, с чего начинать: «сколько позиций даёт
80 % спроса» определяет, работа на двести позиций или на девять тысяч, а «по
чему мы на рынок не выходили» — прямой список упущенного.

Четыре ловушки, каждая даёт правдоподобное, но неверное число:

1. ЧТЕНИЕ ТАБЛИЦЫ ВМЕСТО ЖИВОГО ВИДА. lib_demand_live исключает строки,
   помеченные как текст тендерного документа. Пункт договора, посчитанный
   номенклатурой, завышает и спрос, и «непокрытое»: его никто не спрашивал.
2. СЧЁТ СПРОСА СТРОКАМИ ВМЕСТО СДЕЛОК. Две строки одной сделки об одной позиции
   — это один спрос, а не два. Иначе спецификация с повторами весит больше
   тридцати разных заказчиков.
3. ПОЗИЦИЯ БЕЗ АРТИКУЛА, ПОТЕРЯННАЯ МОЛЧА. Ключа у неё нет, с котировками она не
   сводится — но это спрос, и он обязан быть виден числом.
4. ВОРОНКА БЕЗ ВЗВЕШИВАНИЯ. Доля позиций и доля спросов — разные числа, и первая
   льстит: редкие позиции численно давят частые.

Корпус придуман (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМА = "тест_профиля_спроса"


def скрипт():
    spec = importlib.util.spec_from_file_location(
        "kvant_demand_profile", ROOT / "scripts" / "demand_profile.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def функция_ключа() -> str:
    текст = (ROOT / "library" / "supabase" / "schema_junk.sql").read_text(encoding="utf-8")
    m = re.search(r"create or replace function lib_pn_key.*?\$\$;", текст, re.S | re.I)
    assert m, "в миграции больше нет функции lib_pn_key"
    return m.group(0)


# КОРПУС. Считаем руками.
#
# Живых строк 8 из 9: одна помечена как текст документа.
# Позиции (по ключу): bearing1 — сделки D-1, D-2, D-3 (три);
#                     seal2   — сделки D-1, D-2 (две, причём в D-1 две строки);
#                     nut3    — сделка D-4 (одна);
#                     без артикула — одна строка в D-4.
# Спросов всего (сделок на позицию): 3 + 2 + 1 = 6.
# Котировки есть на bearing1 (две компании, с ценой) и на seal2 (одна компания,
# без цены). На nut3 котировок нет вовсе — это и есть «не выходили на рынок».
КОРПУС = """
create table lib_demand (
  id bigserial primary key, segment_id text, deal_id text, item_name text not null,
  oem text, model text, part_number text, qty numeric, unit text);
create table lib_row_junk (demand_id bigint, revoked_at timestamptz);
create view lib_demand_live as
  select d.* from lib_demand d
   where not exists (select 1 from lib_row_junk j
                      where j.demand_id = d.id and j.revoked_at is null);
create table lib_prices (
  id bigserial primary key, part_number text, feed text, rfq_company text,
  price numeric, currency text);

insert into lib_demand (segment_id, deal_id, item_name, oem, model, part_number, qty, unit) values
  ('насосы',  'D-1', 'Подшипник', 'SKF', 'НМ-125', 'BEARING-1',  10, 'шт'),
  ('насосы',  'D-2', 'Подшипник', 'SKF', 'НМ-125', 'bearing1',    4, 'шт'),
  ('насосы',  'D-3', 'Подшипник', null,  null,     'BEARING-1',   1, 'шт'),
  ('насосы',  'D-1', 'Уплотнение', 'FAG', null,    'SEAL-2',      2, 'шт'),
  ('насосы',  'D-1', 'Уплотнение', 'FAG', null,    'seal2',       3, 'шт'),
  ('насосы',  'D-2', 'Уплотнение', 'FAG', null,    'SEAL-2',      1, 'шт'),
  ('крепёж',  'D-4', 'Гайка',      null,  null,    'NUT-3',      50, 'кг'),
  ('крепёж',  'D-4', 'Нечто',      null,  null,    null,       null, null),
  -- Девятая строка: текст тендерного документа, в живой вид не попадает.
  ('крепёж',  'D-4', 'Пункт 5.2 Условия оплаты', null, null, null, null, null);
insert into lib_row_junk (demand_id, revoked_at)
  select id, null from lib_demand where item_name like 'Пункт 5.2%';

insert into lib_prices (part_number, feed, rfq_company, price, currency) values
  ('BEARING-1', 'разбор КП', '101', 100, 'EUR'),
  ('bearing1',  'разбор КП', '102', 120, 'EUR'),
  ('SEAL-2',    'разбор КП', '101', null, null),
  -- Чужой поток: в воронку попасть не должен.
  ('NUT-3',     'прайс',     '999', 5,   'EUR');
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
    c.execute(функция_ключа())
    c.execute(КОРПУС)
    yield c
    c.execute(f'drop schema if exists "{СХЕМА}" cascade')
    c.close()
    conn.close()


def test_текст_документа_в_спрос_не_идёт(cur):
    м = скрипт()
    cur.execute(м.ОТСЕВ)
    всего, живых = cur.fetchone()
    assert всего == 9
    # Одна строка помечена: пункт договора номенклатурой не считается.
    assert живых == 8


def test_объём_и_позиция_без_артикула_видны(cur):
    м = скрипт()
    cur.execute(м.ОБЪЁМ)
    (строк, сделок, с_артикулом, позиций, с_кол, единиц,
     с_изг, с_маш, сегментов) = cur.fetchone()
    assert строк == 8
    assert сделок == 4                      # D-1 … D-4
    # Семь строк с артикулом, одна без — и она обязана быть видна разницей.
    assert с_артикулом == 7
    assert строк - с_артикулом == 1
    # Три различных ключа: bearing1, seal2, nut3.
    assert позиций == 3
    assert с_кол == 7
    assert единиц == 2                      # шт и кг
    assert с_изг == 5
    assert с_маш == 2
    assert сегментов == 2


def test_спрос_считается_сделками_а_не_строками(cur):
    м = скрипт()
    cur.execute(м.ПОВТОРЯЕМОСТЬ)
    (поз, разовых, повторных, от_пяти, мед, p90, макс) = cur.fetchone()
    assert поз == 3
    # bearing1 — три сделки, seal2 — две (хотя строк в D-1 две), nut3 — одна.
    # Считай строками — у seal2 стало бы три, и максимум сравнялся бы с bearing1.
    assert разовых == 1
    assert повторных == 2
    assert от_пяти == 0
    assert макс == 3
    assert (мед, p90) == (2, 3)


def test_концентрация_считает_позиции_а_не_проценты_строк(cur):
    м = скрипт()
    # Спросов всего 3 + 2 + 1 = 6. Отсортировано по убыванию: 3, 2, 1.
    # 50 % (3) даёт первая позиция; 80 % (4,8) — две; 95 % (5,7) — три.
    for порог, ожидание in ((0.50, 1), (0.80, 2), (0.95, 3)):
        cur.execute(м.КОНЦЕНТРАЦИЯ, (порог, порог))
        _, нужно, всего = cur.fetchone()
        assert (нужно, всего) == (ожидание, 3), порог


def test_воронка_непокрытого_и_её_взвешивание(cur):
    м = скрипт()
    cur.execute(м.НЕПОКРЫТОЕ, (м.FEED,))
    (поз, запрашивали, есть_цена, есть_выбор,
     сп_всего, сп_запр, сп_цена, сп_выбор) = cur.fetchone()
    assert поз == 3
    # Запрашивали bearing1 и seal2. По nut3 котировок нет: строка на него лежит
    # в чужом потоке «прайс», и она в счёт не идёт.
    assert запрашивали == 2
    assert есть_цена == 1                   # только bearing1
    assert есть_выбор == 1                  # у bearing1 две компании
    # ВЗВЕШЕННАЯ воронка даёт другие доли, и в этом её смысл: по позициям
    # запрашивали 2 из 3 (67 %), а по спросам — 5 из 6 (83 %).
    assert сп_всего == 6
    assert сп_запр == 5
    assert сп_цена == 3                     # bearing1 весит три сделки
    assert сп_выбор == 3
    assert (сп_запр / сп_всего) > (запрашивали / поз), \
        "взвешивание не меняет картину — значит считается то же самое"


def test_разрезы_по_сегменту_и_изготовителю(cur):
    м = скрипт()
    cur.execute(м.ПО_СЕГМЕНТАМ)
    сег = {r[0]: r[1:] for r in cur.fetchall()}
    assert сег["насосы"] == (6, 3, 2)       # строк 6, сделок 3, позиций 2
    assert сег["крепёж"] == (2, 1, 1)       # текст документа сюда не попал
    cur.execute(м.ПО_ИЗГОТОВИТЕЛЯМ)
    # Порог «от пяти сделок» никого не пропускает: в корпусе их максимум три.
    assert cur.fetchall() == []
