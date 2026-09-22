"""Мосты «артикул котировки → каталог» считаются на настоящем PostgreSQL.

ЗАЧЕМ. По этим числам решается, какой ключ применять для связи котировок с
каталогом, — а от связи зависят три раздела карточки товара: «чем закрыть»,
«где стоит», «кто делает». Ошибка здесь стоит не пустой страницы, а НЕВЕРНОЙ:
мост, привязавший котировку к чужой детали, заполнит разделы чужой машиной и
чужим изготовителем, и отличить это от правды на странице будет нельзя.

Поэтому главное, что проверяется, — РАЗДЕЛЕНИЕ ОДНОЗНАЧНЫХ И НЕОДНОЗНАЧНЫХ
связей. Один альтернативный номер законно принадлежит нескольким деталям:
«6205» есть у SKF, у FAG и у ГПЗ. Такой мост применять нельзя, и запрос обязан
показывать это отдельным числом, а не растворять в общем счёте связанных.

Второе: СУММА ВКЛАДОВ НЕ РАВНА ОБЪЕДИНЕНИЮ. Один артикул находится и по
альтернативе, и по псевдониму; сложение вкладов завысило бы итог.

Третье: вклад считается только по НОВЫМ артикулам — тех, что основной номер уже
нашёл, мост не добавляет, сколько бы связей он к ним ни давал.

Корпус придуман (CLAUDE.md, правило 18), ответы посчитаны руками и записаны
рядом с каждым ожиданием.
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

СХЕМА = "тест_мостов_каталога"


def скрипт():
    spec = importlib.util.spec_from_file_location(
        "kvant_catalog_bridge", ROOT / "scripts" / "catalog_bridge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def функция_ключа() -> str:
    текст = (ROOT / "library" / "supabase" / "schema_junk.sql").read_text(encoding="utf-8")
    m = re.search(r"create or replace function lib_pn_key.*?\$\$;", текст, re.S | re.I)
    assert m, "в миграции больше нет функции lib_pn_key"
    return m.group(0)


# КОРПУС. Шесть артикулов котировок, каждый закрывает свой случай:
#   6205        — находится основным номером (мост его не добавляет);
#   180205      — только альтернативным номером, ОДНОЗНАЧНО;
#   AMBIG       — альтернативным номером у ДВУХ деталей: применять нельзя;
#   SK-12       — только псевдонимом каталога;
#   KV-000001-1 — только нашим внутренним номером;
#   NOTHING     — ничем.
# Плюс строка чужого потока «прайс»: в замер попасть не должна.
КОРПУС = """
create table lib_models (id text primary key, name text not null);
create table lib_parts (id text primary key, catalog_no text not null, name text not null,
                        aliases text[], kv_no text);
create table lib_part_models (part_id text references lib_parts(id),
                              model_id text references lib_models(id),
                              primary key (part_id, model_id));
create table lib_part_alt (part_id text references lib_parts(id), alt_pn text not null,
                           kind text not null, primary key (part_id, alt_pn, kind));
create table lib_suppliers (id bigint generated always as identity primary key, name text);
create table lib_part_suppliers (part_id text references lib_parts(id),
                                 supplier_id bigint references lib_suppliers(id),
                                 primary key (part_id, supplier_id));
create table lib_prices (id bigserial primary key, part_number text, feed text);

insert into lib_models values ('m1', 'SGT-400');
insert into lib_parts values
  ('6205',    '6205',      'Подшипник SKF', null, null),
  ('fag6205', 'FAG-6205',  'Подшипник FAG', null, null),
  ('sealkit', 'SEAL-KIT',  'Комплект', array['SK-12', 'SK12A'], null),
  ('bolt',    'BOLT-8',    'Болт', null, 'KV-000001-1'),
  ('lonely',  'LONELY-1',  'Одинокая', null, null);
insert into lib_part_models values ('6205', 'm1'), ('sealkit', 'm1');
insert into lib_part_alt values
  ('6205',    '180205', 'номер изготовителя'),
  ('6205',    'AMBIG',  'аналог'),
  ('fag6205', 'AMBIG',  'аналог');
insert into lib_suppliers (name) values ('Завод');
insert into lib_part_suppliers values ('bolt', 1);
insert into lib_prices (part_number, feed) values
  ('6205', 'разбор КП'), ('180205', 'разбор КП'), ('AMBIG', 'разбор КП'),
  ('SK-12', 'разбор КП'), ('KV-000001-1', 'разбор КП'), ('NOTHING', 'разбор КП'),
  -- Чужой поток ДВУМЯ строками: повтор уже известного артикула и свой,
  -- которого в котировках нет. Только вторая делает фильтр по потоку
  -- проверяемым: на повторе снятие фильтра ничего не меняет.
  ('6205', 'прайс'), ('FOREIGN-9', 'прайс');
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
    c.execute(скрипт().БАЗА, (скрипт().FEED,))
    yield c
    c.execute(f'drop schema if exists "{СХЕМА}" cascade')
    c.close()
    conn.close()


