"""Замер роли предложения (scripts/offer_role_measure.py): свод и печать.

Две половины. Свод и печать — без базы, на строках, какие отдаёт запрос части.
Запрос части — на одноразовой PostgreSQL (LIBRARY_SQL_TEST_DSN) с настоящими
схемами: имена колонок и ключи соединения проверяются только живым запросом.
Бренды и компании придуманы (правило 18), реестр — корпус tests/test_offer_role.py.
"""
from __future__ import annotations

import importlib.util
import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from library import offer_role as orl  # noqa: E402


def _модуль(имя, путь):
    spec = importlib.util.spec_from_file_location(имя, путь)
    м = importlib.util.module_from_spec(spec)
    sys.modules[имя] = м
    spec.loader.exec_module(м)
    return м


orm = _модуль("offer_role_measure_t", ROOT / "scripts" / "offer_role_measure.py")
tor = _модуль("test_offer_role_t", ROOT / "tests" / "test_offer_role.py")
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")


@pytest.fixture()
def реестр(monkeypatch):
    р = orl.собрать(tor.СЛОВАРЬ, tor.РЯДЫ, tor.РАЗВЕДКА)
    monkeypatch.setattr(orl, "реестр_файлов", lambda доп=(): р)
    return р


# (сущность, имя, файл, карточка, каталог, домены, инн, строк)
СТРОКИ = [
    ("KV-1", "Zentrix GmbH", "Zentrix", None, None, None, None, 3),
    ("KV-2", "ООО Ромашка Трейд", "Zentrix", "7", None, None, None, 2),
    ("KV-3", "ООО Альфа", None, None, "Zentrix", ["www.zentrix.ru"], None, 1),
    (None, None, "Zentrix", None, None, None, None, 4),
    ("KV-4", "ООО Бета", None, None, None, None, None, 5),
]


def test_свод_и_печать_без_имён(реестр):
    итог = orm.Итог()
    orm.судить(итог, СТРОКИ, {"7": "zentrix"}, реестр)
    assert итог.строк == 15 and итог.без_сущности == 4
    assert итог.роли["итог"] == {orl.ПРЯМОЕ: 4, orl.ТРЕЙДЕР: 2, orl.НЕ_ОПРЕДЕЛЕНО: 9}
    assert итог.роли["файл"][orl.ПРЯМОЕ] == 3
    assert итог.роли["каталог"][orl.ПРЯМОЕ] == 1
    assert итог.роли["карточка"][orl.ТРЕЙДЕР] == 2
    assert {k: len(v) for k, v in итог.компании["итог"].items()} == {
        orl.ПРЯМОЕ: 2, orl.ТРЕЙДЕР: 1, orl.НЕ_ОПРЕДЕЛЕНО: 1}
    assert итог.бренды_прямых == {"zentrix": 4}
    # Прежнее правило: «Zentrix GmbH» против «Zentrix» — у него тоже прямое.
    assert итог.прежнее[(True, orl.ПРЯМОЕ)] == 3
    вывод = io.StringIO()
    with redirect_stdout(вывод):
        orm.печать(итог, "проба", 1)
    текст = вывод.getvalue()
    for имя in ("Ромашка", "Альфа", "Бета", "Zentrix GmbH", "zentrix.ru", "KV-"):
        assert имя not in текст, имя
    assert "стало хуже (было прямое, стало нет): 0" in текст


def test_бренд_вне_реестра_не_печатается(реестр):
    итог = orm.Итог()
    orm.судить(итог, [("KV-9", "ООО Ромашкамаш", "Ромашкамаш", None, None, None, None, 1)], {}, реестр)
    assert итог.бренды_прямых == {"(вне реестра)": 1}


def test_карточка_в_ключи():
    assert orm.карточка_в_ключи("7, 8,9", {"7": "a", "9": "b"}) == ["a", "b"]
    длинная = ",".join(["7"] * 100) + ",12"         # 200+ знаков, хвост «,12» — обрубок
    assert len(длинная) >= 200
    assert orm.карточка_в_ключи(длинная, {"7": "a", "12": "c"}) == ["a"] * 100
    assert orm.карточка_в_ключи(None, {"7": "a"}) == []


# ── Запрос части на живой PostgreSQL ────────────────────────────────────────

