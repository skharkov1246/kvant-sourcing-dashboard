"""Имя для показа на живом PostgreSQL: порядок выбора, откат, читатели без вида.

Вид sup_name_shown — единственное место, где выбирается имя компании для
/suppliers, /nomenclature и выгрузок брендов. Здесь проверено:

  • порядок: карточка Битрикса → реквизиты → написание → display_name, если не
    похож на ключ → домен → ключ как есть;
  • сжатый ключ («supremevalves») не показывается, когда есть что-то лучше;
  • «похоже на ключ» в SQL и в Python отвечает одинаково на одном корпусе;
  • откат прогона по run_id возвращает прежнее имя (пометка, а не удаление);
  • запросы страниц работают и там, где вида ещё нет.

Схема — настоящий suppliers_schema.sql в отдельной схеме одноразовой базы;
корпус придуман (правило 18). Работает при поднятой базе (LIBRARY_SQL_TEST_DSN).
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from library import company_names as cn

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМА = "тест_имён_поставщиков"
ПУСТАЯ = "тест_имён_без_вида"

_spec = importlib.util.spec_from_file_location(
    "library_schema_sql_helpers_names", ROOT / "tests" / "test_library_schema_sql.py")
_helpers = importlib.util.module_from_spec(_spec)
sys.modules["library_schema_sql_helpers_names"] = _helpers
_spec.loader.exec_module(_helpers)

_spec2 = importlib.util.spec_from_file_location(
    "publish_suppliers_names_sql", ROOT / "scripts" / "publish_suppliers.py")
ps = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(ps)

КОРПУС = """
insert into sup_entity (id, kind, display_name) values
  ('KV-S-000001-8', 'legal', 'supremevalves'),
  ('KV-S-000002-6', 'legal', 'romashka'),
  ('KV-S-000003-4', 'legal', 'acmepumps'),
  ('KV-S-000004-2', 'legal', 'Бета Сервис'),
  ('KV-S-000005-9', 'legal', 'epsilon'),
  ('KV-S-000006-7', 'legal', 'bitrix:2002'),
  ('KV-S-000007-5', 'legal', 'gamma');
insert into sup_number_registry (sup_id, seq, run_id)
  select id, substr(id, 6, 6)::int, 'merge-t' from sup_entity;
insert into sup_identifier (sup_id, kind, value, value_norm, source, status, run_id) values
  ('KV-S-000001-8', 'domain', 'supreme.test',  'SUPREMETEST', 's', 'stated', 'merge-t'),
  ('KV-S-000003-4', 'alias',  'acmepumps',     'ACMEPUMPS',   's', 'stated', 'merge-t'),
  ('KV-S-000003-4', 'alias',  'Acme Pumps GmbH', 'ACMEPUMPSGMBH', 's', 'stated', 'merge-t'),
  ('KV-S-000003-4', 'legal',  'gmbh & co kg',  'GMBHCOKG',    's', 'stated', 'merge-t'),
  ('KV-S-000005-9', 'domain', 'epsilon.test',  'EPSILONTEST', 's', 'stated', 'merge-t'),
  ('KV-S-000005-9', 'legal',  'ооо ао',        'ОООАО',       's', 'stated', 'merge-t');
