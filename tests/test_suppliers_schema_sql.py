"""Схема реестра поставщиков применяется на чистой базе и защищает себя сама.

История. Проверка внутри схемы сначала была написана неверно, и это выяснилось
прогоном, а не чтением. Первая версия смотрела, у всех ли ПОМЕЧЕННЫХ таблиц стоит
FORCE RLS, — и оказалась слепой: блок доступа выше переприменяет защиту, поэтому
к моменту проверки он уже починил всё, что мог. Снятый вручную FORCE и выданное
anon право прогон восстановил и отчитался «чисто»: проверка подтверждала работу
предыдущего блока, а не состояние схемы.

Настоящая дыра другая — таблица, добавленная в файл, но забытая в массиве блока
доступа: метки она не получит, защиты тоже, а счёт помеченных сойдётся. Поэтому
проверка перевёрнута и ищет таблицы, которые ПО ИМЕНИ принадлежат схеме, но метки
не несут. Здесь закреплено, что она действительно на это падает.

Схема применяется в отдельную схему одноразовой базы, а не в public: иначе
таблицы sup_* остались бы в общей базе CI и мешали соседним тестам. Ради этого
файл не прибит к public — берёт схему из search_path.

Работает только при поднятой базе (LIBRARY_SQL_TEST_DSN), как и соседние тесты SQL.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

ИМЯ = "suppliers_smoke"
ФАЙЛ = ROOT / "library" / "supabase" / "suppliers_schema.sql"

# Резалка операторов уже написана и выверена в соседнем тесте: у неё разобраны
# долларовые блоки, строки с точкой с запятой внутри и строчные комментарии.
_spec = importlib.util.spec_from_file_location(
    "library_schema_sql_helpers", ROOT / "tests" / "test_library_schema_sql.py")
_helpers = importlib.util.module_from_spec(_spec)
sys.modules["library_schema_sql_helpers"] = _helpers
_spec.loader.exec_module(_helpers)
операторы = _helpers.операторы

ТАБЛИЦЫ = ("sup_entity", "sup_identifier", "sup_fact", "sup_override",
           "sup_review", "sup_number_registry", "our_entity", "sup_display_name")


def применить(cur, схема: str = ИМЯ) -> None:
    cur.execute(f"set search_path to {схема}")
    for оператор in операторы(ФАЙЛ.read_text(encoding="utf-8")):
        cur.execute(оператор)       # падение здесь и есть провал теста


@pytest.fixture()
def база():
    import psycopg2
    from psycopg2.extensions import parse_dsn

    config = parse_dsn(DSN)
    assert config["host"] in ("127.0.0.1", "localhost")
    assert config["dbname"] == "library_sql_test"

    созданные: list[str] = []
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for роль in ("anon", "authenticated", "service_role"):
                cur.execute("select 1 from pg_roles where rolname = %s", (роль,))
                if not cur.fetchone():
                    cur.execute(f"create role {роль} nologin")
                    созданные.append(роль)
            cur.execute(f"drop schema if exists {ИМЯ} cascade")
            cur.execute(f"create schema {ИМЯ}")
        yield conn
    finally:
        with conn.cursor() as cur:
            cur.execute(f"drop schema if exists {ИМЯ} cascade")
            # Роли-заглушки убираем только свои, и терпимо: на service_role может
            # висеть грант из чужой схемы той же одноразовой базы, и тогда DROP
            # ROLE упадёт — уборка не должна ронять сам тест.
            for роль in созданные:
                try:
                    cur.execute(f"drop role if exists {роль}")
                except Exception:      # noqa: BLE001 — уборка, причина не важна
                    pass
        conn.close()


def test_применяется_на_чистой_базе(база):
    with база.cursor() as cur:
        применить(cur)
        cur.execute("""select table_name from information_schema.tables
                        where table_schema = %s and table_type = 'BASE TABLE'""", (ИМЯ,))
        есть = {r[0] for r in cur.fetchall()}
    пропущены = set(ТАБЛИЦЫ) - есть
    assert not пропущены, f"не созданы таблицы: {sorted(пропущены)}"


def test_повторный_прогон_идемпотентен(база):
    with база.cursor() as cur:
        применить(cur)
        применить(cur)       # второй раз: ни один оператор не должен упасть


def test_каждая_таблица_под_force_rls(база):
    with база.cursor() as cur:
        применить(cur)
        cur.execute("""
            select c.relname, c.relrowsecurity, c.relforcerowsecurity
              from pg_class c join pg_namespace ns on ns.oid = c.relnamespace
             where ns.nspname = %s and c.relkind = 'r'""", (ИМЯ,))
        строки = cur.fetchall()
    assert строки, "таблиц не создано"
    слабые = [имя for имя, rls, force in строки if not rls or not force]
    assert not слабые, f"без FORCE RLS: {слабые}"


def test_anon_не_получает_ничего(база):
    with база.cursor() as cur:
        применить(cur)
        cur.execute("""
            select table_name, grantee, privilege_type
              from information_schema.role_table_grants
             where table_schema = %s and grantee in ('anon', 'authenticated', 'PUBLIC')""",
                    (ИМЯ,))
        права = cur.fetchall()
    assert not права, f"у anon/authenticated остались права: {права}"


def test_забытая_в_списке_таблица_роняет_схему(база):
    """Главный сторож: таблица схемы без метки обязана уронить прогон.

    Именно этот случай первая версия проверки пропускала.
    """
    import psycopg2

    with база.cursor() as cur:
        применить(cur)
        # таблица, какую добавили бы в файл и забыли в массиве блока доступа
        cur.execute(f"create table {ИМЯ}.sup_forgotten (id bigserial primary key)")
        with pytest.raises(psycopg2.errors.RaiseException) as поймано:
            применить(cur)
    текст = str(поймано.value)
    assert "sup_forgotten" in текст, f"проверка не назвала таблицу: {текст}"
    assert "блок" in текст.lower() or "метк" in текст.lower(), текст


def test_вечный_номер_проверяется_формой(база):
    """id обязан иметь вид KV-S-NNNNNN-C: регламент pnw/НУМЕРАЦИЯ.md."""
    import psycopg2

    with база.cursor() as cur:
        применить(cur)
        cur.execute(f"insert into {ИМЯ}.sup_entity (id, kind, display_name) "
                    "values ('KV-S-000123-7', 'legal', 'Придуманный поставщик')")
        for плохой in ("KV-000123-7", "KV-X-000123-7", "KV-S-12345-7", "ABC"):
            with pytest.raises(psycopg2.errors.CheckViolation):
                cur.execute(f"insert into {ИМЯ}.sup_entity (id, kind, display_name) "
                            "values (%s, 'legal', 'Придуманный')", (плохой,))
            cur.execute("rollback")


def test_один_companyId_не_может_принадлежать_двум_сущностям(база):
    """Раздвоение системного идентификатора портала — ошибка сведения."""
    import psycopg2

    with база.cursor() as cur:
        применить(cur)
        for n, номер in (("KV-S-000001-8", "9001"), ("KV-S-000002-6", "9001")):
            cur.execute(f"insert into {ИМЯ}.sup_entity (id, kind, display_name) "
                        "values (%s, 'legal', 'Придуманный')", (n,))
        cur.execute(f"insert into {ИМЯ}.sup_identifier "
                    "(sup_id, kind, value, value_norm, source, run_id) "
                    "values ('KV-S-000001-8','bitrix','9001','9001','тест','r1')")
        with pytest.raises(psycopg2.errors.UniqueViolation):
            cur.execute(f"insert into {ИМЯ}.sup_identifier "
                        "(sup_id, kind, value, value_norm, source, run_id) "
                        "values ('KV-S-000002-6','bitrix','9001','9001','тест','r1')")


def test_эффективное_значение_кладёт_правку_поверх_импорта(база):
    with база.cursor() as cur:
        применить(cur)
        cur.execute(f"""insert into {ИМЯ}.sup_fact
            (subject_kind, subject_id, field, value, status, source_type,
             method, method_ver, run_id)
            values ('entity','KV-S-000001-8','country','\"DE\"','stated','document',
                    'правило-тест','1','r1')""")
        cur.execute(f"select value, status from {ИМЯ}.sup_effective where field='country'")
        assert cur.fetchone() == ("DE", "stated"), "импортированное значение не видно"

        cur.execute(f"""insert into {ИМЯ}.sup_override
            (subject_kind, subject_id, field, source_value, corrected_value,
             author, reason, run_id)
            values ('entity','KV-S-000001-8','country','\"DE\"','\"AT\"',
                    'придуманный сотрудник','проверено по выписке','r2')""")
        cur.execute(f"""select value, status, imported_value, corrected_by
                          from {ИМЯ}.sup_effective where field='country'""")
        значение, статус, импорт, автор = cur.fetchone()
    assert значение == "AT", "правка человека не легла поверх импорта"
    assert статус == "verified", "статус после правки должен стать verified"
    assert импорт == "DE", "исходное значение должно остаться читаемым"
    assert автор == "придуманный сотрудник"