СХЕМА = "тест_роли_предложения"
ФАЙЛЫ = ("schema.sql", "schema_junk.sql", "suppliers_schema.sql", "brands_schema.sql")
КОРПУС = """
insert into sup_entity (id, kind, display_name, status) values
  ('KV-S-000001-8', 'legal', 'Zentrix GmbH', 'active'),
  ('KV-S-000002-6', 'legal', 'ООО Ромашка Трейд', 'active'),
  ('KV-S-000003-4', 'legal', 'ООО Альфа', 'active');
insert into sup_identifier (sup_id, kind, value, value_norm, source, status, run_id) values
  ('KV-S-000001-8', 'bitrix', 'ID 101', '101', 't', 'stated', 'r'),
  ('KV-S-000002-6', 'bitrix', 'ID 102', '102', 't', 'stated', 'r'),
  ('KV-S-000003-4', 'bitrix', 'ID 103', '103', 't', 'stated', 'r'),
  ('KV-S-000003-4', 'domain', 'Zentrix.RU', 'ZENTRIXRU', 't', 'stated', 'r');
insert into lib_prices (item_name, part_number, price, currency, rfq_company, source, feed,
                        confidence, oem, rfq_brands) values
  ('Подшипник', 'ZX-100', 10, 'EUR', '101', 'КП', 'разбор КП', 'med', 'Zentrix', null),
  ('Подшипник', 'ZX-100', 11, 'EUR', '102', 'КП', 'разбор КП', 'med', null, '7'),
  ('Подшипник', 'ZX-100', 12, 'EUR', '103', 'КП', 'разбор КП', 'med', null, null),
  ('Втулка',    'QQ-1',    9, 'EUR', '103', 'КП', 'разбор КП', 'med', null, null),
  ('Чужая',     'ZX-100',  1, 'EUR', '101', 'прайс', 'каталог ODM', 'med', 'Zentrix', null);
insert into lib_parts (id, catalog_no, name, oem) values ('zx-100', 'ZX-100', 'Подшипник', 'Zentrix');
"""


@pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")
def test_запрос_части_на_живой_базе(реестр):
    import psycopg2
    from psycopg2.extensions import parse_dsn

    assert parse_dsn(DSN)["dbname"] == "library_sql_test"
    helpers = _модуль("library_schema_sql_helpers_role", ROOT / "tests" / "test_library_schema_sql.py")
    созданные: list[str] = []
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for роль in ("anon", "authenticated"):
                cur.execute("select 1 from pg_roles where rolname = %s", (роль,))
                if not cur.fetchone():
                    cur.execute(f"create role {роль} nologin")
                    созданные.append(роль)
            cur.execute(f'drop schema if exists "{СХЕМА}" cascade')
            cur.execute(f'create schema "{СХЕМА}"')
            cur.execute(f'set search_path to "{СХЕМА}"')
            for имя in ФАЙЛЫ:
                for оператор in helpers.операторы((ROOT / "library" / "supabase" / имя).read_text(encoding="utf-8")):
                    cur.execute(оператор)
            cur.execute(КОРПУС)
            cur.execute("""insert into lib_brands (brand_key, name, rule, run_id)
                           values ('zentrix', 'Zentrix', 'r', 'r')""")
            cur.execute("""insert into lib_brand_alias (spelling, spelling_key, source, seen_at, sp176_id,
                                                        brand_key, status, rule, run_id)
                           values ('Zentrix', 'zentrix', 'СП-176', 'СП-176#7', 7, 'zentrix',
                                   'разрешено', 'r', 'r')""")
            with redirect_stdout(io.StringIO()):
                итог, откуда = orm.замер(cur, 3)
            assert "lib_brand_map" in откуда
            assert итог.строк == 4
            assert итог.роли["файл"][orl.ПРЯМОЕ] == 1
            assert итог.роли["карточка"][orl.ТРЕЙДЕР] == 1
            # Каталог по ключу номера: ZX-100 — у всех трёх компаний.
            assert итог.роли["каталог"] == {orl.ПРЯМОЕ: 2, orl.ТРЕЙДЕР: 1, orl.НЕ_ОПРЕДЕЛЕНО: 1}
            # Итог: Zentrix сам, «Альфа» по домену (ZX-100), «Ромашка Трейд» — трейдер,
            # втулка без бренда — не определено.
            assert итог.роли["итог"] == {orl.ПРЯМОЕ: 2, orl.ТРЕЙДЕР: 1, orl.НЕ_ОПРЕДЕЛЕНО: 1}
            assert итог.причины["итог"][(orl.ПРЯМОЕ, "домен: тот же бренд")] == 1
            # Тот же запрос доменов и ИНН читает публикатор снимка номенклатуры.
            ид = orl.идентификаторы(cur)
            assert ид["KV-S-000003-4"] == (["zentrix.ru"], [])
            assert "KV-S-000001-8" not in ид
            доп, откуда_ = orl.карта_базы(cur)
            assert откуда_ == откуда and доп
    finally:
        with conn.cursor() as cur:
            cur.execute(f'drop schema if exists "{СХЕМА}" cascade')
            for роль in созданные:
                cur.execute(f"drop role if exists {роль}")
        conn.close()
