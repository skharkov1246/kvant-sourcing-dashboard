"""Карточки портала связаны по всей цепочке: схема → прогон миграций → воркер → страница.

ЗАЧЕМ ОТДЕЛЬНО ОТ test_portal_entity_sql.py. Тот проверяет функции на базе и
без базы пропускается. Здесь то, что базы не требует и ломается молча:
  · файл схемы не попал в прогон миграций — функций в рабочей базе так и не
    появится, а страница будет честно говорить «карточки не установлены»;
  · воркер зовёт функцию не тем именем или не с тем параметром — PostgREST
    ответит 404, и это неотличимо от «не установлены»;
  · оболочка страницы не под версией (`.gitignore` глушит `public/*.html`) —
    локально всё зелёное, а на сайт страница не уезжает;
  · роль Supabase названа в схеме напрямую — на чистой базе файл падает целиком
    (CLAUDE.md, правило 20);
  · функция карточки что-то пишет — а она обязана только читать.
Разбор читает код, а не комментарии (CLAUDE.md, стиль работы).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
СХЕМА = ROOT / "library" / "supabase" / "portal_entity_schema.sql"
ФУНКЦИИ = {"portal_code": "portalCode", "portal_brand": "portalBrand", "portal_supplier": "portalSupplier",
           "portal_model": "portalModel", "portal_unit": "portalUnit"}


def без_комментариев_sql(текст: str) -> str:
    return "\n".join(строка.split("--")[0] for строка in текст.splitlines())


def без_комментариев_js(текст: str) -> str:
    return re.sub(r"(?m)^\s*//.*$", "", текст)


def test_схема_карточек_в_прогоне_миграций_после_поиска():
    yml = (ROOT / ".github" / "workflows" / "zip-db.yml").read_text(encoding="utf-8")
    файлы = [s.strip().split()[1] for s in yml.splitlines() if s.strip().startswith("apply ")]
    assert "library/supabase/portal_entity_schema.sql" in файлы
    где = файлы.index("library/supabase/portal_entity_schema.sql")
    for опора in ("schema.sql", "schema_junk.sql", "suppliers_schema.sql", "brands_schema.sql",
                  "portal_schema.sql"):
        assert файлы.index("library/supabase/" + опора) < где, опора
    # Ужесточение доступа ЗИП — по-прежнему последним.
    assert файлы[-1] == "zip/supabase/migrations_rls_stage1.sql"


def test_воркер_зовёт_те_функции_с_теми_параметрами():
    sql = без_комментариев_sql(СХЕМА.read_text(encoding="utf-8"))
    js = без_комментариев_js((ROOT / "public" / "_worker.js").read_text(encoding="utf-8"))
    for функция, маршрут in ФУНКЦИИ.items():
        m = re.search(rf"create function {функция}\((\w+) text\) returns jsonb", sql)
        assert m, f"в схеме нет {функция}(<параметр> text) returns jsonb"
        вызов = re.search(rf'{маршрут}: \{{ param: "(\w+)", fn: "(\w+)", arg: "(\w+)"', js)
        assert вызов, маршрут
        assert вызов.group(2) == функция and вызов.group(3) == m.group(1), (маршрут, вызов.groups(), m.group(1))
    for путь in ("/api/portal/code", "/api/portal/brand", "/api/portal/supplier", "/api/portal/model",
                 "/api/portal/unit"):
        assert f'"{путь}"' in js, путь
    assert '"/rest/v1/rpc/" + вид.fn' in js


def test_адреса_страницы_и_параметры_совпадают_с_воркером():
    """Страница спрашивает ?k=, ?b=, ?s=, ?id= — ровно те параметры, что читает воркер."""
    js = без_комментариев_js((ROOT / "public" / "_worker.js").read_text(encoding="utf-8"))
    стр = (ROOT / "public" / "portal_entity.js").read_text(encoding="utf-8")
    for маршрут, путь in (("portalCode", "/api/portal/code"), ("portalBrand", "/api/portal/brand"),
                          ("portalSupplier", "/api/portal/supplier"), ("portalModel", "/api/portal/model"),
                          ("portalUnit", "/api/portal/unit")):
        параметр = re.search(rf'{маршрут}: \{{ param: "(\w+)"', js).group(1)
        assert f'"{путь}?{параметр}="' in стр, (путь, параметр)


def test_оболочка_страницы_под_версией_и_подключает_поиск_в_шапке():
    html = (ROOT / "public" / "portal_entity.html").read_text(encoding="utf-8")
    подключение = '<script src="/portal_search.js" defer></script>'
    assert html.count(подключение) == 1
    assert html.index(подключение) < html.index("</head>")
    assert 'id="card"' in html and 'href="/"' in html
    # public/*.html заглушён в .gitignore целиком; оболочка обязана быть исключением.
    игнор = subprocess.run(["git", "check-ignore", "-q", "public/portal_entity.html"], cwd=ROOT)
    assert игнор.returncode == 1, "public/portal_entity.html заглушён .gitignore — на сайт не уедет"


def test_роли_supabase_в_схеме_только_через_проверку_наличия():
    sql = без_комментариев_sql(СХЕМА.read_text(encoding="utf-8"))
    for роль in ("anon", "authenticated", "service_role"):
        for m in re.finditer(rf"\b{роль}\b", sql):
            assert sql[m.start() - 1] == "'" and sql[m.end()] == "'", (роль, sql[m.start() - 60:m.end() + 20])
    assert "from pg_roles" in sql


def test_функции_карточек_ничего_не_пишут():
    sql = без_комментариев_sql(СХЕМА.read_text(encoding="utf-8")).lower()
    тела = re.findall(r"create (?:or replace )?function\s+(\w+)\(.*?(\$fn\$|\$\$)(.*?)\2;", sql, re.S)
    assert {имя for имя, _, _ in тела} >= set(ФУНКЦИИ) | {"portal_qty", "portal_brand_rows"}, [имя for имя, _, _ in тела]
    for имя, _, тело in тела:
        for слово in ("insert into", "update ", "delete from", "truncate", "create temp", "create table",
                      "drop table", "alter table"):
            assert слово not in тело, (имя, слово)
        assert re.search(r"language (plpgsql|sql) (stable|immutable)", sql[sql.index(f"function {имя}("):]), имя


def test_машина_и_узел_закрыты_правом_библиотеки():
    """Машина и узел — данные библиотеки: воркер пускает к ним по сайту
    knowledge (как /library и как ссылки на машину в поиске), а не только по
    праву suppliers. Проверка — по коду разбора раздела: маршруты машины и узла
    идут через список PORTAL_LIBRARY_ROUTES, и проверка права стоит ДО вызова
    карточки."""
    js = без_комментариев_js((ROOT / "public" / "_worker.js").read_text(encoding="utf-8"))
    assert re.search(r'PORTAL_LIBRARY_ROUTES = \["portalModel", "portalUnit"\]', js)
    ветка = js[js.index("if (PORTAL_LIBRARY_ROUTES.includes(suppliers))"):]
    ветка = ветка[:ветка.index("return portalEntity(suppliers")]
    assert 'includes("knowledge")' in ветка and '"forbidden"' in ветка and "rights.admin" in ветка


def test_ссылки_на_машину_и_узел_ведут_на_их_карточки():
    """Поиск и карточки кода и бренда ведут на /p#model= и /p#unit=; прежняя
    заглушка «у библиотеки нет адреса машины» ушла, а прежний раздел
    библиотеки остался второй ссылкой, как у кода и бренда."""
    стр = без_комментариев_js((ROOT / "public" / "portal_entity.js").read_text(encoding="utf-8"))
    поиск = без_комментариев_js((ROOT / "public" / "portal_search.js").read_text(encoding="utf-8"))
    assert '"/p#model=" + к(id)' in стр and '"/p#unit=" + к(id)' in стр
    assert "/^#(code|brand|supplier|model|unit)=(.+)$/" in стр
    assert "нет адреса машины" not in стр
    assert 'return "/p#model=" + k;' in поиск and 'return "/p#unit=" + k;' in поиск
    assert '"/library#section=component"' in поиск and '"/library#segment="' in поиск
