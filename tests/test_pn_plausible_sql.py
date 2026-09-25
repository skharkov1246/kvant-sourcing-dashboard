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
    # Стандарт с размером: пробел, табуляция, перевод строки, неразрывный пробел;
    # слитно и через узкий пробел — голый стандарт (узкого пробела в списке нет).
    "DIN 471\t25", "DIN 471\n25", "DIN 471\u00a025", "DIN 471\u200925", "din 471 - 25",
    "ГОСТ 9833-73  020-025-30", "ГОСТ 9833-73/020-025-30", " DIN 933 ", "ISO 9001 2015",
    "G B / T 5 7 8 3 - 2 0 0 0", "DIN 7 6", "DIN 4 7 1 25", "DIN 471 2 5", "ГОСТ 9833-73\r\n020",
]


def параметры(sql: str, t: str) -> tuple:
    """Написание подставляется во все места %s условия."""
    return (t,) * sql.count("%s")


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
            # Каталог защищает: номер из lib_parts кодом считается всегда. В
            # каталоге — выдуманный размерный номер, который закрытый список
            # иначе отверг бы (замер 24.09.2026: 6 таких номеров в живом каталоге).
            cur.execute("create table lib_parts (id text primary key, catalog_no text not null)")
            cur.execute("insert into lib_parts values ('1250x300', '1250X300'),"
                        " ('выдум-7', 'ВЫДУМ-7')")
            годен = "select " + docfilter.sql_код_годен("%s")
            cur.execute(годен, параметры(годен, "1250x300"))
            assert cur.fetchone()[0] is True, "номер каталога отвергнут правилом"
            assert not docfilter.код_правдоподобен("1250x300"), \
                "корпус защиты должен быть обвиняемым без каталога"
            cur.execute(годен, параметры(годен, "640x480"))
            assert cur.fetchone()[0] is False, "размер вне каталога принят за код"
            # Условие с готовым ключом — как в codes_sql (x.pn, x.code).
            с_ключом = "select " + docfilter.sql_код_годен("%s", "lib_pn_key(%s)")
            for t in корпус:
                cur.execute("select lib_pn_plausible(%s), lib_pn_plausible(lib_pn_key(%s))",
                            (t, t))
                по_написанию, по_ключу = cur.fetchone()
                ждём = docfilter.код_правдоподобен(t)
                assert по_написанию == ждём, f"{t!r}: SQL {по_написанию}, Python {ждём}"
                # Функцию зовут и от ключа: в ключе пробелов нет, стандарт с
                # размером по нему — стандарт, в SQL так же, как в Python.
                assert по_ключу == docfilter.код_правдоподобен(docfilter.ключ_кода(t)), t
                # Условие, вписанное в запросы crossref и codes_sql, — то же тело.
                cur.execute(годен, параметры(годен, t))
                assert cur.fetchone()[0] == ждём, t
                cur.execute(с_ключом, параметры(с_ключом, t))
                assert cur.fetchone()[0] == ждём, t
            # NULL — не марка: пустое кодом не обвиняется, как и в Python.
            cur.execute("select lib_pn_plausible(null)")
            assert cur.fetchone()[0] is True
    finally:
        with conn.cursor() as cur:
            cur.execute(f'drop schema if exists "{схема}" cascade')
        conn.close()
