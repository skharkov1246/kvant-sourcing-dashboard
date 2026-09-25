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


def открытые_объекты(cur, схема: str = ИМЯ) -> list[tuple[str, str, str, str]]:
    """Права anon, authenticated и PUBLIC на все объекты схемы, кроме функций.

    Прямо по relacl, без information_schema: role_table_grants последовательности
    не показывает вовсе. PUBLIC в acl — нулевой oid, поэтому имя через case."""
    cur.execute("""
        select c.relname, c.relkind::text,
               case when a.grantee = 0 then 'PUBLIC' else pg_get_userbyid(a.grantee) end,
               a.privilege_type
          from pg_class c
          join pg_namespace ns on ns.oid = c.relnamespace,
               aclexplode(c.relacl) a
         where ns.nspname = %s
           and (a.grantee = 0 or pg_get_userbyid(a.grantee) in ('anon', 'authenticated'))
         order by 1, 3, 4""", (схема,))
    return cur.fetchall()


def test_служебная_роль_получает_последовательности_своей_схемы(база):
    """Выдача service_role обязана попасть в схему, куда применён файл.

    До 25.09.2026 блок последовательностей был прибит к public: в отдельной
    схеме он не находил ни одной своей последовательности, и выдача, как и
    отзыв, молча ничего не делала."""
    with база.cursor() as cur:
        применить(cur)
        cur.execute("""
            select c.relname, has_sequence_privilege('service_role', c.oid, 'usage'),
                   has_sequence_privilege('service_role', c.oid, 'select')
              from pg_class c join pg_namespace ns on ns.oid = c.relnamespace
             where ns.nspname = %s and c.relkind = 'S'""", (ИМЯ,))
        строки = cur.fetchall()
    assert строки, "последовательностей не создано — проверять нечего"
    без_прав = [имя for имя, usage, select in строки if not (usage and select)]
    assert not без_прав, f"у service_role нет usage/select: {без_прав}"


def test_права_по_умолчанию_как_у_supabase_закрыты(база):
    """Всё открытое правами по умолчанию схема обязана закрыть сама.

    На голом PostgreSQL у anon изначально нет ничего, и отзыв там проверять не
    на чем: пропущенный revoke так же зелен, как сделанный. Supabase в public
    выдаёт по умолчанию всё на таблицы, виды и последовательности anon и
    authenticated — здесь то же в схеме теста, и после применения у них не
    должно остаться ни одного права. Смотрятся ВСЕ объекты схемы, а не
    sup_* по имени: последовательность таблицы с другим именем блок отзыва
    пропустил бы, а этот тест — нет."""
    with база.cursor() as cur:
        for вид in ("tables", "sequences", "functions"):
            cur.execute(f"alter default privileges in schema {ИМЯ} grant all on {вид} "
                        "to anon, authenticated, service_role")
        применить(cur)
        открыто = открытые_объекты(cur)
        cur.execute("""select count(*) from pg_class c
                         join pg_namespace ns on ns.oid = c.relnamespace
                        where ns.nspname = %s and c.relkind = 'S'""", (ИМЯ,))
        последовательностей = cur.fetchone()[0]
    assert последовательностей >= 4, "последовательностей меньше, чем bigserial в файле"
    assert not открыто, f"права по умолчанию не сняты: {открыто}"


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
