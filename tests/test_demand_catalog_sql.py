"""Витрина «спрос × каталог» считает только живые строки спроса.

ЗАЧЕМ. lib_demand_catalog до 23.09.2026 читал lib_demand целиком и брал строки с
действующей пометкой в lib_row_junk: текст документа, принятый за позицию
(mark_prose), и прежнюю редакцию файла, заменённую переразбором («переразбор v2»,
library/reparse.py). Счётчики «спрос опознан» и «сделок опознано» были завышены, а
строки переразобранного файла считались дважды.

Проверяется на НАСТОЯЩЕЙ схеме — оба файла миграции применяются в свою схему
одноразовой базы, как это делает прогон миграций:
1. строка с действующей пометкой в витрину не попадает, со снятой — попадает;
2. миграция применяется ПОВТОРНО поверх уже стоящих видов — ровно состояние живой
   базы. Витрина, построенная поверх lib_demand_live, здесь бы и упала: тот вид
   пересоздаётся «drop view» без cascade, а на чистой базе зависимости ещё нет;
3. роли Supabase прав на витрину не имеют (снятие — через проверку наличия роли,
   правило 20);
4. фильтр — анти-соединение, а не подзапрос на каждую строку (правило 8).

Корпус придуман (правило 18). Работает при поднятой базе (LIBRARY_SQL_TEST_DSN).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.test_library_schema_sql import операторы

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

ИМЯ = "demand_catalog_test"
ФАЙЛЫ = ("schema.sql", "schema_junk.sql")


def применить(cur, *файлы: str) -> None:
    for имя in файлы:
        for оператор in операторы((ROOT / "library" / "supabase" / имя)
                                  .read_text(encoding="utf-8")):
            cur.execute(оператор)


@pytest.fixture
def cur():
    import psycopg2
    from psycopg2.extensions import parse_dsn

    assert parse_dsn(DSN)["dbname"] == "library_sql_test"
    conn = psycopg2.connect(DSN)
    conn.autocommit = True      # в миграции CREATE INDEX CONCURRENTLY, как у psql
    созданные: list[str] = []
    c = conn.cursor()
    try:
        # Ролей Supabase в чистом PostgreSQL нет; заглушки — только на время теста.
        for роль in ("anon", "authenticated"):
            c.execute("select 1 from pg_roles where rolname = %s", (роль,))
            if not c.fetchone():
                c.execute(f"create role {роль} nologin")
                созданные.append(роль)
        c.execute(f"drop schema if exists {ИМЯ} cascade")
        c.execute(f"create schema {ИМЯ}")
        c.execute(f"set search_path to {ИМЯ}")
        # Как в Supabase: новые таблицы и виды схемы сразу получают права ролей
        # платформы. Без этого проверка прав прошла бы и без снятия — в чистом
        # PostgreSQL прав по умолчанию нет.
        c.execute(f"alter default privileges in schema {ИМЯ} "
                  "grant all on tables to anon, authenticated")
        применить(c, *ФАЙЛЫ)
        yield c
    finally:
        c.execute(f"drop schema if exists {ИМЯ} cascade")
        for роль in созданные:
            c.execute(f"drop role if exists {роль}")
        conn.close()


def наполнить(cur) -> dict[str, int]:
    """Пять строк спроса об одной детали каталога и одна — о чужой."""
    cur.execute("insert into lib_parts (id, catalog_no, name) "
                "values (lib_pn_key('ВЫД-6205'), 'ВЫД-6205', 'Подшипник выдуманный')")
    строки = {
        "живая": "1", "проза": "2", "снятая": "3", "заменённая": "4", "новая": "4",
        "чужая": "5",
    }
    ид = {}
    for имя, сделка in строки.items():
        артикул = "ЧУЖ-1" if имя == "чужая" else "выд 6205"
        cur.execute("insert into lib_demand (deal_id, item_name, part_number) "
                    "values (%s, %s, %s) returning id",
                    (сделка, f"Подшипник выдуманный — {имя}", артикул))
        ид[имя] = cur.fetchone()[0]
    cur.execute("""insert into lib_row_junk (demand_id, rule, run_id, revoked_at) values
        (%s, 'proza-v1', 'proza-тест', null),
        (%s, 'proza-v1', 'proza-тест', now()),
        (%s, 'переразбор v2', 'reparse-тест', null)""",
                (ид["проза"], ид["снятая"], ид["заменённая"]))
    return ид


def витрина(cur) -> set[int]:
    cur.execute("select id from lib_demand_catalog")
    return {r[0] for r in cur.fetchall()}


def test_витрина_берёт_только_живые_строки(cur):
    ид = наполнить(cur)
    assert витрина(cur) == {ид["живая"], ид["снятая"], ид["новая"]}
    # Сделки считаются по живым строкам: сделка 2 держалась только на прозе.
    cur.execute("select count(distinct deal_id) from lib_demand_catalog")
    assert cur.fetchone()[0] == 3
    # Та же выборка, что у lib_demand_live, — одно правило в двух местах.
    cur.execute("select id from lib_demand_live where part_number = 'выд 6205'")
    assert {r[0] for r in cur.fetchall()} == витрина(cur)


def test_миграция_применяется_поверх_стоящих_видов(cur):
    """Состояние живой базы: виды уже есть, и в них есть строки."""
    ид = наполнить(cur)
    применить(cur, "schema_junk.sql")
    assert витрина(cur) == {ид["живая"], ид["снятая"], ид["новая"]}


def test_роли_supabase_прав_на_витрину_не_имеют(cur):
    for роль in ("anon", "authenticated"):
        cur.execute("select has_table_privilege(%s, %s, 'select')",
                    (роль, f"{ИМЯ}.lib_demand_catalog"))
        assert cur.fetchone()[0] is False, роль


def test_фильтр_пометок_анти_соединение_а_не_подзапрос_на_строку(cur):
    наполнить(cur)
    cur.execute("analyze lib_demand; analyze lib_row_junk; analyze lib_parts")
    cur.execute("explain select count(*) from lib_demand_catalog")
    план = "\n".join(r[0] for r in cur.fetchall())
    assert "Anti Join" in план and "SubPlan" not in план, план


def test_отчёт_библиотеки_считает_спрос_по_живым_строкам(cur):
    """scripts/library_report.py: «спрос» и «спрос опознан» — без помеченных строк."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "library_report", ROOT / "scripts" / "library_report.py")
    отчёт = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(отчёт)
    наполнить(cur)
    d = отчёт.собрать(cur)
    assert d["спрос_живой"] is True
    assert (d["спрос"], d["спрос_опознан"], d["сделок_опознано"]) == (4, 3, 3)


def test_калибровка_цены_берёт_только_живые_строки(cur):
    """scripts/quote_price_calibration.py: помеченные строки раздували отказ ворот
    (проза) и считали строку дважды (прежняя редакция файла)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "quote_price_calibration", ROOT / "scripts" / "quote_price_calibration.py")
    калибровка = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(калибровка)
    ид = наполнить(cur)
    cur.execute("update lib_demand set source_file = 'Ф-1'")
    cur.execute("insert into lib_files (file_id, status, origin, parse_path) "
                "values ('Ф-1', 'разобран', %s, 'таблица')", (калибровка.ПРЕДЛОЖЕНИЕ,))
    cur.execute(калибровка.СТРОКИ.format(спрос="lib_demand_live"),
                (калибровка.ПРЕДЛОЖЕНИЕ, 100))
    имена = {r[1] for r in cur.fetchall()}
    cur.execute("select item_name from lib_demand where id = any(%s)",
                ([ид["живая"], ид["снятая"], ид["новая"], ид["чужая"]],))
    assert имена == {r[0] for r in cur.fetchall()}