"""


@pytest.fixture()
def курсор():
    import psycopg2
    from psycopg2.extensions import parse_dsn

    config = parse_dsn(DSN)
    assert config["host"] in ("127.0.0.1", "localhost")
    assert config["dbname"] == "library_sql_test"
    conn = psycopg2.connect(DSN)
    try:
        with conn.cursor() as cur:
            for сх in (СХЕМА, ПУСТАЯ):
                cur.execute(f'drop schema if exists "{сх}" cascade')
                cur.execute(f'create schema "{сх}"')
            cur.execute(f'set search_path to "{СХЕМА}"')
            for оператор in _helpers.операторы(
                    (ROOT / "library/supabase/suppliers_schema.sql").read_text(encoding="utf-8")):
                cur.execute(оператор)
            cur.execute(КОРПУС)
            yield cur
        conn.rollback()
    finally:
        with conn.cursor() as cur:
            for сх in (СХЕМА, ПУСТАЯ):
                cur.execute(f'drop schema if exists "{сх}" cascade')
        conn.commit()
        conn.close()


def имена(cur) -> dict[str, tuple[str, str]]:
    cur.execute("select sup_id, name, name_source from sup_name_shown")
    return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def записать(cur, run_id, строки):
    cn.записать(cur, [{"previous_name": None, "card_id": None, "full_name": None,
                       "inn": None, "kpp": None, "ogrn": None, **s} for s in строки], run_id)


def test_порядок_выбора_имени(курсор):
    записать(курсор, "names-1", [
        {"sup_id": "KV-S-000001-8", "source": "bitrix:title", "name": "Supreme Valves Ltd"},
        {"sup_id": "KV-S-000001-8", "source": "bitrix:requisite", "name": "ООО «Суприм»"},
        {"sup_id": "KV-S-000002-6", "source": "bitrix:requisite", "name": "ООО «Ромашка»",
         "inn": "0000000002"},
        # Сохранённое раньше название-ключ вид обязан пропустить.
        {"sup_id": "KV-S-000007-5", "source": "bitrix:title", "name": "gamma"},
        {"sup_id": "KV-S-000007-5", "source": "bitrix:requisite", "name": "АО «Гамма»"},
    ])
    н = имена(курсор)
    assert н["KV-S-000001-8"] == ("Supreme Valves Ltd", "bitrix:title")
    assert н["KV-S-000002-6"] == ("ООО «Ромашка»", "bitrix:requisite")
    # Правовая форма (kind legal) — не имя, берётся написание-алиас.
    assert н["KV-S-000003-4"] == ("Acme Pumps GmbH", "написание")
    assert н["KV-S-000004-2"] == ("Бета Сервис", "реестр")
    assert н["KV-S-000005-9"] == ("epsilon.test", "домен")
    # Лучше ключа нет ничего — имя пустое: подпись выбирает читатель.
    assert н["KV-S-000006-7"] == (None, "ключ реестра")
    assert н["KV-S-000007-5"] == ("АО «Гамма»", "bitrix:requisite")
    # Ни одного сжатого ключа там, где было из чего выбрать.
    for sid, (имя, откуда) in н.items():
        assert имя is None or not cn.как_ключ(имя), sid


def test_похоже_на_ключ_одинаково_в_sql_и_python(курсор):
    корпус = ["supremevalves", "bitrix:2002", "ромашка", "abc123", "", "  ", "Supreme Valves",
              "ООО «Ромашка»", "acme pumps", "Acme-Pumps", "3M", "ёлка", "Ёлка", "lib:7 x"]
    for s in корпус:
        курсор.execute("select sup_имя_как_ключ(%s)", (s,))
        assert курсор.fetchone()[0] == cn.как_ключ(s), repr(s)


def test_откат_прогона_возвращает_прежнее_имя(курсор):
    записать(курсор, "names-1", [
        {"sup_id": "KV-S-000001-8", "source": "bitrix:title", "name": "Первое имя"}])
    # Второй прогон читает действующее и пишет только изменение — с прежним.
    сущности, текущие, _ = cn.читать_базу(курсор)
    assert сущности == {}                         # признаков bitrix в корпусе нет
    строки, _ = cn.собрать({"KV-S-000001-8": [11]}, {11: "Второе имя"}, {}, текущие)
    assert строки[0]["previous_name"] == "Первое имя"
    cn.записать(курсор, строки, "names-2")
    assert имена(курсор)["KV-S-000001-8"][0] == "Второе имя"

    assert cn.откатить(курсор, "names-2") == 1
    assert имена(курсор)["KV-S-000001-8"] == ("Первое имя", "bitrix:title")
    # Строка не удалена, а помечена: откат обратим и виден.
    курсор.execute("select count(*) from sup_display_name where rolled_back_at is not null")
    assert курсор.fetchone()[0] == 1
    assert cn.откатить(курсор, "names-1") == 1
    assert имена(курсор)["KV-S-000001-8"] == ("supreme.test", "домен")


def test_повтор_без_изменений_не_пишет(курсор):
    записать(курсор, "names-1", [
        {"sup_id": "KV-S-000001-8", "source": "bitrix:title", "name": "Supreme Valves Ltd",
         "card_id": "11"}])
    _, текущие, _ = cn.читать_базу(курсор)
    строки, сч = cn.собрать({"KV-S-000001-8": [11]}, {11: "Supreme Valves Ltd"}, {}, текущие)
    assert строки == [] and сч["bitrix:title: без изменений"] == 1


def test_страница_читает_имя_из_вида_и_живёт_без_него(курсор):
    записать(курсор, "names-1", [
        {"sup_id": "KV-S-000001-8", "source": "bitrix:title", "name": "Supreme Valves Ltd"}])
    assert cn.вид_имён_есть(курсор)
    курсор.execute(cn.имена_sql(ps.СУЩНОСТИ_SQL, True))
    строки = курсор.fetchall()
    по = {r[0]: r for r in строки}
    assert по["KV-S-000001-8"][1] == "Supreme Valves Ltd"
    assert по["KV-S-000001-8"][7] == "bitrix:title"
    курсор.execute(ps.ИНН_РЕКВИЗИТОВ_SQL)
    assert курсор.fetchall() == []

    # Рабочая база до применения схемы: таблиц реестра нет только вида.
    курсор.execute(f'set search_path to "{ПУСТАЯ}"')
    курсор.execute("create table sup_entity (id text, display_name text, country text, note text, "
                   "status text, resolution text)")
    курсор.execute("create table sup_number_registry (sup_id text, seq int)")
    курсор.execute("insert into sup_entity values ('KV-S-000001-8', 'supremevalves', null, null, "
                   "'active', 'candidate')")
    assert not cn.вид_имён_есть(курсор)
    курсор.execute(cn.имена_sql(ps.СУЩНОСТИ_SQL, False))
    assert курсор.fetchall()[0][1] == "supremevalves"
