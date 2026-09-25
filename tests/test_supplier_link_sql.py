"""Связь реестра разведки с реестром компаний — на настоящей схеме PostgreSQL.

Что проверено (шаг 4 портала; схема library/supabase/supplier_link_schema.sql,
прогон library/supplier_link.py, карточки portal_code и portal_supplier):
  · вхолостую прогон ничего не пишет, а в журнал идут только агрегаты;
  · ИНН сводит, домен сайта и почты сводит, общий почтовый домен — нет;
  · имя даёт только кандидата, и база сама не пустит имя в связь;
  · спор «разведка → две сущности» виден числом и строками спора;
  · связь ведёт в корень цепочки слияний — и тогда, когда слияние случилось
    после прогона; чужой id разведки связью не становится;
  · откат по ключу прогона — пометкой: действующим снова становится прежний;
  · карточка кода ведёт «кто делает» на компанию, карточка компании
    показывает её поставщиков разведки;
  · без таблицы связи (схема не применена) карточки работают как раньше;
  · файл применяется повторно, после него — схема поставщиков, и без ролей
    Supabase; права — только у сервисной роли.

Корпус придуман (CLAUDE.md, правило 18): зоны .example, синтетические ИНН с
верной контрольной суммой. Работает при поднятой базе PostgreSQL 16 в локали
C.UTF-8 (LIBRARY_SQL_TEST_DSN, правило 21а).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from tests.test_library_schema_sql import операторы

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from library import supplier_link as sl  # noqa: E402

DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМА = "supplier_link_sql_test"
ГОЛАЯ = "supplier_link_sql_bare"
БЕЗ_РОЛЕЙ = "supplier_link_sql_noroles"
СВЯЗЬ = "supplier_link_schema.sql"
ФАЙЛЫ = ("schema.sql", "schema_junk.sql", "suppliers_schema.sql", "portal_schema.sql", СВЯЗЬ,
         "portal_entity_schema.sql")
ФАЙЛЫ_ГОЛОЙ = ("schema.sql", "schema_junk.sql", "suppliers_schema.sql", "portal_schema.sql",
               "portal_entity_schema.sql")

А, Б, В, Г, Д, Е, Ж = ("KV-S-000031-1", "KV-S-000032-2", "KV-S-000033-3", "KV-S-000034-4",
                       "KV-S-000035-5", "KV-S-000036-6", "KV-S-000037-7")

# Разведка: 1 — домен сайта; 2 — ИНН в тексте; 3 — только gmail; 4 — только имя;
# 5 — сайт одной компании, почта другой (спор); 6 — почта того же домена, что у 1;
# 7 — домен слитой сущности (связь уходит в корень).
КОРПУС = f"""
insert into lib_suppliers (id, name, name_key, kind, country, site, contact_email, contact_phone, strengths)
overriding system value values
 (1, 'Выдуманный склад', 'выдуманный склад', 'дистрибьютор', 'Нигдения',
  'https://www.sklad-vydum.example/каталог', 'info@sklad-vydum.example', '+0 000 000-00-01', null),
 (2, 'Выдуманный завод', 'выдуманный завод', 'OEM', 'Нигдения', null, null, null,
  'реквизиты: ИНН 0000000018'),
 (3, 'Почтовый торговец', 'почтовый торговец', 'трейдер', null, null, 'trader.vydum@gmail.com', null, null),
 (4, 'ООО «Бета Уплотнения Выдуманные»', 'бета уплотнения выдуманные', 'сервис', null, null, null, null, null),
 (5, 'Спорная витрина', 'спорная витрина', 'трейдер', null, 'gamma-vydum.example',
  'x@delta-vydum.example', null, null),
 (6, 'Дочка склада', 'дочка склада', 'дистрибьютор', null, null, 'sales@sklad-vydum.example', null, null),
 (7, 'Старое имя завода', 'старое имя завода', 'OEM', null, 'old-vydum.example', null, null, null);
insert into lib_parts (id, catalog_no, name, oem) values ('kl7', 'KL-7', 'Клапан выдуманный', 'Kelton GmbH');
insert into lib_part_suppliers (part_id, supplier_id, makes, verdict, in_stock, price, currency) values
 ('kl7', 1, 'клапаны выдуманные', 'in_stock', 'yes', 88, 'EUR'),
 ('kl7', 2, 'делает под заказ', null, null, null, null),
 ('kl7', 3, 'перепродаёт', null, null, null, null);
