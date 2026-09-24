"""Правдоподобный код в SQL и в Python судит одинаково — иначе правило двоится.

Разбор (docfilter.код_правдоподобен) решает, какой номер ляжет в part_number
новой строки; чтение (lib_pn_plausible в library/supabase/schema.sql) — какой
из накопленных номеров считается кодом на страницах номенклатуры и брендов.
Разойдись они — «SS316», отвергнутый разбором, остался бы кодом на странице, и
наоборот. Функции берутся настоящие: из файла миграции и из модуля. Работает
только при поднятой одноразовой базе CI (LIBRARY_SQL_TEST_DSN). Корпус придуман.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from library import docfilter
from tests.test_code_plausible import КОДЫ, НЕ_КОДЫ

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

# Сверх двух списков — пограничные написания: регистр, «ё», латиница вместо
# кириллицы, ключ длиннее 80 знаков, пробелы и знаки вокруг.
ПОГРАНИЧНЫЕ = [
    "ss316", "Ss-316-L", "AISI  304 L", "12х18н10т", "12Х18Н10Т ", "(Ду 50)", "Ø50 мм",
    "шайба 12Ё", "0" * 90, "SS316" + "7" * 90, "  ", "мм", "ГОСТ", "ГОСТ Р 52857",
    "WCB-1", "A105N", "F316L", "S355J2", "ST52-3", "40Х", "65Г", "Ст.3", "10 кВт",
]


def функции() -> str:
    текст = (ROOT / "library" / "supabase" / "schema.sql").read_text(encoding="utf-8")
    части = []
    for имя in ("lib_pn_key", "lib_pn_plausible"):
        m = re.search(rf"create or replace function {имя}\(.*?\$\$;", текст, re.S | re.I)
        assert m, f"в schema.sql больше нет функции {имя}"
        части.append(m.group(0))
    return "\n".join(части)


def test_правдоподобный_код_в_sql_и_в_python_одинаков():
    import psycopg2
    from psycopg2.extensions import parse_dsn

    config = parse_dsn(DSN)
    assert config["host"] in ("127.0.0.1", "localhost")
    assert config["dbname"] == "library_sql_test"
    корпус = [t for t, _ in НЕ_КОДЫ] + КОДЫ + ПОГРАНИЧНЫЕ
    схема = "тест_правдоподобного_кода"
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("select lower(%s)", ("ШАЙБА",))
            if cur.fetchone()[0] != "шайба":
                pytest.fail("база не складывает регистр кириллицы: локаль C вместо "
                            "UTF-8 (CLAUDE.md, правило 21а)")
            cur.execute(f'drop schema if exists "{схема}" cascade')
            cur.execute(f'create schema "{схема}"')
            cur.execute(f'set search_path to "{схема}"')
            cur.execute(функции())
            for t in корпус:
                cur.execute("select lib_pn_plausible(%s), lib_pn_plausible(lib_pn_key(%s))",
                            (t, t))
                по_написанию, по_ключу = cur.fetchone()
                ждём = docfilter.код_правдоподобен(t)
                assert по_написанию == ждём, f"{t!r}: SQL {по_написанию}, Python {ждём}"
                # Функцию зовут и от ключа, и от написания.
                assert по_ключу == ждём, t
                # Условие, вписанное в запросы crossref и codes_sql, — то же тело.
                cur.execute("select " + docfilter.sql_код_годен("%s"), (t,))
                assert cur.fetchone()[0] == ждём, t
            # NULL — не марка: пустое кодом не обвиняется, как и в Python.
            cur.execute("select lib_pn_plausible(null)")
            assert cur.fetchone()[0] is True
    finally:
        with conn.cursor() as cur:
            cur.execute(f'drop schema if exists "{схема}" cascade')
        conn.close()
