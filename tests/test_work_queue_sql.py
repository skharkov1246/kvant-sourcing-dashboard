"""Очередь работ обязана ЗАКРЫВАТЬСЯ ценой. До 22.09.2026 не закрывалась никогда.

ЗАЧЕМ ЭТО ПРОВЕРЯТЬ. Вид lib_work_queue — список «по этим позициям денег много, а
цены нет». Его читает человек и по нему рассылает запросы. Если позиция из списка
не уходит после того, как цена пришла, человек запрашивает одно и то же заново, а
заметить это нечем: пустая проверка выглядит как «цен ещё нет».

ПОЧЕМУ НЕ СРАБАТЫВАЛО. Условие сравнивало pr.part_id с e.part_id, и обе стороны
пустовали:

  · поток «разбор КП» part_id не пишет вовсе (library/price_store.py кладёт None);
  · у позиции вне каталога e.part_id тоже NULL, а NULL = NULL истиной не бывает.

Корпус придуман (CLAUDE.md, правило 18), ответы посчитаны руками.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМА = "тест_очереди_работ"

# КОРПУС. Четыре позиции, и каждая проверяет свой путь:
#
#   в каталоге, цена пришла      → из очереди УЙТИ (главный случай)
#   вне каталога, цена пришла    → тоже уйти: part_id у неё NULL, и прежняя форма
#                                  оставляла её в очереди навсегда
#   вне каталога, цены нет       → остаться
#   have_price из источника      → остаться вне очереди без всякой цены
#
# Плюс строка цены ЧУЖОГО потока на позицию без цены: если фильтр по потоку снять,
# позиция уйдёт из очереди по прайсу, которого поставщик нам не давал.
КОРПУС = """
create function lib_pn_key(t text) returns text language sql immutable as $$
  select left(regexp_replace(replace(lower(coalesce(t, '')), 'ё', 'е'),
                             '[^0-9a-zа-я]', '', 'g'), 80)
$$;

create table lib_parts (id text primary key, catalog_no text, unit_id text, kv_no text);
create table lib_suppliers (id bigint generated always as identity primary key, name text);
create table lib_part_suppliers (part_id text references lib_parts(id),
                                 supplier_id bigint references lib_suppliers(id),
                                 primary key (part_id, supplier_id));
create table lib_prices (
  id bigserial primary key, feed text, part_number text, part_id text, price numeric);
create table lib_exposure (
  id text primary key, part_id text references lib_parts(id), part_number text not null,
  name text, qty numeric, usd_exposure numeric, have_price boolean default false,
  deal text, file text, file_rows int, file_rows_priced int);

insert into lib_parts values ('aaa111', 'AAA-111', 'ротор', 'KV-000001-8');

insert into lib_exposure (id, part_id, part_number, name, usd_exposure, have_price) values
  ('aaa111', 'aaa111', 'AAA-111', 'в каталоге, цена пришла',   9000, false),
  ('bbb222', null,     'BBB-222', 'вне каталога, цена пришла', 8000, false),
  ('ccc333', null,     'CCC-333', 'вне каталога, цены нет',    7000, false),
  ('ddd444', null,     'DDD-444', 'цена была у источника',     6000, true);

insert into lib_prices (feed, part_number, part_id, price) values
  -- part_id НЕ заполнен: именно так пишет разбор КП.
  ('разбор КП', 'AAA 111', null, 100),
  ('разбор КП', 'bbb-222', null, 200),
  -- Чужой поток на позицию без цены: из очереди её убирать нельзя.
  ('прайс',     'CCC333',  null, 300),
  -- Цена без числа: строка есть, а цены нет — позиция обязана остаться.
  ('разбор КП', 'EEE-555', null, null);
