"""Прайс-бук КП на настоящей базе PostgreSQL.

  · схема применяется дважды, в свою схему одноразовой базы, без ролей платформы;
  · загрузчик пишет все строки, включая повторы номера позиции, с ключом
    прогона; повторная загрузка не задваивает; откат по ключу снимает свои строки;
  · причина несравнимости, посчитанная Python, совпадает с SQL-видом
    pb_kp_lines_checked на каждой строке — второй способ счёта;
  · ключ артикула Python совпадает с lib_pn_key.

Работает при поднятой базе (LIBRARY_SQL_TEST_DSN). Корпус придуман (правило 18).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "library"))
sys.path.insert(0, str(ROOT))

import load_pricebook as lp  # noqa: E402
from pricebook import part_key  # noqa: E402
from tests.test_pricebook import _корпус  # noqa: E402

DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМА = "pricebook_test"


def _применить(c, имя: str) -> None:
    from tests.test_library_schema_sql import операторы
    for оператор in операторы((ROOT / "library" / "supabase" / имя).read_text(encoding="utf-8")):
        c.execute(оператор)


@pytest.fixture
def conn():
    import psycopg2
    с = psycopg2.connect(DSN, options=f"-c search_path={СХЕМА}")
    с.autocommit = True
    c = с.cursor()
    c.execute(f"drop schema if exists {СХЕМА} cascade")
    c.execute(f"create schema {СХЕМА}")
    c.execute(f"set search_path to {СХЕМА}")
    _применить(c, "schema.sql")
    for _ in range(2):
        _применить(c, "pricebook_schema.sql")
    try:
        yield с
    finally:
        с.cursor().execute(f"drop schema if exists {СХЕМА} cascade")
        с.close()


def _одно(conn, sql, *p):
    with conn.cursor() as c:
        c.execute(sql, p)
        return c.fetchone()


def test_загрузка_python_и_sql_сходятся(conn, tmp_path):
    _корпус(tmp_path)
    шапки, строки = lp.прочитать(tmp_path)
    lp.разметить(шапки, строки)
    assert lp.записать(DSN, шапки, строки, "pb-t1", схема=СХЕМА) == (4, 9)
    # повтор той же версии не задваивает
    assert lp.записать(DSN, шапки, строки, "pb-t2", схема=СХЕМА) == (0, 0)
    assert _одно(conn, "select count(*) from pb_kp_lines") == (9,)
    assert _одно(conn, "select count(*) from pb_kp_lines_checked "
                       "where reason is distinct from reason_sql") == (0,)
    assert _одно(conn, "select count(*) from pb_kp_lines where reason is null") == (5,)
    with conn.cursor() as c:
        c.execute("delete from pb_kp_lines where run_id = 'pb-t1'")
        c.execute("delete from pb_kp_headers where run_id = 'pb-t1'")
    assert _одно(conn, "select count(*) from pb_kp_lines") == (0,)


def test_ключ_python_равен_lib_pn_key(conn):
    assert _одно(conn, "select lower('ШАЙБА')") == ("шайба",), "локаль базы не складывает кириллицу"
    for номер in ("ШАЙБА-12Ё", "3115 3768 00", "VYD+12(A)", "6m³", "4.5", ""):
        assert _одно(conn, "select lib_pn_key(%s)", номер) == (part_key(номер),), номер
