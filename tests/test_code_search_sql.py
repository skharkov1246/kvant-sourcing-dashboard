"""Живой поиск по спросу (lib_code_search) держит предел строк на обоих путях.

ЗАЧЕМ. До 25.09.2026 у функции стояло «set statement_timeout = '8s'», и
комментарий называл это пределом вызова. Предел не действовал: таймер
оператора взводится в начале клиентского оператора, и смена настройки внутри
функции его не перевзводит (замер 24.09.2026 на PostgreSQL 16). Путь по коду
при этом читал все строки ключа, сколько бы их ни было. Теперь работу держит
число строк: каждый путь читает не больше lim_rows живых строк, и ответ
говорит об усечении (capped).

Проверяется поведение, а не написание:
1. путь по коду упирается в предел, и ответ помечен усечённым; под пределом
   счёт точный, а помеченные строки (lib_row_junk) в него не идут;
2. путь по словам — то же;
3. предел держит ЧТЕНИЕ таблицы, а не только счёт в ответе: вызов читает из
   lib_demand меньше строк, чем их у ключа (счётчики pg_stat_xact_user_tables
   внутри одной транзакции);
4. наблюдение, на котором стоит снятие «set statement_timeout»: у функции оно
   вызов не ограничивает, у сеанса — ограничивает. Поменяется это в новой
   версии PostgreSQL — тест скажет, что комментарий в schema_junk.sql устарел.

Корпус придуман (CLAUDE.md, правило 18). Работает при поднятой базе
(LIBRARY_SQL_TEST_DSN), как соседние тесты SQL.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tests.test_library_schema_sql import операторы

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

ИМЯ = "code_search_sql_test"
ФАЙЛЫ = ("schema.sql", "schema_junk.sql")

# Ключ qx900: 900 живых строк в 300 сделках и 100 строк с действующей
# пометкой, в своих сделках. Живые вставлены первыми: на такой малой таблице
# путь по словам идёт последовательным проходом, и он тоже должен упираться в
# предел, а не дочитывать пометки. Написание одно: второе («qx 900») даёт тот
# же ключ, но другие слова, и проход по словам читал бы вдвое больше строк.
# Слово «заглушка»: 150 живых строк, у каждой свой код.
КОРПУС = """
insert into lib_demand (deal_id, item_name, part_number)
  select 'D' || (g % 300), 'Шпилька выдуманная', 'QX-900' from generate_series(1, 900) g;
insert into lib_demand (deal_id, item_name, part_number)
  select 'DJ' || g, 'Шпилька выдуманная', 'QX-900' from generate_series(1, 100) g;
insert into lib_row_junk (demand_id, rule, run_id)
  select id, 'proza-тест', 'тест' from lib_demand where deal_id like 'DJ%';
insert into lib_demand (deal_id, item_name, part_number)
  select 'W' || g, 'Заглушка выдуманная', 'ZG-' || g from generate_series(1, 150) g;
analyze lib_demand;
analyze lib_row_junk;
"""


@pytest.fixture(scope="module")
def cur():
    import psycopg2
    from psycopg2.extensions import parse_dsn

    assert parse_dsn(DSN)["dbname"] == "library_sql_test"
    conn = psycopg2.connect(DSN)
    conn.autocommit = True      # в миграции CREATE INDEX CONCURRENTLY, как у psql
    c = conn.cursor()
    try:
        c.execute(f"drop schema if exists {ИМЯ} cascade")
        c.execute(f"create schema {ИМЯ}")
        c.execute(f"set search_path to {ИМЯ}")
        for имя in ФАЙЛЫ:
            for оператор in операторы((ROOT / "library" / "supabase" / имя).read_text(encoding="utf-8")):
                c.execute(оператор)
        c.execute(КОРПУС)
        yield c
    finally:
        c.execute(f"drop schema if exists {ИМЯ} cascade")
        conn.close()


def поиск(cur, q: str, lim_rows: int | None = None) -> dict:
    if lim_rows is None:
        cur.execute("select lib_code_search(%s)", (q,))
    else:
        cur.execute("select lib_code_search(%s, 20, %s)", (q, lim_rows))
    return cur.fetchone()[0]


def test_путь_по_коду_упирается_в_предел_и_говорит_об_этом(cur):
    v = поиск(cur, "QX-900", 100)
    assert [(r["code"], r["rows"]) for r in v["by_code"]] == [("qx900", 100)]
    assert v["by_code"][0]["deals"] <= 100
    assert v["capped"] is True
    # Какой путь упёрся, видно по счёту: у дошедшего он равен пределу.
    assert v["by_code"][0]["rows"] == max(v["by_code"][0]["rows"], v["word_rows"])


def test_под_пределом_счёт_по_коду_точный_и_без_пометок(cur):
    v = поиск(cur, "QX-900")
    # 900 живых строк в 300 сделках; 100 помеченных не идут ни в строки, ни в сделки.
    assert [(r["code"], r["rows"], r["deals"]) for r in v["by_code"]] == [("qx900", 900, 300)]
    assert v["capped"] is False


def test_путь_по_словам_упирается_в_предел(cur):
    v = поиск(cur, "заглушка выдуманная", 100)
    assert v["word_rows"] == 100 and v["capped"] is True
    assert v["by_code"] == []
    assert sum(r["rows"] for r in v["by_words"]) <= 100
    под_пределом = поиск(cur, "заглушка выдуманная")
    assert под_пределом["word_rows"] == 150 and под_пределом["capped"] is False


def test_предел_держит_чтение_таблицы_а_не_только_счёт(cur):
    """Счётчики pg_stat_xact_user_tables копятся в процессе до сброса, а сброс
    бывает только вне транзакции, — поэтому до и после вызова в одной
    транзакции: разница и есть чтение вызова."""
    чтение = ("select coalesce(seq_tup_read, 0) + coalesce(idx_tup_fetch, 0) "
              "from pg_stat_xact_user_tables where relid = %s::regclass")
    cur.execute("begin")
    try:
        cur.execute(чтение, (f"{ИМЯ}.lib_demand",))
        до = cur.fetchone()[0]
        v = поиск(cur, "QX-900", 100)
        cur.execute(чтение, (f"{ИМЯ}.lib_demand",))
        прочитано = cur.fetchone()[0] - до
    finally:
        cur.execute("rollback")
    assert v["capped"] is True
    # У ключа 1 000 строк. Без предела путь по коду один читает их все (замер
    # на прежней функции: 1 100 вместе с путём по словам); с пределом каждый
    # путь — сотню живых строк, вместе 200 (замер 25.09.2026), с запасом на
    # выбор плана.
    assert прочитано <= 3 * 100, прочитано


@pytest.mark.parametrize("тело", [
    "language sql set statement_timeout = '20ms' as $$ select 1 from pg_sleep(0.2) $$",
    "language plpgsql set statement_timeout = '20ms' "
    "as $$ begin perform pg_sleep(0.2); return 1; end $$",
], ids=["sql", "plpgsql"])
def test_предел_времени_у_функции_вызов_не_ограничивает(cur, тело):
    cur.execute(f"create or replace function проба_предела() returns int {тело}")
    t = time.monotonic()
    cur.execute("select проба_предела()")
    assert cur.fetchone()[0] == 1 and time.monotonic() - t >= 0.2
    # Тот же предел у сеанса отменяет оператор: проверка отличает одно от другого.
    import psycopg2

    cur.execute("begin")
    try:
        cur.execute("set local statement_timeout = '20ms'")
        with pytest.raises(psycopg2.errors.QueryCanceled):
            cur.execute("select pg_sleep(0.2)")
    finally:
        cur.execute("rollback")