insert into sup_entity (id, kind, display_name, resolution) values
 ('{А}', 'legal', 'skladvydum', 'resolved'),
 ('{Б}', 'legal', 'Выдуманный Завод Б', 'resolved'),
 ('{В}', 'legal', 'Почтовая компания', 'candidate'),
 ('{Г}', 'legal', 'Бета Уплотнения Выдуманные', 'candidate'),
 ('{Д}', 'legal', 'Гамма выдуманная', 'candidate'),
 ('{Е}', 'legal', 'Дельта выдуманная', 'candidate');
insert into sup_entity (id, kind, display_name, resolution, merged_into) values
 ('{Ж}', 'legal', 'oldvydum', 'merged', '{Б}');
insert into sup_identifier (sup_id, kind, value, value_norm, source, status, run_id) values
 ('{А}', 'domain', 'sklad-vydum.example', 'SKLADVYDUMEXAMPLE', 'сведение', 'verified', 'm1'),
 ('{Б}', 'inn', '0000000018', '0000000018', 'реквизиты', 'verified', 'm1'),
 ('{В}', 'domain', 'gmail.com', 'GMAILCOM', 'сведение', 'stated', 'm1'),
 ('{Г}', 'alias', 'бетауплотнениявыдуманные', 'БЕТАУПЛОТНЕНИЯВЫДУМАННЫЕ', 'сведение', 'stated', 'm1'),
 ('{Г}', 'legal', 'ооо', 'ООО', 'правовая форма', 'stated', 'm1'),
 ('{Д}', 'domain', 'gamma-vydum.example', 'GAMMAVYDUMEXAMPLE', 'сведение', 'stated', 'm1'),
 ('{Е}', 'domain', 'delta-vydum.example', 'DELTAVYDUMEXAMPLE', 'сведение', 'stated', 'm1'),
 ('{Ж}', 'domain', 'old-vydum.example', 'OLDVYDUMEXAMPLE', 'сведение', 'stated', 'm1');
