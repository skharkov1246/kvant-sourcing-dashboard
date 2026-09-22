"""Ключ артикула обязан совпадать в Python и в SQL — иначе связь молча пустеет.

Загрузчики складывают деталь в lib_parts под ключом, посчитанным на Python
(part_key), а представление lib_demand_catalog соединяет спрос с каталогом по
ключу, посчитанному в SQL (lib_pn_key). Это одно правило в двух местах: разойдись
они на одном символе — соединение даст ноль строк, и ни одна проверка этого не
заметит, потому что пустой результат ошибкой не выглядит.

Тест берёт настоящую функцию из файла миграции и сравнивает её с настоящим
part_key на придуманных номерах. Работает только при поднятой одноразовой базе
CI (LIBRARY_SQL_TEST_DSN), как и соседний тест SQL-публикации.

СНАЧАЛА ПРОВЕРЯЕТСЯ САМА БАЗА. `lower()` складывает регистр средствами локали, и
в базе с локалью C кириллица не складывается вовсе: `lower('ШАЙБА')` возвращает
'ШАЙБА', заглавные буквы вылетают фильтром посторонних знаков, и ключ выходит
'12' вместо 'шайба12е'. Расхождение при этом настоящее — но виновата база, а не
код, и без отдельной проверки тест краснеет строкой «assert '12' ==
'шайба12е'», по которой этого не видно. 22.09.2026 я на ней и застрял, а заодно
получил на локальной базе неверный счёт кодов: пять вместо четырёх, потому что
'ВЫДУМ-101' и 'выдум 101' там дают разные ключи.
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

НОМЕРА = [
    "MW21215M", "64/60030070/1", "56-017/080", "56 017 080", "ШАЙБА-12Ё",
    "6ES7153-2BA10-0XB0", "14T47", "", "   ", "KV30 0001", "7490 0290 74",
    "ГОСТ 8752-79", "abc",
]


def part_key():
    spec = importlib.util.spec_from_file_location(
        "kvant_load_crossrefs", ROOT / "library" / "load_crossrefs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.part_key


def функция_из_миграции() -> str:
    текст = (ROOT / "library" / "supabase" / "schema.sql").read_text(encoding="utf-8")
    m = re.search(r"create or replace function lib_pn_key.*?\$\$;", текст, re.S | re.I)
    assert m, "в schema.sql больше нет функции lib_pn_key"
    return m.group(0)


def test_ключ_артикула_в_sql_и_в_python_считается_одинаково():
    import psycopg2
    from psycopg2.extensions import parse_dsn

    config = parse_dsn(DSN)
    assert config["host"] in ("127.0.0.1", "localhost")
    assert config["dbname"] == "library_sql_test"
    py = part_key()
    with psycopg2.connect(DSN) as conn:
        with conn.cursor() as cur:
            # Сначала база, потом код: на локали C сравнивать ключи бессмысленно.
            cur.execute("select lower(%s)", ("ШАЙБА",))
            сложилось = cur.fetchone()[0]
            if сложилось != "шайба":
                pytest.fail(
                    f"база не складывает регистр кириллицы: lower('ШАЙБА') = "
                    f"'{сложилось}'. Это локаль базы (C вместо UTF-8), а не код: "
                    "ключи артикулов на ней считаются иначе, чем на живой, и "
                    "сравнивать их нельзя. Подними базу с --locale=C.UTF-8 — "
                    "в гейте она такая.")
            cur.execute(функция_из_миграции())
            for номер in НОМЕРА:
                cur.execute("select lib_pn_key(%s)", (номер,))
                assert cur.fetchone()[0] == py(номер), номер
        conn.rollback()