"""


@pytest.fixture()
def cur():
    import psycopg2
    from psycopg2.extensions import parse_dsn

    config = parse_dsn(DSN)
    assert config["host"] in ("127.0.0.1", "localhost")
    assert config["dbname"] == "library_sql_test"
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    c = conn.cursor()
    c.execute(f'drop schema if exists "{СХЕМА}" cascade')
    c.execute(f'create schema "{СХЕМА}"')
    c.execute(f'set search_path to "{СХЕМА}"')
    c.execute(КОРПУС)
    c.execute(вид_из_схемы())
    yield c
    c.execute(f'drop schema if exists "{СХЕМА}" cascade')
    c.close()
    conn.close()


def вид_из_схемы(без_комментариев: bool = False) -> str:
    """Настоящий текст вида из файла миграции, а не его пересказ.

    ГРАНИЦА — ПО «order by … ;», А НЕ ПО ПЕРВОЙ ТОЧКЕ С ЗАПЯТОЙ. Первая версия
    этой функции обрывала текст на «;» внутри SQL-комментария (там есть «);»), и
    вид создавался усечённым: проверки падали не на коде, а на разборе файла.

    С `без_комментариев` строки «--» снимаются: разбор исходника читает код, а не
    пояснения (CLAUDE.md, «Стиль работы»), иначе проверка находит упоминание
    прежней формы в объяснении, почему её убрали.
    """
    текст = (ROOT / "library" / "supabase" / "schema.sql").read_text(encoding="utf-8")
    m = re.search(r"create or replace view lib_work_queue.*?\n\s*order by[^;]*;", текст, re.S)
    assert m, "в миграции больше нет вида lib_work_queue"
    # security_invoker на временной схеме не нужен и мешает: вид создаётся владельцем.
    вид = m.group(0).replace("with (security_invoker = true)", "")
    if без_комментариев:
        вид = "\n".join(s.split("--")[0] for s in вид.splitlines())
    return вид


def очередь(cur) -> dict[str, int]:
    cur.execute('select part_number, usd from lib_work_queue order by usd desc')
    return {r[0]: int(r[1]) for r in cur.fetchall()}


def test_позиция_уходит_из_очереди_когда_цена_пришла(cur):
    """Главный случай. Прежняя форма оставляла её в очереди навсегда."""
    в_очереди = очередь(cur)
    assert "AAA-111" not in в_очереди, \
        "позиция с ценой осталась в очереди — проверка цены не срабатывает"


def test_позиция_вне_каталога_тоже_уходит(cur):
    """У неё part_id NULL с обеих сторон, и прежнее сравнение было тождественно ложным."""
    assert "BBB-222" not in очередь(cur), \
        "позиция вне каталога не уходит из очереди: сравнение идёт по part_id"


def test_без_цены_позиция_остаётся(cur):
    в_очереди = очередь(cur)
    assert "CCC-333" in в_очереди, "позиция без цены пропала из очереди"
    assert в_очереди["CCC-333"] == 7000


def test_чужой_поток_не_закрывает_позицию(cur):
    """«Прайс» — не ответ поставщика на наш запрос."""
    assert "CCC-333" in очередь(cur), \
        "позицию закрыл прайс, которого поставщик нам не давал"


def test_строка_без_числа_не_закрывает_позицию(cur):
    """Строка цены есть, а цены в ней нет: закрывать позицию нечем."""
    cur.execute("""insert into lib_exposure (id, part_number, name, usd_exposure)
                   values ('eee555', 'EEE-555', 'цена без числа', 5000)""")
    assert "EEE-555" in очередь(cur), "позицию закрыла строка с price is null"


def test_снимок_источника_держит_позицию_вне_очереди(cur):
    """have_price — факт из выгрузки, а не живая проверка; одно не заменяет другое."""
    assert "DDD-444" not in очередь(cur)


def test_разные_написания_номера_сходятся(cur):
    """Цена пришла как «AAA 111», позиция называется «AAA-111» — это один код."""
    cur.execute("select lib_pn_key('AAA 111') = lib_pn_key('AAA-111')")
    assert cur.fetchone()[0] is True


def test_проверка_цены_идёт_по_ключу_а_не_по_part_id(cur):
    """Свойство, а не текст: part_id у цен пустует, и опираться на него нельзя."""
    код = вид_из_схемы(без_комментариев=True)
    assert "lib_pn_key(pr.part_number) = e.id" in код
    assert "pr.part_id = e.part_id" not in код, "вернулось сравнение, которое не срабатывает"


def test_индекс_по_ключу_цены_есть_в_схеме():
    """Без него проверка читает таблицу цен целиком на каждую позицию очереди."""
    текст = (ROOT / "library" / "supabase" / "schema.sql").read_text(encoding="utf-8")
    assert "lib_prices_pn_key" in текст
    assert "on lib_prices (lib_pn_key(part_number))" in текст
