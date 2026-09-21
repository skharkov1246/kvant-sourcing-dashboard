"""Схема библиотеки обязана применяться на ЧИСТОЙ базе, а не только на нашей.

История. 12.09.2026 схема месяц не применялась с нуля, и это скрыло ошибку:
lib_part_suppliers ссылалась на lib_suppliers внешним ключом, а создавалась
раньше неё. На существующей базе оператор проходил (таблица уже была), на чистой
файл падал целиком — и вместе с ним всё, что стояло дальше. Нашлось случайно,
при разворачивании локальной копии.

Тест применяет оба файла миграции в отдельной схеме одноразовой базы CI и
проверяет, что ни один оператор не упал. Работает только при поднятой базе
(LIBRARY_SQL_TEST_DSN), как и соседний тест SQL-публикации.

Отдельная схема, а не public: в той же базе живёт тест публикации, и он
проверяет, что таблиц библиотеки в public НЕТ. За собой тест убирает всё.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

ИМЯ = "schema_smoke"
ФАЙЛЫ = ("schema.sql", "schema_junk.sql")


def операторы(sql: str):
    """Файл миграции → отдельные операторы, как их посылает psql.

    Целым файлом их выполнить нельзя: несколько операторов в одном вызове
    драйвер оборачивает в транзакцию, а CREATE INDEX CONCURRENTLY в транзакции
    запрещён. Резать по «;» наивно тоже нельзя — в файле есть блоки do $$ … $$
    и строки с точкой с запятой внутри, поэтому состояние отслеживается:
    строковая кавычка, долларовая кавычка с тегом и строчный комментарий."""
    out, буфер, i = [], [], 0
    в_строке = False
    тег = None
    while i < len(sql):
        c = sql[i]
        if тег:
            буфер.append(c)
            if sql.startswith(тег, i):
                буфер.extend(тег[1:])
                i += len(тег)
                тег = None
                continue
            i += 1
            continue
        if в_строке:
            буфер.append(c)
            if c == "'":
                в_строке = False
            i += 1
            continue
        if c == "'":
            в_строке = True
            буфер.append(c)
            i += 1
            continue
        if sql.startswith("--", i):
            конец = sql.find("\n", i)
            i = len(sql) if конец < 0 else конец
            continue
        if c == "$":
            конец = sql.find("$", i + 1)
            if конец > i:
                тег = sql[i:конец + 1]
                буфер.append(тег)
                i = конец + 1
                continue
        if c == ";":
            текст = "".join(буфер).strip()
            if текст:
                out.append(текст)
            буфер = []
            i += 1
            continue
        буфер.append(c)
        i += 1
    хвост = "".join(буфер).strip()
    if хвост:
        out.append(хвост)
    return out


def test_резалка_операторов_не_ломает_долларовые_блоки():
    sql = ("create table t (a text);\n"
           "do $$ begin raise notice 'a;b'; end $$;\n"
           "-- комментарий; с точкой с запятой\n"
           "insert into t values ('x;y');")
    куски = операторы(sql)
    assert len(куски) == 3, куски
    assert куски[1].startswith("do $$") and куски[1].endswith("$$")
    assert "'x;y'" in куски[2]


def test_схема_применяется_на_чистой_базе():
    import psycopg2
    from psycopg2.extensions import parse_dsn

    config = parse_dsn(DSN)
    assert config["host"] in ("127.0.0.1", "localhost")
    assert config["dbname"] == "library_sql_test"
    assert config["user"] == "postgres"

    созданные_роли: list[str] = []
    conn = psycopg2.connect(DSN)
    # CREATE INDEX CONCURRENTLY нельзя выполнять в транзакции, а в миграции он
    # есть — поэтому autocommit, как и у psql, которым миграция применяется.
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            # Роли Supabase в чистом PostgreSQL отсутствуют, а миграция у них
            # отзывает права. Создаём заглушки и убираем в конце.
            for роль in ("anon", "authenticated"):
                cur.execute("select 1 from pg_roles where rolname = %s", (роль,))
                if not cur.fetchone():
                    cur.execute(f"create role {роль} nologin")
                    созданные_роли.append(роль)
            cur.execute(f"drop schema if exists {ИМЯ} cascade")
            cur.execute(f"create schema {ИМЯ}")
            cur.execute(f"set search_path to {ИМЯ}")
            for имя in ФАЙЛЫ:
                sql = (ROOT / "library" / "supabase" / имя).read_text(encoding="utf-8")
                for оператор in операторы(sql):
                    cur.execute(оператор)   # падение здесь и есть провал теста
            cur.execute("""
                select count(*) from information_schema.tables
                 where table_schema = %s and table_name like 'lib\\_%%'""", (ИМЯ,))
            таблиц = cur.fetchone()[0]
            assert таблиц >= 15, f"таблиц библиотеки создано всего {таблиц}"
            # Внешние ключи обязаны указывать на существующие таблицы этой же
            # схемы: иначе порядок операторов снова разъедется незаметно.
            cur.execute("""
                select count(*) from information_schema.table_constraints
                 where table_schema = %s and constraint_type = 'FOREIGN KEY'""", (ИМЯ,))
            assert cur.fetchone()[0] >= 10
    finally:
        with conn.cursor() as cur:
            cur.execute(f"drop schema if exists {ИМЯ} cascade")
            # Роли-заглушки убираем только те, что создали сами: в чужой базе
            # они могут быть настоящими.
            for роль in созданные_роли:
                cur.execute(f"drop role if exists {роль}")
        conn.close()


ИМЯ_ЦЕНЫ = "quote_price_smoke"


def test_вид_связывает_цену_кп_с_компанией_реестра():
    """sup_quote_price соединяет два реестра, не подменяя один другим.

    Цена из разобранного КП несёт ключ портала (lib_prices.rfq_company), а
    реестр опознаёт компанию по sup_identifier. Записать одно в supplier_id
    нельзя — там внешний ключ на старый справочник lib_suppliers. Вид считает
    связь на чтении, и проверять его надо на живой базе: имена колонок
    («number» вместо «id») и ключ соединения («value» вместо «value_norm»)
    молча дали бы пустую связь — обе ошибки поймались именно так.
    """
    import psycopg2
    from psycopg2.extensions import parse_dsn

    assert parse_dsn(DSN)["dbname"] == "library_sql_test"
    файлы = ("schema.sql", "schema_junk.sql", "suppliers_schema.sql")

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
            cur.execute(f"drop schema if exists {ИМЯ_ЦЕНЫ} cascade")
            cur.execute(f"create schema {ИМЯ_ЦЕНЫ}")
            cur.execute(f"set search_path to {ИМЯ_ЦЕНЫ}")
            for имя in файлы:
                for оператор in операторы(
                        (ROOT / "library" / "supabase" / имя).read_text(encoding="utf-8")):
                    cur.execute(оператор)

            cur.execute("""
                insert into sup_entity (id, kind, display_name, status)
                values ('KV-S-000123-7', 'legal', 'Придуманный поставщик', 'active')""")
            cur.execute("""
                insert into sup_identifier (sup_id, kind, value, value_norm, source,
                                            status, run_id)
                values ('KV-S-000123-7', 'bitrix', 'ID 4242', '4242', 'bitrix',
                        'stated', 'проба')""")
            # value и value_norm НАМЕРЕННО разные: норма — буквы и цифры в
            # верхнем регистре (library/load_supplier_master.py, норма()).
            # Совпади они в пробе, тест не отличил бы соединение по value от
            # соединения по value_norm — а разница между ними и есть та ошибка,
            # ради которой тест написан.
            cur.execute("""
                insert into lib_prices (item_name, price, currency, qty, rfq_id,
                                        rfq_company, lead_days, source, feed, confidence)
                values ('Подшипник',  1200.50, 'EUR', 10, '11', '4242', 42,
                        'КП', 'разбор КП', 'med'),
                       ('Уплотнение',  850.00, 'EUR',  4, '12', null,  null,
                        'КП', 'разбор КП', 'low'),
                       ('Чужая цена',      99, 'RUB',  1, null, null,  null,
                        'прайс', 'каталог ODM', 'med')""")

            cur.execute("select item_name, supplier_number, supplier_name, lead_days "
                        "from sup_quote_price order by item_name")
            строки = cur.fetchall()

            # Только котировки: цена из каталога сюда не попадает.
            assert [с[0] for с in строки] == ["Подшипник", "Уплотнение"]
            # Связь нашлась.
            assert строки[0][1] == "KV-S-000123-7"
            assert строки[0][2] == "Придуманный поставщик"
            assert строки[0][3] == 42
            # Цена без поставщика НЕ теряется: скрыть её значило бы потерять
            # цифру, которая есть.
            assert строки[1][1] is None and строки[1][2] is None
    finally:
        with conn.cursor() as cur:
            cur.execute(f"drop schema if exists {ИМЯ_ЦЕНЫ} cascade")
            for роль in созданные:
                cur.execute(f"drop role if exists {роль}")
        conn.close()