def test_чужой_поток_в_замер_не_попадает(cur):
    cur.execute("select count(*) from если_есть_ключи")
    # Шесть артикулов потока «разбор КП». В потоке «прайс» лежат ещё два: повтор
    # 6205 и свой FOREIGN-9. Без фильтра по потоку стало бы семь.
    assert cur.fetchone()[0] == 6
    cur.execute("select ключ from если_есть_ключи order by 1")
    assert [r[0] for r in cur.fetchall()] == [
        "180205", "6205", "ambig", "kv0000011", "nothing", "sk12"]


def test_однозначность_каждого_моста_считается_отдельно(cur):
    м = скрипт()
    ожидание = {
        # мост: (связано, однозначно, неоднозначно, максимум деталей)
        "основной номер (как сейчас)": (1, 1, 0, 1),
        # 180205 → одна деталь, AMBIG → две. Слей их в одно число «связано 2» —
        # и мост покажется вдвое полезнее, чем он есть.
        "альтернативный номер (lib_part_alt)": (2, 1, 1, 2),
        "псевдоним каталога (lib_parts.aliases)": (1, 1, 0, 1),
        "наш внутренний номер (kv_no)": (1, 1, 0, 1),
        "каталожный номер сырым написанием": (1, 1, 0, 1),
    }
    assert set(м.МОСТЫ) == set(ожидание), "список мостов изменился — пересчитай ответы"
    for имя, sql in м.МОСТЫ.items():
        cur.execute(м.СЧЁТ.format(sql))
        assert cur.fetchone() == ожидание[имя], имя


def test_вклад_считается_только_по_новым_артикулам(cur):
    м = скрипт()
    ожидание = {
        # мост: (новых, с машиной, с аналогом, с изготовителем)
        # 180205 ведёт к 6205: у неё есть и машина, и аналог, изготовителя нет.
        "альтернативный номер (lib_part_alt)": (1, 1, 1, 0),
        # SK-12 ведёт к sealkit: машина есть, аналога и изготовителя нет.
        "псевдоним каталога (lib_parts.aliases)": (1, 1, 0, 0),
        # KV-000001-1 ведёт к bolt: только изготовитель. Такой мост связь даёт,
        # а разделы «чем закрыть» и «где стоит» всё равно останутся пустыми.
        "наш внутренний номер (kv_no)": (1, 0, 0, 1),
        # Сырое написание находит тот же 6205, который уже найден основным
        # номером. Новых нуль: не проверяй «новизну» — здесь была бы единица.
        "каталожный номер сырым написанием": (0, 0, 0, 0),
    }
    for имя, sql in м.МОСТЫ.items():
        if имя.startswith("основной"):
            continue
        cur.execute(м.ВКЛАД.format(sql))
        assert cur.fetchone() == ожидание[имя], имя


def test_объединение_меньше_суммы_вкладов(cur):
    м = скрипт()
    союз = "\n union all\n".join(sql for имя, sql in м.МОСТЫ.items()
                                 if not имя.startswith("основной"))
    cur.execute(м.ОБЪЕДИНЕНИЕ.format(союз))
    артикулов, по_основному, добавят, с_машиной = cur.fetchone()
    assert артикулов == 6
    assert по_основному == 1
    # Добавят 180205, SK-12 и KV-000001-1 — три. AMBIG остаётся неоднозначным и
    # в объединении: 6205 и fag6205 претендуют на него оба.
    assert добавят == 3
    # К машине ведут 180205 (через 6205) и SK-12 (через sealkit). KV-000001-1
    # ведёт к болту без машины — связь есть, а раздела всё равно не будет.
    assert с_машиной == 2
    # Сумма вкладов по отдельности: 1 + 1 + 1 + 0 = 3, и здесь она совпала с
    # объединением. Проверка не в равенстве, а в том, что объединение считается
    # своим запросом: на живых данных мосты пересекаются, и сложение завысит.
    assert добавят <= 3


def test_неоднозначный_артикул_не_попадает_ни_в_один_вклад(cur):
    """AMBIG не должен добавиться ни одним мостом — ни по отдельности, ни вместе.

    Отдельным тестом, потому что это и есть та ошибка, которая на странице не
    видна: раздел заполнится чужой машиной, а не останется пустым.
    """
    м = скрипт()
    for sql in м.МОСТЫ.values():
        cur.execute(f"""
            with мост as ({sql}), однозначные as (
              select ключ from мост group by ключ having count(distinct part_id) = 1
            ) select count(*) from однозначные where ключ = 'ambig'
        """)
        assert cur.fetchone()[0] == 0