insert into sup_number_registry (sup_id, seq, run_id) values ('{А}', 31, 'm1');
insert into sup_display_name (sup_id, source, name, run_id) values ('{А}', 'bitrix:title', 'Склад Выдумка', 'n1');
"""
# Чего в журнале прогона быть не должно ни в каком виде (правило 17).
ТАЙНЫ = ("vydum", "Выдуман", "торговец", "склад", "0000000018", "@", "gmail")


def _схема(c, имя: str, файлы: tuple[str, ...]) -> None:
    c.execute(f"drop schema if exists {имя} cascade")
    c.execute(f"create schema {имя}")
    c.execute(f"set search_path to {имя}")
    for файл in файлы:
        for оператор in операторы((ROOT / "library" / "supabase" / файл).read_text(encoding="utf-8")):
            c.execute(оператор)


@pytest.fixture(scope="module")
def база():
    import psycopg2
    from psycopg2.extensions import parse_dsn

    assert parse_dsn(DSN)["dbname"] == "library_sql_test"
    conn = psycopg2.connect(DSN)
    conn.autocommit = True      # в миграциях CREATE INDEX CONCURRENTLY, как у psql
    созданные: list[str] = []
    c = conn.cursor()
    try:
        for роль in ("anon", "authenticated", "service_role"):
            c.execute("select 1 from pg_roles where rolname = %s", (роль,))
            if not c.fetchone():
                c.execute(f"create role {роль} nologin")
                созданные.append(роль)
        _схема(c, СХЕМА, ФАЙЛЫ)
        c.execute(КОРПУС)
        c.execute("analyze")
        yield conn
    finally:
        c.execute("reset search_path")
        for сх in (СХЕМА, ГОЛАЯ, БЕЗ_РОЛЕЙ):
            c.execute(f"drop schema if exists {сх} cascade")
        for роль in созданные:
            c.execute(f"drop role if exists {роль}")
        conn.close()


def соединение(схема: str = СХЕМА):
    """Своё соединение прогону: он сам делает commit и rollback."""
    import psycopg2
    return psycopg2.connect(DSN, options=f"-c search_path={схема}")


@pytest.fixture()
def мягкие_гейты(monkeypatch):
    """Корпус мал: один спор на пять ключей — это 20 %, а порог рабочий — 5 %.
    Сам гейт проверен без базы (tests/test_supplier_link.py)."""
    monkeypatch.setitem(sl.ГЕЙТЫ, "макс_доля_споров", 0.5)


def прогнать(схема: str = СХЕМА, **kw) -> int:
    conn = соединение(схема)
    try:
        return sl.прогон(conn, **{"apply": False, "run_id": "link-t0", **kw})
    finally:
        conn.close()


@pytest.fixture(scope="module")
def записано(база):
    """Первый прогон с записью — link-t1; тесты ниже читают его картину."""
    старый = sl.ГЕЙТЫ["макс_доля_споров"]
    sl.ГЕЙТЫ["макс_доля_споров"] = 0.5
    try:
        assert прогнать(apply=True, run_id="link-t1") == 0
    finally:
        sl.ГЕЙТЫ["макс_доля_споров"] = старый
    return "link-t1"


def выбрать(conn, sql: str, *args, схема: str = СХЕМА):
    c = conn.cursor()
    c.execute(f"set search_path to {схема}")
    c.execute(sql, args)
    return c.fetchall()


def вызвать(conn, функция: str, аргумент, схема: str = СХЕМА):
    return выбрать(conn, f"select {функция}(%s)", аргумент, схема=схема)[0][0]


def живые(conn, схема: str = СХЕМА) -> dict[int, str]:
    return dict(выбрать(conn, "select research_id, sup_id from sup_research_link_live", схема=схема))


# ── замер вхолостую ─────────────────────────────────────────────────────────

def test_вхолостую_ничего_не_пишет_и_журнал_только_агрегаты(база, мягкие_гейты, capsys):
    до = выбрать(база, "select count(*) from sup_research_link")[0][0]
    assert прогнать(apply=False) == 0
    вывод = capsys.readouterr().out
    assert выбрать(база, "select count(*) from sup_research_link")[0][0] == до
    assert "ВСЕГО поставщиков разведки сведено:    4 из 7 (57.1 %)" in вывод
    assert "строк «кто делает» со ссылкой:         2 из 3 (66.7 %)" in вывод
    assert "разведка → две и больше сущностей:     1" in вывод
    assert "Прогон ВХОЛОСТУЮ" in вывод
    for тайна in ТАЙНЫ:
        assert тайна not in вывод, тайна


def test_гейт_споров_отменяет_запись(база, capsys):
    """Рабочий порог — 5 %: один спор из пяти ключей запись отменяет."""
    до = выбрать(база, "select count(*) from sup_research_link")[0][0]
    assert прогнать(apply=True, run_id="link-гейт") == 1
    assert "ГЕЙТЫ НЕ СОШЛИСЬ" in capsys.readouterr().out
    assert выбрать(база, "select count(*) from sup_research_link")[0][0] == до


# ── запись ──────────────────────────────────────────────────────────────────

def test_сильные_ключи_сводят_общий_домен_нет(база, записано):
    assert живые(база) == {1: А, 2: Б, 6: А, 7: Б}
    строки = выбрать(база, """
        select research_id, sup_id, rule, status, confidence::float, note from sup_research_link
         where run_id = %s order by research_id, sup_id, rule""", записано)
    связи = {(r[0], r[1], r[2]) for r in строки if r[3] == "link"}
    assert связи == {(1, А, "домен сайта"), (2, Б, "инн"), (6, А, "домен почты"),
                     (7, Ж, "домен сайта")}      # пишется носитель признака, корень — на чтении
    # gmail.com у сущности В — общий домен: торговец с почтой на нём не сведён ничем.
    assert not [r for r in строки if r[0] == 3]
    правила = dict(выбрать(база, "select research_id, rule from sup_research_link_live"))
    assert правила == {1: "домен сайта", 2: "инн", 6: "домен почты", 7: "домен сайта"}


def test_имя_только_кандидат(база, записано):
    [кандидат] = выбрать(база, "select sup_id, rule, status, note from sup_research_link "
                               "where run_id = %s and research_id = 4", записано)
    assert кандидат[:3] == (Г, "имя", "candidate")
    assert 4 not in живые(база)
    import psycopg2
    with pytest.raises(psycopg2.errors.CheckViolation):
        выбрать(база, "insert into sup_research_link (research_id, research_key, sup_id, rule, status, "
                      "confidence, run_id) values (4, 'бета уплотнения выдуманные', %s, 'имя', 'link', 0.3, "
                      "'link-обман')", Г)


def test_спор_виден_и_не_сводит(база, записано):
    споры = выбрать(база, "select sup_id, rule, note from sup_research_link "
                          "where run_id = %s and research_id = 5 and status = 'conflict' order by sup_id",
                    записано)
    assert споры == [(Д, "домен сайта", "ключи ведут в разные сущности"),
                     (Е, "домен почты", "ключи ведут в разные сущности")]
    assert 5 not in живые(база)


def test_слияние_после_прогона_и_чужой_id(база, записано):
    c = база.cursor()
    c.execute(f"set search_path to {СХЕМА}")
    c.execute("begin")
    try:
        # А слили в Б уже после прогона — связь идёт в новый корень без прогона.
        c.execute("update sup_entity set merged_into = %s, resolution = 'merged' where id = %s", (Б, А))
        # Разведку пересобрали, и под номером 2 теперь другая компания.
        c.execute("update lib_suppliers set name_key = 'совсем другая' where id = 2")
        c.execute("select research_id, sup_id from sup_research_link_live")
        assert dict(c.fetchall()) == {1: Б, 6: Б, 7: Б}
    finally:
        c.execute("rollback")
    assert живые(база) == {1: А, 2: Б, 6: А, 7: Б}


# ── карточки портала ────────────────────────────────────────────────────────

def test_кто_делает_ведёт_на_компанию(база, записано):
    r = вызвать(база, "portal_code", "KL-7")
    по_имени = {m["name"]: m for m in r["makers"]}
    склад = по_имени["Выдуманный склад"]
    assert склад["company"] == {"id": А, "name": "Склад Выдумка", "src": "bitrix:title", "number": А}
    assert (склад["link"], склад["verdict"]) == ("домен сайта", "in_stock")
    завод = по_имени["Выдуманный завод"]
    assert (завод["company"]["id"], завод["company"]["number"], завод["link"]) == (Б, None, "инн")
    торговец = по_имени["Почтовый торговец"]
    assert (торговец["company"], торговец["link"]) == (None, None)
    assert r["makers_n"] == 3
    текст = json.dumps(r, ensure_ascii=False)
    assert "@" not in текст and "000-00-01" not in текст


def test_компания_видит_своих_поставщиков_разведки(база, записано):
    а = вызвать(база, "portal_supplier", А)
    assert а["research_n"] == 2
    assert [(x["name"], x["rule"], x["parts"], x["checked"]) for x in а["research"]] == [
        ("Выдуманный склад", "домен сайта", 1, 1), ("Дочка склада", "домен почты", 0, 0)]
    б = вызвать(база, "portal_supplier", Б)
    # Слитая Ж — член Б: её поставщик разведки — тоже Б.
    assert {x["name"] for x in б["research"]} == {"Выдуманный завод", "Старое имя завода"}
    г = вызвать(база, "portal_supplier", Г)
    assert (г["research_n"], г["research"]) == (0, [])     # кандидат по имени — не связь


# ── откат ───────────────────────────────────────────────────────────────────

def test_откат_пометкой_возвращает_прежний_прогон(база, записано, мягкие_гейты, capsys):
    всего = выбрать(база, "select count(*) from sup_research_link")[0][0]
    c = база.cursor()
    c.execute(f"set search_path to {СХЕМА}")
    # Второй прогон: у А пропал домен — прежняя связь склада и дочки теряется.
    c.execute("update sup_identifier set status = 'rejected' where sup_id = %s and kind = 'domain'", (А,))
    try:
        assert прогнать(apply=True, run_id="link-t2") == 0
        вывод = capsys.readouterr().out
        assert "потеряли связь:         2" in вывод
        assert живые(база) == {2: Б, 7: Б}
        assert прогнать(rollback="link-t2") == 0
        # Действующим снова стал link-t1, строки t2 не удалены, а помечены.
        assert живые(база) == {1: А, 2: Б, 6: А, 7: Б}
        помечено = выбрать(база, "select count(*) from sup_research_link "
                                 "where run_id = 'link-t2' and rolled_back_at is not null")[0][0]
        assert помечено > 0
        assert прогнать(rollback="link-t1") == 0
        assert живые(база) == {}
        assert выбрать(база, "select count(*) from sup_research_link")[0][0] == всего + помечено
        # Ключ с опечаткой — не молчаливый успех.
        assert прогнать(rollback="link-нет-такого") == 3
    finally:
        c.execute("update sup_identifier set status = 'verified' where sup_id = %s and kind = 'domain'", (А,))
        c.execute("update sup_research_link set rolled_back_at = null where run_id = %s", (записано,))
    assert живые(база) == {1: А, 2: Б, 6: А, 7: Б}


# ── без таблицы связи — как раньше ──────────────────────────────────────────

def test_без_схемы_связи_карточки_и_замер_как_раньше(база, мягкие_гейты, capsys):
    c = база.cursor()
    _схема(c, ГОЛАЯ, ФАЙЛЫ_ГОЛОЙ)
    c.execute(КОРПУС)
    r = вызвать(база, "portal_code", "KL-7", схема=ГОЛАЯ)
    assert r["makers_n"] == 3
    assert all(m["company"] is None and m["link"] is None for m in r["makers"])
    assert {m["name"] for m in r["makers"]} == {"Выдуманный склад", "Выдуманный завод", "Почтовый торговец"}
    s = вызвать(база, "portal_supplier", А, схема=ГОЛАЯ)
    assert (s["research_n"], s["research"], s["name"]) == (None, [], "Склад Выдумка")
    # Замер работает и без таблицы; запись без неё отказывает с причиной.
    assert прогнать(ГОЛАЯ) == 0
    assert "ДЕЙСТВУЮЩЕГО ПРОГОНА НЕТ" in capsys.readouterr().out
    assert прогнать(ГОЛАЯ, apply=True, run_id="link-голая") == 2


# ── схема: повторно, после неё схема поставщиков, права, без ролей ──────────

def test_схема_повторно_и_схема_поставщиков_после(база, записано):
    c = база.cursor()
    c.execute(f"set search_path to {СХЕМА}")
    for файл in (СВЯЗЬ, "suppliers_schema.sql", СВЯЗЬ):
        for оператор in операторы((ROOT / "library" / "supabase" / файл).read_text(encoding="utf-8")):
            c.execute(оператор)
    assert живые(база) == {1: А, 2: Б, 6: А, 7: Б}


def test_права_только_сервису(база):
    for объект in ("sup_research_link", "sup_research_link_live"):
        for роль, можно in (("anon", False), ("authenticated", False), ("service_role", True)):
            [(есть,)] = выбрать(база, "select has_table_privilege(%s, %s, 'select')",
                                роль, f"{СХЕМА}.{объект}")
            assert есть is можно, (объект, роль)
    [(anon_пишет,)] = выбрать(база, "select has_table_privilege('anon', %s, 'insert')",
                              f"{СХЕМА}.sup_research_link")
    assert anon_пишет is False
    [(сила, форс)] = выбрать(база, "select relrowsecurity, relforcerowsecurity from pg_class c "
                                   "join pg_namespace n on n.oid = c.relnamespace "
                                   "where n.nspname = %s and c.relname = 'sup_research_link'", СХЕМА)
    assert сила and форс


def test_схема_применяется_без_ролей_supabase(база):
    """Правило 20 — по-настоящему: pg_roles подменён пустым видом, как на
    чистом PostgreSQL. Файл применяется целиком и не выдаёт прав никому."""
    c = база.cursor()
    try:
        c.execute(f"drop schema if exists {БЕЗ_РОЛЕЙ} cascade")
        c.execute(f"create schema {БЕЗ_РОЛЕЙ}")
        c.execute(f"create view {БЕЗ_РОЛЕЙ}.pg_roles as select rolname from pg_catalog.pg_roles where false")
        c.execute(f"set search_path to {БЕЗ_РОЛЕЙ}, pg_catalog")
        c.execute("select count(*) from pg_roles where rolname in ('anon', 'authenticated', 'service_role')")
        assert c.fetchone()[0] == 0, "подмена pg_roles не действует — проверка ничего бы не доказала"
        for файл in ("schema.sql", "suppliers_schema.sql", СВЯЗЬ):
            for оператор in операторы((ROOT / "library" / "supabase" / файл).read_text(encoding="utf-8")):
                c.execute(оператор)
        for объект in ("sup_research_link", "sup_research_link_live"):
            c.execute("select has_table_privilege('service_role', %s, 'select')", (f"{БЕЗ_РОЛЕЙ}.{объект}",))
            assert c.fetchone()[0] is False, объект
            c.execute("select count(*) from information_schema.role_table_grants "
                      "where table_schema = %s and table_name = %s and grantee = 'PUBLIC'", (БЕЗ_РОЛЕЙ, объект))
            assert c.fetchone()[0] == 0, объект
    finally:
        c.execute("reset search_path")
        c.execute(f"drop schema if exists {БЕЗ_РОЛЕЙ} cascade")
