"""Миграции библиотеки применяются в две схемы одной базы, и обе выходят полными.

История. Проверки файлов миграции искали ограничение по имени во всей базе:
«if exists (select 1 from pg_constraint where conname = '…')». pg_constraint
видит все схемы, и 24.09.2026 в library_sql_test с одной оставшейся тестовой
схемой brands_schema.sql, применённый во вторую, упал целиком: поиск нашёл
чужую проверку, и снос пошёл на таблицу, у которой её нет. Обратная форма
(«if not exists … then add constraint») не падала, а молча пропускала: во
второй схеме таблицы оставались без одиннадцати проверок, и тесты на ней были
зелёными без того, что проверяли.

Прод это не задевало — там схема одна. Задевало тесты: каждый применяет эти
файлы в свою схему общей базы, и две схемы разом (параллельный прогон или
брошенная прерванным тестом) давали то падение, то проверку без проверки.

Тест применяет четыре файла в схему A, затем в B, затем ещё раз в A (файлы
обязаны быть повторяемыми, правило 21) и сравнивает ограничения обеих схем по
самим таблицам — через conrelid, а не по имени.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from tests.test_library_schema_sql import операторы

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
нужна_база = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМЫ = ("two_schemas_a", "two_schemas_b")
ФАЙЛЫ = ("schema.sql", "schema_junk.sql", "suppliers_schema.sql", "brands_schema.sql")

# Проверки, которые ставились под поиском по имени. Список — чтобы сравнение
# схем не прошло пустым: равенство двух пустых множеств ничего не доказывает.
ПРОВЕРКИ = {
    ("lib_prices", "lib_prices_basis_src_chk"),
    ("lib_prices", "lib_prices_pay_src_chk"),
    ("lib_prices", "lib_prices_lead_src_chk"),
    ("lib_prices", "lib_prices_make_src_chk"),
    ("lib_prices", "lib_prices_price_date_src_chk"),
    ("lib_cpi", "lib_cpi_country_chk"),
    ("lib_cpi", "lib_cpi_month_chk"),
    ("lib_cpi", "lib_cpi_value_chk"),
    ("lib_files", "lib_files_side_вид"),
    ("lib_files", "lib_files_doc_class_chk"),
    ("lib_ocr_writes", "lib_ocr_writes_mode_chk"),
    ("lib_brand_alias", "lib_brand_alias_статус"),
    ("lib_brand_alias", "lib_brand_alias_ключ_при_статусе"),
}


def ограничения(cur, схема: str) -> dict[tuple[str, str], str]:
    """(таблица, имя) → определение, для таблиц ЭТОЙ схемы.

    Соединение по conrelid: имя ограничения уникально в таблице, а не в базе.
    Имя схемы из определения снимается — внешний ключ на соседнюю таблицу
    выводится с ним, если схема не в search_path."""
    cur.execute("""
        select t.relname, k.conname, pg_get_constraintdef(k.oid)
          from pg_constraint k
          join pg_class t on t.oid = k.conrelid
          join pg_namespace n on n.oid = t.relnamespace
         where n.nspname = %s""", (схема,))
    return {(т, и): о.replace(схема + ".", "") for т, и, о in cur.fetchall()}


def применить(cur, схема: str) -> None:
    cur.execute(f"set search_path to {схема}")
    for имя in ФАЙЛЫ:
        for оператор in операторы((ROOT / "library" / "supabase" / имя).read_text(encoding="utf-8")):
            cur.execute(оператор)   # падение здесь и есть провал теста


@нужна_база
def test_две_схемы_получают_одни_и_те_же_ограничения():
    import psycopg2
    from psycopg2.extensions import parse_dsn

    config = parse_dsn(DSN)
    assert config["host"] in ("127.0.0.1", "localhost")
    assert config["dbname"] == "library_sql_test"

    созданные_роли: list[str] = []
    conn = psycopg2.connect(DSN)
    conn.autocommit = True   # CREATE INDEX CONCURRENTLY в транзакции запрещён
    cur = conn.cursor()
    try:
        for роль in ("anon", "authenticated", "service_role"):
            cur.execute("select 1 from pg_roles where rolname = %s", (роль,))
            if not cur.fetchone():
                cur.execute(f"create role {роль} nologin")
                созданные_роли.append(роль)
        for схема in СХЕМЫ:
            cur.execute(f"drop schema if exists {схема} cascade")
            cur.execute(f"create schema {схема}")

        а, б = СХЕМЫ
        применить(cur, а)
        первая = ограничения(cur, а)
        # Вторая схема — при живой первой: именно здесь поиск по имени во всей
        # базе ронял brands_schema.sql и пропускал проверки schema.sql.
        применить(cur, б)
        вторая = ограничения(cur, б)
        # И снова первая — при живой второй: повторный прогон обязан пройти и
        # ничего не потерять и не задвоить.
        применить(cur, а)
        первая_снова = ограничения(cur, а)

        нет_в_первой = ПРОВЕРКИ - первая.keys()
        assert not нет_в_первой, f"в схеме {а} нет проверок: {sorted(нет_в_первой)}"
        нет_во_второй = sorted(первая.keys() - вторая.keys())
        assert not нет_во_второй, f"в схеме {б} не поставлены: {нет_во_второй}"
        лишние = sorted(вторая.keys() - первая.keys())
        assert not лишние, f"в схеме {б} лишние: {лишние}"
        разные = sorted(к for к in первая if первая[к] != вторая[к])
        assert not разные, f"определения расходятся: {разные}"
        assert первая_снова == первая, "повторное применение изменило ограничения"
    finally:
        for схема in СХЕМЫ:
            cur.execute(f"drop schema if exists {схема} cascade")
        # Роли-заглушки — только свои: в чужой базе они могут быть настоящими.
        for роль in созданные_роли:
            cur.execute(f"drop role if exists {роль}")
        conn.close()


def поиски_ограничения_без_таблицы(sql: str) -> list[str]:
    """Обращения к pg_constraint, не привязанные к таблице через conrelid.

    Комментарии сняты заранее (операторы() их выбрасывает): пояснение
    «раньше искали в pg_constraint по имени» не должно ни ловиться, ни
    прикрывать настоящую строку. Окно — до конца условия: then, loop или «;»."""
    код = ";\n".join(операторы(sql))
    найдено = []
    for m in re.finditer(r"\bpg_constraint\b", код):
        конец = re.compile(r"\bthen\b|\bloop\b|;").search(код, m.end())
        окно = код[m.start():конец.start() if конец else len(код)]
        if "conrelid" not in окно:
            найдено.append(" ".join(окно.split())[:120])
    return найдено


def test_поиск_ограничения_привязан_к_таблице():
    """Во всех миграциях репозитория, а не только в четырёх из теста выше."""
    плохие = []
    for путь in sorted(ROOT.rglob("*.sql")):
        if ".git" in путь.parts:
            continue
        for кусок in поиски_ограничения_без_таблицы(путь.read_text(encoding="utf-8")):
            плохие.append(f"{путь.relative_to(ROOT)}: {кусок}")
    assert not плохие, ("ограничение ищется по имени во всей базе, а не у таблицы "
                        "(conrelid = to_regclass('…')):\n" + "\n".join(плохие))


def test_распознаватель_ловит_поиск_по_имени_и_пропускает_комментарий():
    плохо = ("do $$ begin\n"
             "  if not exists (select 1 from pg_constraint where conname = 'x_chk') then\n"
             "    alter table x add constraint x_chk check (a > 0);\n"
             "  end if;\n"
             "end $$;")
    хорошо = ("-- было: select 1 from pg_constraint where conname = 'x_chk'\n"
              "do $$ begin\n"
              "  if not exists (select 1 from pg_constraint where conname = 'x_chk'\n"
              "                   and conrelid = to_regclass('x')) then\n"
              "    alter table x add constraint x_chk check (a > 0);\n"
              "  end if;\n"
              "end $$;")
    assert len(поиски_ограничения_без_таблицы(плохо)) == 1
    assert поиски_ограничения_без_таблицы(хорошо) == []
