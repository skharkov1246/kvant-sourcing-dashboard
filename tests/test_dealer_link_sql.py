"""Замер «дилеры разведки → реестр компаний» против настоящей схемы PostgreSQL.

Схема — те же файлы library/supabase/*.sql, что у сведения поставщиков
(tests/test_supplier_link_sql.py); реестр — выдуманный (правило 18). Держится:
  · замер идёт на соединении только для чтения и ничего не пишет;
  · сводит домен записи дилера и ИНН из её текста, в корень цепочки слияний;
    имя — только кандидат;
  · Python против SQL: реестр, прочитанный из базы, сводит так же, как тот
    же реестр, собранный в памяти;
  · в журнале — только агрегаты: ни имени, ни домена, ни ИНН, ни номера KV.
База — одноразовая, в локали C.UTF-8 (LIBRARY_SQL_TEST_DSN, правило 21а).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from library import dealer_link as dl  # noqa: E402
from library import supplier_link as sl  # noqa: E402
from tests.test_library_schema_sql import операторы  # noqa: E402

DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМА = "dealer_link_sql_test"
ФАЙЛЫ = ("schema.sql", "schema_junk.sql", "suppliers_schema.sql", "portal_schema.sql",
         "portal_entity_schema.sql")
А, Б, В, Г = "KV-S-000041-1", "KV-S-000042-2", "KV-S-000043-3", "KV-S-000044-4"

КОРПУС = f"""
insert into sup_entity (id, kind, display_name, resolution, country) values
 ('{А}', 'legal', 'Альфа Дилер Выдуманный', 'resolved', 'Нигдения'),
 ('{Б}', 'legal', 'Бета Выдуманная', 'resolved', 'Нигдения'),
 ('{Г}', 'legal', 'Гамма Выдуманная', 'candidate', null);
insert into sup_entity (id, kind, display_name, resolution, merged_into) values
 ('{В}', 'legal', 'oldvydum', 'merged', '{Б}');
insert into sup_identifier (sup_id, kind, value, value_norm, source, status, run_id) values
 ('{А}', 'domain', 'alfa-vydum.example', 'ALFAVYDUMEXAMPLE', 'сведение', 'verified', 'm1'),
 ('{В}', 'domain', 'old-vydum.example', 'OLDVYDUMEXAMPLE', 'сведение', 'stated', 'm1'),
 ('{Г}', 'inn', '0000000018', '0000000018', 'реквизиты', 'verified', 'm1'),
 ('{Г}', 'alias', 'гаммавыдуманная', 'ГАММАВЫДУМАННАЯ', 'сведение', 'rejected', 'm1');
insert into sup_display_name (sup_id, source, name, run_id) values
 ('{Б}', 'bitrix:title', 'Бета Выдуманная Групп', 'n1');
"""

РАЗВЕДКА = [{"oem_key": "vydumka", "dealers": [
    {"company": "Альфа (дилер)", "country": "Нигдения", "role": "официальный дилер",
     "domain": "alfa-vydum.example"},
    {"company": "Старая вывеска", "country": "Нигдения", "role": "независимый продавец",
     "domain": "old-vydum.example"},
    {"company": "Гамма (реквизиты: ИНН 0000000018)", "role": "дочерняя компания изготовителя"},
    {"company": "Бета Выдуманная Групп", "country": "Нигдения", "role": "дистрибьютор"},
    {"company": "Гамма Выдуманная", "role": "дилер"},
]}]
ТАЙНЫ = ("vydum", "Выдуман", "Альфа", "Гамма", "0000000018", "KV-S-")


@pytest.fixture(scope="module")
def база():
    import psycopg2
    from psycopg2.extensions import parse_dsn

    assert parse_dsn(DSN)["dbname"] == "library_sql_test"
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    созданные: list[str] = []
    c = conn.cursor()
    try:
        for роль in ("anon", "authenticated", "service_role"):
            c.execute("select 1 from pg_roles where rolname = %s", (роль,))
            if not c.fetchone():
                c.execute(f"create role {роль} nologin")
                созданные.append(роль)
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        c.execute(f"create schema {СХЕМА}")
        c.execute(f"set search_path to {СХЕМА}")
        for файл in ФАЙЛЫ:
            for оператор in операторы((ROOT / "library" / "supabase" / файл).read_text(encoding="utf-8")):
                c.execute(оператор)
        c.execute(КОРПУС)
        yield conn
    finally:
        c.execute("reset search_path")
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        for роль in созданные:
            c.execute(f"drop role if exists {роль}")
        conn.close()


def _соединение():
    import psycopg2
    conn = psycopg2.connect(DSN, options=f"-c search_path={СХЕМА}")
    conn.set_session(readonly=True)
    return conn


def test_замер_только_для_чтения_и_только_агрегаты(база, capsys):
    conn = _соединение()
    try:
        assert dl.прогон(conn, РАЗВЕДКА) == 0
    finally:
        conn.close()
    out = capsys.readouterr().out
    assert "ВСЕГО записей дилеров сведено:         3 из 5" in out
    assert "Прогон ВХОЛОСТУЮ" in out
    for тайна in ТАЙНЫ:
        assert тайна not in out, тайна


def test_python_против_sql(база):
    """Реестр из базы сводит так же, как тот же реестр в памяти, и верно."""
    conn = _соединение()
    try:
        with conn.cursor() as cur:
            р, сущностей = sl.читать_реестр(cur)
    finally:
        conn.close()
    assert сущностей == 4
    список = dl.дилеры(РАЗВЕДКА)
    итог = dl.сопоставить(список, р)
    assert итог.связь == {0: А, 1: Б, 2: Г}             # домен, корень слияния, ИНН
    # Имя — только кандидат: Бета по написанию карточки, Гамма — alias отклонён,
    # но имя сущности то же; у обеих связи нет.
    assert итог.кандидаты == {3: {Б: "имя+страна"}, 4: {Г: "имя"}}
    в_памяти = sl.собрать_реестр(
        [(А, None, "Нигдения", "Альфа Дилер Выдуманный"), (Б, None, "Нигдения", "Бета Выдуманная"),
         (В, Б, None, "oldvydum"), (Г, None, None, "Гамма Выдуманная")],
        [(А, "domain", "alfa-vydum.example"), (В, "domain", "old-vydum.example"), (Г, "inn", "0000000018")],
        [(Б, "Бета Выдуманная Групп", None, None)])
    в_памяти_итог = dl.сопоставить(список, в_памяти)
    assert в_памяти_итог.связь == итог.связь and в_памяти_итог.кандидаты == итог.кандидаты


def test_без_реестра_код_2(база, capsys):
    import psycopg2
    conn = psycopg2.connect(DSN, options="-c search_path=pg_catalog")
    try:
        assert dl.прогон(conn, РАЗВЕДКА) == 2
    finally:
        conn.close()
