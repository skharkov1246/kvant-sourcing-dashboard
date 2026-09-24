"""Единый поиск портала связан по всей цепочке: схема → прогон миграций → воркер → страницы.

ЗАЧЕМ ОТДЕЛЬНО ОТ test_portal_search_sql.py. Тот проверяет функцию на базе и
без базы пропускается. Здесь то, что базы не требует и ломается молча:
  · файл схемы не попал в прогон миграций — функция в рабочей базе так и не
    появится, а страница будет честно говорить «поиск не установлен» годами;
  · воркер зовёт функцию не тем именем или не с теми параметрами — PostgREST
    ответит 404, и это неотличимо от «не установлен»;
  · страница раздела потеряла строку подключения — поиска на ней нет, а все
    тесты страницы зелёные, потому что они о другом;
  · роль Supabase названа в схеме напрямую — на чистой базе файл падает целиком
    (CLAUDE.md, правило 20).
Разбор читает код, а не комментарии (CLAUDE.md, стиль работы).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
СХЕМА = ROOT / "library" / "supabase" / "portal_schema.sql"
СТРАНИЦЫ = ("suppliers", "nomenclature", "brands", "counters")
ПОДКЛЮЧЕНИЕ = '<script src="/portal_search.js" defer></script>'


def без_комментариев_sql(текст: str) -> str:
    return "\n".join(строка.split("--")[0] for строка in текст.splitlines())


def без_комментариев_js(текст: str) -> str:
    return re.sub(r"(?m)^\s*//.*$", "", текст)


def test_схема_поиска_в_прогоне_миграций_после_схем_библиотеки():
    yml = (ROOT / ".github" / "workflows" / "zip-db.yml").read_text(encoding="utf-8")
    строки = [s.strip() for s in yml.splitlines() if s.strip().startswith("apply ")]
    файлы = [s.split()[1] for s in строки]
    assert "library/supabase/portal_schema.sql" in файлы
    где = файлы.index("library/supabase/portal_schema.sql")
    for опора in ("schema.sql", "schema_junk.sql", "suppliers_schema.sql", "brands_schema.sql"):
        assert файлы.index("library/supabase/" + опора) < где, опора
    # Ужесточение доступа ЗИП — по-прежнему последним.
    assert файлы[-1] == "zip/supabase/migrations_rls_stage1.sql"


def test_воркер_зовёт_ту_функцию_с_теми_параметрами():
    sql = без_комментариев_sql(СХЕМА.read_text(encoding="utf-8"))
    m = re.search(r"create function portal_search\((\w+) text, (\w+) int default \d+\)", sql)
    assert m, "в схеме нет portal_search(q text, lim int …)"
    параметры = {m.group(1), m.group(2)}
    js = без_комментариев_js((ROOT / "public" / "_worker.js").read_text(encoding="utf-8"))
    assert '"/rest/v1/rpc/portal_search"' in js
    тело = re.search(r"/rest/v1/rpc/portal_search\".*?body: JSON\.stringify\(\{ (\w+), (\w+): ", js, re.S)
    assert тело and {тело.group(1), тело.group(2)} == параметры, тело and тело.groups()
    assert '"/api/portal/search"' in js


def test_страницы_раздела_подключают_общую_строку_один_раз_в_шапке():
    assert (ROOT / "public" / "portal_search.js").is_file()
    for имя in СТРАНИЦЫ:
        html = (ROOT / "public" / f"{имя}.html").read_text(encoding="utf-8")
        assert html.count(ПОДКЛЮЧЕНИЕ) == 1, имя
        assert html.index(ПОДКЛЮЧЕНИЕ) < html.index("</head>"), имя
        # Своя навигация страницы на месте: полоска добавляется, а не заменяет.
        assert 'href="/"' in html, имя


def test_роли_supabase_в_схеме_только_через_проверку_наличия():
    sql = без_комментариев_sql(СХЕМА.read_text(encoding="utf-8"))
    for роль in ("anon", "authenticated", "service_role"):
        for m in re.finditer(rf"\b{роль}\b", sql):
            # Имя роли допустимо только строкой в выборке из pg_roles.
            assert sql[m.start() - 1] == "'" and sql[m.end()] == "'", (роль, sql[m.start() - 60:m.end() + 20])
    assert "from pg_roles" in sql


def test_функция_ничего_не_пишет():
    """Поиск только читает: ни insert, ни update, ни delete в теле функции."""
    sql = без_комментариев_sql(СХЕМА.read_text(encoding="utf-8")).lower()
    тело = sql[sql.index("create function portal_search"):sql.index("end $fn$;", sql.index("create function portal_search"))]
    for слово in ("insert into", "update ", "delete from", "truncate", "create temp"):
        assert слово not in тело, слово
    assert "language plpgsql stable" in тело
