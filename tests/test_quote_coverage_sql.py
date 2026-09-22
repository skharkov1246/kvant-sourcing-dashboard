"""Разрез цен по машине считается на настоящем PostgreSQL, а не на глаз.

ЗАЧЕМ ОТДЕЛЬНЫЙ ТЕСТ. Это число уходит в отчёт владельцу: «столько-то цен
привязано к машине». Две ошибки в нём были тихими — обе нашлись только
прогоном против живой СУБД, чтением кода не находились:

1. `count(*)` по соединению вместо `count(distinct)`. `catalog_no` НЕ уникален
   (уникален `id`, а он считается по `catalog_norm`, если тот заполнен), и один
   артикул цены, попавший на две записи каталога, считался дважды. На корпусе
   ниже это давало 3 совпадения там, где их 2, — доля так может перевалить и за
   сто процентов.
2. Сверка точным написанием вместо ключа. `560-170-80` и `56017080` — одна
   деталь; точное сравнение находило 2 артикула из 5, по ключу находится 4.
   Занижение вдвое, и тоже молча: пустая связь ошибкой не выглядит.

Ключ обязан считаться той же функцией `lib_pn_key`, что связывает спрос с
каталогом. Вторая копия правила нормализации разошлась бы с первой — тест берёт
и функцию из миграции, и запрос из скрипта, а не их пересказ.

Корпус придуман (CLAUDE.md, правило 18), ответ посчитан руками.
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

СХЕМА = "тест_разреза_цен"


def запрос_из_скрипта() -> str:
    """Настоящий ПО_МАШИНЕ из скрипта замера, а не его копия в тесте."""
    spec = importlib.util.spec_from_file_location(
        "kvant_quote_price_coverage", ROOT / "scripts" / "quote_price_coverage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ПО_МАШИНЕ, mod.FEED


def функция_ключа() -> str:
    текст = (ROOT / "library" / "supabase" / "schema.sql").read_text(encoding="utf-8")
    m = re.search(r"create or replace function lib_pn_key.*?\$\$;", текст, re.S | re.I)
    assert m, "в schema.sql больше нет функции lib_pn_key"
    return m.group(0)


КОРПУС = """
create table lib_models (id text primary key, name text not null);
create table lib_parts (id text primary key, catalog_no text not null, name text not null);
create table lib_part_models (part_id text references lib_parts(id),
                              model_id text references lib_models(id),
                              primary key (part_id, model_id));
create table lib_prices (id bigserial primary key, part_number text, feed text);

insert into lib_models values ('m1','Машина Один'), ('m2','Машина Два');
insert into lib_parts values
  ('56017080','56017080','деталь один'),
  ('m74n50',  'M74N-50', 'деталь два'),
  ('abc123',  'ABC-123', 'деталь три'),
  -- Тот же сырой catalog_no при ином id: так выходит, когда ключ взят из
  -- catalog_norm, а catalog_no сохранён как есть. Ловушка для count(*).
  ('m74n50x', 'M74N-50', 'деталь два, иная запись');
insert into lib_part_models values ('56017080','m1'), ('m74n50','m1'), ('m74n50','m2');

insert into lib_prices (part_number, feed) values
  ('56017080',   'разбор КП'),   -- совпадает и точно, и по ключу; машина m1
  ('560-170-80', 'разбор КП'),   -- только по ключу; та же деталь, та же машина
  ('M74N-50',    'разбор КП'),   -- точно и по ключу; машины m1 и m2
  ('ABC 123',    'разбор КП'),   -- только по ключу; машины нет
  ('НЕТТАКОГО',  'разбор КП'),   -- не находится вовсе
  ('56017080',   'разбор КП'),   -- повтор: не должен удваивать артикул
  ('   ',        'разбор КП'),   -- пробелы: не артикул
  (null,         'разбор КП'),   -- пусто: не артикул
  ('M74N-50',    'иной корм');   -- чужой корм: в счёт не идёт
"""

# Посчитано руками по корпусу выше.
ОЖИДАЕМО = (
    5,   # различных артикулов: 56017080, 560-170-80, M74N-50, ABC 123, НЕТТАКОГО
    2,   # точным написанием: 56017080 и M74N-50 (несмотря на дубль записи)
    4,   # по ключу: те же плюс 560-170-80 и ABC 123
    3,   # ведут к машине: 56017080, 560-170-80, M74N-50 (у ABC 123 связи нет)
    2,   # машин затронуто: m1 и m2
)


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
            # Своя схема: тест не должен ни видеть, ни портить таблицы соседних
            # тестов, которые живут в той же одноразовой базе.
            cur.execute(f'drop schema if exists "{СХЕМА}" cascade')
            cur.execute(f'create schema "{СХЕМА}"')
            cur.execute(f'set search_path to "{СХЕМА}"')
            cur.execute(функция_ключа())
            cur.execute(КОРПУС)
            yield cur
        conn.rollback()
    finally:
        with conn.cursor() as cur:
            cur.execute(f'drop schema if exists "{СХЕМА}" cascade')
        conn.commit()
        conn.close()


def test_разрез_по_машине_считает_то_что_посчитано_руками(курсор):
    запрос, feed = запрос_из_скрипта()
    курсор.execute(запрос, (feed,))
    assert курсор.fetchone() == ОЖИДАЕМО


def test_дубль_каталожного_номера_не_удваивает_совпадение(курсор):
    """Защита от count(*): 'M74N-50' лежит в каталоге дважды под разными id."""
    запрос, feed = запрос_из_скрипта()
    курсор.execute("select count(*) from lib_parts where catalog_no = 'M74N-50'")
    assert курсор.fetchone()[0] == 2, "корпус потерял дубль — ловушка перестала ловить"
    курсор.execute(запрос, (feed,))
    артикулов, точным, в_каталоге, с_машиной, _ = курсор.fetchone()
    # Дубль даёт ТРИ строки соединения на ДВА артикула. Проверка «не больше
    # общего числа» это пропускает (3 <= 5), поэтому сверяемся с самим числом.
    assert точным == 2, "дубль catalog_no посчитан дважды — count(*) вместо count(distinct)"
    # M74N-50 ведёт к двум машинам: то же удвоение, но по другому соединению.
    assert с_машиной == 3, "деталь с двумя машинами посчитана дважды"
    assert точным <= артикулов and в_каталоге <= артикулов


def test_нормализация_номера_находит_больше_точного_сравнения(курсор):
    """Если эти числа сравнялись, ключ перестал работать — а разрез опустеет тихо."""
    запрос, feed = запрос_из_скрипта()
    курсор.execute(запрос, (feed,))
    _, точным, в_каталоге, _, _ = курсор.fetchone()
    assert в_каталоге > точным, "ключ не даёт ничего сверх точного сравнения"
