"""Очередь на ИНН берётся за ПОСЛЕДНИЙ прогон сведения, а не за всю таблицу.

ЗАЧЕМ. sup_review копит строки: сущность, отложенную и в прошлый прогон, и в
этот, «closed_at is null» вернёт дважды. Холостой прогон публикации 21.09.2026
показал 767 сущностей там, где сведение отложило 394 — ровно вдвое. Число
компаний ушло бы на страницу владельцу завышенным.

Коварство — в том, что по карточкам ошибка НЕ видна: дубли ссылаются на те же
карточки, и счётчик карточек (980) сходился с прогоном сведения. То есть один
из двух счётчиков был верен, и расхождение выглядело как разные величины, а не
как ошибка. Поймал его холостой прогон, для того он и холостой.

Проверка идёт против настоящего PostgreSQL и берёт НАСТОЯЩИЙ запрос из скрипта,
а не его пересказ. Корпус придуман (правило 18), живёт в отдельной схеме.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМА = "тест_очереди_инн"


def запрос_из_скрипта() -> str:
    spec = importlib.util.spec_from_file_location(
        "kvant_publish_suppliers", ROOT / "scripts" / "publish_suppliers.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ОЧЕРЕДЬ_ИНН_SQL


# «merge-1» отложил А и Б. «merge-2» отложил ТЕХ ЖЕ двоих заново плюс В и Г:
# так таблица и копится. Е из последнего прогона закрыта человеком, Д — чужого
# вида. Верный ответ: четыре строки, все из merge-2.
КОРПУС = """
create table sup_review (
  id bigserial primary key, kind text not null, closed_at timestamptz,
  payload jsonb not null, run_id text not null);
insert into sup_review (kind, payload, run_id) values
 ('entity_uncertain', '{"names":["А"],"keys":["101"]}', 'merge-1'),
 ('entity_uncertain', '{"names":["Б"],"keys":["102"]}', 'merge-1'),
 ('entity_uncertain', '{"names":["А"],"keys":["101"]}', 'merge-2'),
 ('entity_uncertain', '{"names":["Б"],"keys":["102"]}', 'merge-2'),
 ('entity_uncertain', '{"names":["В"],"keys":["103"]}', 'merge-2'),
 ('entity_uncertain', '{"names":["Г"],"keys":["104"]}', 'merge-2'),
 ('ambiguous_match',  '{"names":["Д"],"keys":["105"]}', 'merge-2'),
 ('entity_uncertain', '{"names":["Е"],"keys":["106"]}', 'merge-2');
update sup_review set closed_at = now() where payload->>'names' = '["Е"]';
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
            cur.execute(f'drop schema if exists "{СХЕМА}" cascade')
            cur.execute(f'create schema "{СХЕМА}"')
            cur.execute(f'set search_path to "{СХЕМА}"')
            cur.execute(КОРПУС)
            yield cur
        conn.rollback()
    finally:
        with conn.cursor() as cur:
            cur.execute(f'drop schema if exists "{СХЕМА}" cascade')
        conn.commit()
        conn.close()


def имена(курсор):
    курсор.execute(запрос_из_скрипта())
    return sorted(r[0]["names"][0] for r in курсор.fetchall())


def test_дубль_из_прошлого_прогона_не_удваивает_счёт(курсор):
    # Без отбора по прогону вышло бы шесть строк: А и Б дважды.
    assert имена(курсор) == ["А", "Б", "В", "Г"]


def test_закрытая_строка_и_чужой_вид_не_попадают(курсор):
    выдача = имена(курсор)
    assert "Е" not in выдача, "закрытая строка снова показана как работа"
    assert "Д" not in выдача, "в очередь ИНН попал чужой вид строки"


def test_пустая_таблица_не_роняет_запрос(курсор):
    """Подзапрос за последним прогоном на пустой таблице даёт NULL — запрос
    обязан вернуть ноль строк, а не упасть и не вернуть всё."""
    курсор.execute("delete from sup_review")
    курсор.execute(запрос_из_скрипта())
    assert курсор.fetchall() == []
