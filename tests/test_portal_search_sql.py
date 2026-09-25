"""Единый поиск портала (portal_search) на настоящей схеме.

ЗАЧЕМ. Поиск — вход на стартовую страницу: код, бренд, поставщик, машина и
узел по одному слову. Ошибка в нём не роняет страницу, а молча отвечает «ничего
не нашлось», и отличить это от «такого у нас нет» некому. Поэтому проверяется
ответ по существу на каждом источнике по отдельности: код, известный только
спросу, только каталогу, только КП и только как аналог; бренд по написанию;
поставщик по ИНН, домену и номеру; машина по прежнему имени.

И то, без чего этому нельзя верить:
  · опоры, которых может не быть (реестр брендов, вид sup_name_shown, проверка
    правдоподобия lib_pn_plausible), проверяются в обе стороны — есть и нет;
  · граница диапазона «начинается с» не теряет ни одного ключа ни в одном
    правиле сравнения, какие есть в базе (C и ICU, где кириллица раньше
    латиницы, а «й» — это «и» на втором уровне);
  · большие таблицы читаются по индексам — проверено счётчиком чтений
    транзакции, а не текстом плана, при запрещённом последовательном чтении;
  · усечение по бюджету времени видно строкой «усечено», а не тишиной.

Схемы применяются в свою схему одноразовой базы, как прогон миграций, файл
поиска — дважды (идемпотентность). Корпус придуман (CLAUDE.md, правило 18),
ответы посчитаны руками. Работает при поднятой базе PostgreSQL 16 в локали
C.UTF-8 (LIBRARY_SQL_TEST_DSN, правило 21а).
"""
from __future__ import annotations

import os
import re
import random
from pathlib import Path

import pytest

from tests.test_library_schema_sql import операторы

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

ИМЯ = "portal_search_sql_test"
ГОЛАЯ = "portal_search_sql_bare"
ФАЙЛЫ = ("schema.sql", "schema_junk.sql", "suppliers_schema.sql", "brands_schema.sql",
         "portal_schema.sql", "portal_schema.sql")
# Без реестра брендов: так стоит база, где brands_schema.sql ещё не применяли.
ФАЙЛЫ_ГОЛОЙ = ("schema.sql", "schema_junk.sql", "suppliers_schema.sql", "portal_schema.sql")

КОРПУС = """
insert into lib_segments (id, name) values ('gtu', 'ГТУ выдуманные');
insert into lib_demand (deal_id, item_name, oem, part_number) values
 ('D1','Втулка выдуманная','Kelton','QX-1001'),
 ('D2','Втулка выдуманная','Келтон','QX 1001'),
 ('D3','Втулка выдуманная',null,'QX-1002'),
 ('D4','Пункт договора','Junkbrand','QX-1003'),
 ('D5','Сталь выдуманная',null,'SS316'),
 ('D6','Подшипник выдуманный','SKF','AB-6205'),
 ('D7','Кольцо выдуманное',null,'DIN 471 25'),
 ('D8','Болт выдуманный',null,'DIN 933');
insert into lib_row_junk (demand_id, rule, run_id)
  select id, 'proza-тест', 'тест' from lib_demand where part_number = 'QX-1003';
insert into lib_units (id, name, name_en, crit) values ('hot', 'Горячая часть', 'Hot section', 'A');
insert into lib_units (id, parent_id, name, name_en, crit) values
 ('hot.liner', 'hot', 'Жаровая труба', 'Combustion liner', 'A');
insert into lib_models (id, name, oem, legacy, aliases, segment_id, kind) values
 ('vm400', 'ВМ-400', 'Выдуманный завод', 'Циклоп', array['Циклоп', 'VM 400'], 'gtu', 'турбина');
insert into lib_fleet (id, site, model_id) values ('пл1', 'Выдуманная площадка', 'vm400');
insert into lib_parts (id, catalog_no, name, oem, unit_id, kv_no) values
 ('zc2002', 'ZC-2002', 'Седло выдуманное', 'Kelton GmbH', 'hot.liner', 'KV-000753-4'),
 ('kl7', 'KL-7', 'Клапан выдуманный', 'Kelton GmbH', 'hot.liner', null);
insert into lib_part_models (part_id, model_id) values ('kl7', 'vm400'), ('zc2002', 'vm400');
insert into lib_part_alt (part_id, alt_pn, kind, alt_maker) values
 ('kl7', 'AN-4004', 'аналог', 'Выдуманный литейщик');
insert into lib_prices (feed, source, part_number, item_name, price, currency, rfq_id, rfq_company, oem) values
 ('разбор КП','КП','PR-3003','Кольцо выдуманное',10,'USD','R1','1101','Келтон'),
 ('разбор КП','КП','PR 3003','Кольцо выдуманное',11,'USD','R2','1101','Kelton'),
 ('разбор КП','КП','PR-3003','Кольцо выдуманное',12,'USD','R3','1201','SKF'),
 ('разбор КП','КП','AB-6205','Подшипник выдуманный',5,'USD','R1','1101','SKF'),
 ('ТКП КВАНТ (отпускная цена)','КП','ZZ-9999','Нечто выдуманное',99,'USD','R1','1101',null);
"""

КОРПУС_РЕЕСТРА = """
insert into lib_brands (brand_key, name, owner, country, rule, run_id) values
 ('kelton', 'Kelton GmbH', 'Выдуманный холдинг', 'Нигдения', 'тест', 'тест'),
 ('skf', 'SKF', null, null, 'тест', 'тест');
insert into lib_brand_alias (spelling, spelling_key, source, seen_at, brand_key, status, n_rows, rule, run_id) values
 ('Келтон', lib_brand_key('Келтон'), 'dict/oem.json', 'a', 'kelton', 'разрешено', 7, 'тест', 'тест'),
 ('Kelton', lib_brand_key('Kelton'), 'dict/oem.json', 'b', 'kelton', 'разрешено', 3, 'тест', 'тест'),
 ('Kelton GmbH', lib_brand_key('Kelton GmbH'), 'lib_parts.oem', 'c', 'kelton', 'разрешено', 2, 'тест', 'тест'),
 ('SKF', lib_brand_key('SKF'), 'dict/oem.json', 'a', 'skf', 'разрешено', 5, 'тест', 'тест'),
 ('Выдуманный литейщик', lib_brand_key('Выдуманный литейщик'), 'lib_parts.oem', 'd', null,
  'в очереди', 1, 'тест', 'тест');
"""

КОРПУС_ПОСТАВЩИКОВ = """
insert into sup_entity (id, kind, display_name, resolution, status, country) values
 ('KV-S-000011-1','legal','alphabearings','candidate','active','Нигдения'),
 ('KV-S-000012-2','legal','Бета Уплотнения','resolved','active', null);
insert into sup_entity (id, kind, display_name, resolution, status, merged_into) values
 ('KV-S-000013-3','legal','gammaold','merged','active','KV-S-000012-2');
insert into sup_identifier (sup_id, kind, value, value_norm, source, status, run_id) values
 ('KV-S-000011-1','bitrix','1101','1101','bitrix','verified','r1'),
 ('KV-S-000011-1','inn','7700000001','7700000001','реквизиты','verified','r1'),
 ('KV-S-000011-1','domain','alpha-bearings.example','ALPHABEARINGSEXAMPLE','сведение','stated','r1'),
 ('KV-S-000012-2','bitrix','1201','1201','bitrix','verified','r1'),
 ('KV-S-000012-2','alias','Бета Уплотнения','БЕТАУПЛОТНЕНИЯ','сведение','stated','r1'),
 ('KV-S-000013-3','inn','7700000003','7700000003','реквизиты','verified','r1');
-- Вечный номер выдан только Бете: Альфа ждёт ИНН, её запись — не номер.
insert into sup_number_registry (sup_id, seq, run_id) values ('KV-S-000012-2', 12, 'r1');
"""

# Показываемое имя из карточки Битрикса: у сущности KV-S-000011-1 реестровое
# имя — ключ («alphabearings»), а люди знают её как «Альфа-Подшипник».
КОРПУС_ИМЁН = """
insert into sup_display_name (sup_id, source, name, run_id) values
 ('KV-S-000011-1', 'bitrix:title', 'Альфа-Подшипник', 'r1');
"""

# Стоит вместо проверки соседней работы (library/supabase/schema.sql,
# lib_pn_plausible): здесь нужно лишь, чтобы она БЫЛА и отвергала марку стали.
ПРАВДОПОДОБИЕ = """
create function lib_pn_plausible(t text) returns boolean language sql immutable as $$
  select lib_pn_key(t) !~ '^(ss|aisi)(304|316)l?$'
$$
"""

КОЛОНКИ = ("kind", "key", "title", "subtitle", "brand", "brand_key", "brand_src", "segment",
           "counts", "source", "rank")


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
    conn.autocommit = True      # в миграции CREATE INDEX CONCURRENTLY, как у psql
    созданные: list[str] = []
    c = conn.cursor()
    try:
        for роль in ("anon", "authenticated", "service_role"):
            c.execute("select 1 from pg_roles where rolname = %s", (роль,))
            if not c.fetchone():
                c.execute(f"create role {роль} nologin")
                созданные.append(роль)
        _схема(c, ИМЯ, ФАЙЛЫ)
        c.execute(КОРПУС)
        c.execute(КОРПУС_РЕЕСТРА)
        c.execute(КОРПУС_ПОСТАВЩИКОВ)
        c.execute(КОРПУС_ИМЁН)
        c.execute("analyze")
        yield conn
    finally:
        c.execute("reset search_path")
        c.execute(f"drop schema if exists {ИМЯ} cascade")
        c.execute(f"drop schema if exists {ГОЛАЯ} cascade")
        for роль in созданные:
            c.execute(f"drop role if exists {роль}")
        conn.close()


def искать(conn, q, схема: str = ИМЯ, lim: int = 10) -> list[dict]:
    c = conn.cursor()
    c.execute(f"set search_path to {схема}")
    c.execute("select " + ", ".join(КОЛОНКИ) + " from portal_search(%s, %s)", (q, lim))
    return [dict(zip(КОЛОНКИ, r)) for r in c.fetchall()]


def по_виду(строки: list[dict], вид: str) -> dict[str, dict]:
    return {r["key"]: r for r in строки if r["kind"] == вид}


# ── коды: по источнику ───────────────────────────────────────────────────────

def test_код_только_из_спроса(база):
    коды = по_виду(искать(база, "QX-1001"), "код")
    r = коды["qx1001"]
    assert r["rank"] == 0 and r["source"] == "спрос"
    assert r["counts"] == {"deals": 2, "demand_rows": 2}
    # Бренд не из каталога: «Kelton» и «Келтон» — одно и то же по реестру, их
    # частоты складываются, и в ответ идёт имя реестра, а не слово спецификации.
    assert (r["brand"], r["brand_key"], r["brand_src"]) == ("Kelton GmbH", "kelton", "частота")


def test_помеченная_строка_спроса_кода_не_даёт(база):
    assert not по_виду(искать(база, "QX-1003"), "код")
    коды = по_виду(искать(база, "QX-10"), "код")
    # По началу ключа — оба живых кода, ранг «начало», помеченного нет.
    assert set(коды) == {"qx1001", "qx1002"}
    assert {r["rank"] for r in коды.values()} == {1}
    # Бренда у QX-1002 не названо — и он не выдуман.
    assert коды["qx1002"]["brand"] is None and коды["qx1002"]["brand_key"] is None


def test_код_только_из_каталога_и_по_номеру_kv(база):
    r = по_виду(искать(база, "ZC-2002"), "код")["zc2002"]
    assert r["source"] == "каталог" and r["counts"] == {"catalog": 1}
    assert (r["brand"], r["brand_key"], r["brand_src"]) == ("Kelton GmbH", "kelton", "каталог")
    assert "наш номер KV-000753-4" in r["subtitle"]
    # Наш номер ведёт на ту же деталь, хотя ключ у неё другой.
    kv = по_виду(искать(база, "KV-000753-4"), "код")
    assert list(kv) == ["zc2002"] and kv["zc2002"]["rank"] == 0


def test_код_только_из_кп(база):
    r = по_виду(искать(база, "pr 3003"), "код")["pr3003"]
    assert r["source"] == "КП" and r["title"] == "PR-3003"
    assert r["counts"] == {"offers": 3, "suppliers": 2}
    # Келтон ×1 + Kelton ×1 против SKF ×1: самый частый бренд реестра — kelton.
    assert (r["brand_key"], r["brand_src"]) == ("kelton", "частота")
    # Цена не из разбора КП (наша отпускная) кода не даёт.
    assert not по_виду(искать(база, "ZZ-9999"), "код")


def test_код_только_как_аналог(база):
    r = по_виду(искать(база, "AN-4004"), "код")["an4004"]
    assert r["source"] == "аналог" and r["counts"] == {"analog_of": 1}
    assert "аналог к KL-7" in r["subtitle"]
    # Изготовитель аналога в реестре не разрешён: слово есть, бренда реестра нет.
    assert (r["brand"], r["brand_key"], r["brand_src"]) == ("Выдуманный литейщик", None, "аналог")


def test_код_и_бренд_стоят_рядом_у_каждого_кода(база):
    for q in ("QX-1001", "ZC-2002", "PR-3003", "AB-6205"):
        for r in искать(база, q):
            if r["kind"] == "код":
                assert r["brand"], (q, r)


# ── бренды, поставщики, машины, узлы ─────────────────────────────────────────

def test_бренд_по_написанию(база):
    r = по_виду(искать(база, "келтон"), "бренд")["kelton"]
    assert r["rank"] == 0 and r["title"] == "Kelton GmbH"
    assert "написание «Келтон»" in r["subtitle"]
    assert r["counts"] == {"spellings": 3, "rows": 12}
    # Правовая форма снимается ключом написания: «KELTON GmbH» — тот же бренд.
    assert по_виду(искать(база, "KELTON GmbH"), "бренд")["kelton"]["rank"] == 0
    # Начало написания — ранг «начало».
    assert по_виду(искать(база, "kelt"), "бренд")["kelton"]["rank"] == 1


def test_поставщик_по_инн_домену_номеру_и_имени(база):
    r = по_виду(искать(база, "7700000001"), "поставщик")["KV-S-000011-1"]
    assert (r["source"], r["rank"]) == ("ИНН", 0)
    # Имя — показываемое (карточка Битрикса), а не реестровый ключ.
    assert r["title"] == "Альфа-Подшипник"
    assert r["counts"] == {"offers": 3, "codes": 2}
    d = по_виду(искать(база, "https://www.alpha-bearings.example/contacts"), "поставщик")
    assert d["KV-S-000011-1"]["source"] == "домен" and d["KV-S-000011-1"]["rank"] == 0
    assert по_виду(искать(база, "KV-S-000011-1"), "поставщик")["KV-S-000011-1"]["rank"] == 0
    # Номер без дефисов и контрольной цифры — по началу.
    assert по_виду(искать(база, "kvs000011"), "поставщик")["KV-S-000011-1"]["rank"] == 1
    а = по_виду(искать(база, "Альфа"), "поставщик")
    assert а["KV-S-000011-1"]["source"] == "название"


def test_слитый_поставщик_ведёт_на_того_в_кого_слит(база):
    строки = по_виду(искать(база, "7700000003"), "поставщик")
    assert list(строки) == ["KV-S-000012-2"]
    assert "ИНН 7700000003" in строки["KV-S-000012-2"]["subtitle"]


def test_номер_в_подписи_только_выданный(база):
    """Подпись поиска, раздел «Поставщики» и карточка /p говорят о номере одно:
    выдан — номер, не выдан — «номер не выдан», а не ключ записи."""
    бета = по_виду(искать(база, "7700000003"), "поставщик")["KV-S-000012-2"]
    assert бета["subtitle"].startswith("KV-S-000012-2 · ")
    альфа = по_виду(искать(база, "7700000001"), "поставщик")["KV-S-000011-1"]
    assert альфа["subtitle"].startswith("номер не выдан · ") and "KV-S-000011-1" not in альфа["subtitle"]
    # Ключ записи остаётся ключом строки: по нему открывается карточка.
    assert альфа["key"] == "KV-S-000011-1"


def test_машина_по_прежнему_имени_и_узел_по_имени(база):
    m = по_виду(искать(база, "Циклоп"), "машина")["vm400"]
    assert m["rank"] == 0 and m["title"] == "ВМ-400" and m["segment"] == "gtu"
    assert m["counts"] == {"parts": 2, "fleet": 1}
    assert по_виду(искать(база, "VM 400"), "машина")["vm400"]["rank"] == 0
    u = по_виду(искать(база, "жаровая"), "узел")["hot.liner"]
    assert u["rank"] == 1 and "Горячая часть" in u["subtitle"]
    assert u["counts"] == {"parts": 2, "children": 0}
    assert по_виду(искать(база, "Combustion liner"), "узел")["hot.liner"]["rank"] == 0


def test_короткий_и_длинный_запрос_пусты(база):
    for q in ("", " ", "x", "Q", "я" * 81):
        assert искать(база, q) == [], q


def test_в_ответе_только_агрегаты(база):
    """Ни номеров сделок, ни файлов: в counts — только числа и флаг усечения."""
    for q in ("QX-1001", "PR-3003", "7700000001", "келтон", "Циклоп"):
        for r in искать(база, q):
            for k, v in (r["counts"] or {}).items():
                assert isinstance(v, (int, bool)), (q, k, v)
            текст = " ".join(str(v) for v in r.values())
            assert "D1" not in текст and "R1" not in текст, (q, r)


def test_стандарт_с_размером_код_голый_стандарт_нет(база):
    """Правило schema.sql: «DIN 471 25» в спросе — код, «DIN 933» — голый стандарт.
    Кандидат поиска — ключ, и стандарт с размером судит lib_pn_std_sized."""
    c = база.cursor()
    c.execute(f"set search_path to {ИМЯ}")
    текст = (ROOT / "library" / "supabase" / "schema.sql").read_text(encoding="utf-8")
    for имя in ("lib_pn_plausible", "lib_pn_std_sized"):
        m = re.search(rf"create or replace function {имя}\(.*?\$(fn)?\$;", текст, re.S)
        c.execute(m.group(0))
    try:
        assert "din47125" in по_виду(искать(база, "DIN 471 25"), "код")
        assert "din47125" in по_виду(искать(база, "din471"), "код")
        assert not по_виду(искать(база, "DIN 933"), "код")
        # Без функции по ключу стандарт с размером по одному ключу — стандарт.
        c.execute("drop function lib_pn_std_sized(text[])")
        assert not по_виду(искать(база, "DIN 471 25"), "код")
    finally:
        c.execute(f"set search_path to {ИМЯ}")
        c.execute("drop function if exists lib_pn_std_sized(text[])")
        c.execute("drop function if exists lib_pn_plausible(text)")


# ── опоры, которых может не быть ─────────────────────────────────────────────

def test_марка_материала_не_код_когда_есть_проверка(база):
    c = база.cursor()
    c.execute(f"set search_path to {ИМЯ}")
    # С 24.09.2026 (PR #418) lib_pn_plausible приходит со schema.sql. Проверяем
    # оба состояния базы: без функции поиск отдаёт код как есть, с ней — нет.
    c.execute("drop function if exists lib_pn_plausible(text)")
    assert "ss316" in по_виду(искать(база, "SS316"), "код")   # проверки нет — код как есть
    c.execute(ПРАВДОПОДОБИЕ)
    try:
        assert not по_виду(искать(база, "SS316"), "код")
        assert "qx1001" in по_виду(искать(база, "QX-1001"), "код")
    finally:
        c.execute(f"set search_path to {ИМЯ}")
        c.execute("drop function lib_pn_plausible(text)")
    assert "ss316" in по_виду(искать(база, "SS316"), "код")


def test_без_реестра_и_вида_имён_поиск_работает(база):
    c = база.cursor()
    _схема(c, ГОЛАЯ, ФАЙЛЫ_ГОЛОЙ)
    c.execute(КОРПУС)
    c.execute(КОРПУС_ПОСТАВЩИКОВ)
    c.execute("drop view sup_name_shown")
    # Бренд — словом каталога или спецификации, ключа реестра нет.
    r = по_виду(искать(база, "ZC-2002", ГОЛАЯ), "код")["zc2002"]
    assert (r["brand"], r["brand_key"], r["brand_src"]) == ("Kelton GmbH", None, "каталог")
    q = по_виду(искать(база, "QX-1001", ГОЛАЯ), "код")["qx1001"]
    assert q["brand"] in ("Kelton", "Келтон") and q["brand_key"] is None
    assert q["brand_src"] == "написание"
    assert not по_виду(искать(база, "келтон", ГОЛАЯ), "бренд")
    # Имя поставщика — реестровое, раз показываемого нет.
    s = по_виду(искать(база, "7700000001", ГОЛАЯ), "поставщик")["KV-S-000011-1"]
    assert s["title"] == "alphabearings"


def test_усечение_по_бюджету_видно(база):
    c = база.cursor()
    c.execute("set portal_search.budget_ms = '0'")
    try:
        строки = искать(база, "QX-1001")
    finally:
        c.execute("reset portal_search.budget_ms")
    усечено = {r["key"] for r in строки if r["kind"] == "усечено"}
    assert усечено == {"бренд", "машина", "узел", "код"}
    assert not по_виду(строки, "код")


def test_схема_применяется_повторно_и_права_только_сервису(база):
    c = база.cursor()
    c.execute(f"set search_path to {ИМЯ}")
    for роль, можно in (("anon", False), ("authenticated", False), ("service_role", True)):
        c.execute("select has_function_privilege(%s, %s, 'execute')",
                  (роль, f"{ИМЯ}.portal_search(text, int)"))
        assert c.fetchone()[0] is можно, роль


# ── планы: большие таблицы — только по индексам ──────────────────────────────

def test_спрос_и_кп_читаются_по_индексам(база):
    """Счётчики чтений транзакции при запрещённом последовательном чтении.

    enable_seqscan = off не запрещает полное чтение, а делает его последним
    выбором: если условие до индекса не доходит, полное чтение всё равно
    будет — либо таблицы (seq_scan), либо ДРУГОГО индекса, либо того же самого
    целиком, по порядку, с фильтром (так читается «order by ключ limit 1» с
    LIKE вместо диапазона). Поэтому считается и путь, и число прочитанных
    записей индекса: перед поиском в таблицы кладутся три тысячи строк с
    ключами, стоящими в индексе РАНЬШЕ искомых. Диапазон их не читает, полный
    проход — читает все. Строки живут только внутри откатываемой транзакции.
    Так проверяется поведение, а не текст плана.
    """
    c = база.cursor()
    c.execute(f"set search_path to {ИМЯ}")
    # Какими индексами читать можно.
    можно = {"lib_demand": {"lib_demand_pnkey"},
             "lib_prices": {"lib_prices_pn_key", "lib_prices_rfqco"}}

    # Счётчик «транзакции» — это ещё не сброшенные счета сеанса, и в них могут
    # лежать чтения прежних тестов. Поэтому меряется разница внутри транзакции:
    # сброс посреди транзакции не происходит.
    def счета():
        c.execute("""
            -- У таблицы numscans — это последовательные чтения (так же считает
            -- seq_scan в pg_stat_xact_user_tables), у индекса — его чтения.
            select t.relname, 'seq', pg_stat_get_xact_numscans(t.oid), 0
              from pg_class t join pg_namespace n on n.oid = t.relnamespace
             where n.nspname = %s and t.relname in ('lib_demand', 'lib_prices', 'lib_row_junk')
            union all
            select t.relname, ix.relname, pg_stat_get_xact_numscans(ix.oid),
                   pg_stat_get_xact_tuples_returned(ix.oid)
              from pg_class t join pg_namespace n on n.oid = t.relnamespace
              join pg_index i on i.indrelid = t.oid join pg_class ix on ix.oid = i.indexrelid
             where n.nspname = %s and t.relname in ('lib_demand', 'lib_prices', 'lib_row_junk')""",
                  (ИМЯ, ИМЯ))
        return {(r[0], r[1]): (int(r[2]), int(r[3])) for r in c.fetchall()}

    c.execute("begin")
    try:
        c.execute("insert into lib_demand (deal_id, item_name, part_number) "
                  "select 'F', 'Заполнитель выдуманный', 'AA-' || g from generate_series(1, 3000) g")
        c.execute("insert into lib_prices (feed, source, part_number, item_name, price, currency) "
                  "select 'разбор КП', 'КП', 'AA-' || g, 'Заполнитель', 1, 'USD' "
                  "from generate_series(1, 3000) g")
        c.execute("set local enable_seqscan = off")
        до = счета()
        for q in ("QX-10", "PR-3003", "KV-000753-4", "AB-6205", "7700000001"):
            c.execute("select count(*) from portal_search(%s, 10)", (q,))
        после = счета()
    finally:
        c.execute("rollback")
    чтения, записей = {}, {}
    for k, (n, t) in после.items():
        n0, t0 = до.get(k, (0, 0))
        if n - n0:
            чтения[k] = n - n0
            записей[k] = t - t0
    for таблица, индексы in можно.items():
        по_таблице = {путь: n for (t, путь), n in чтения.items() if t == таблица}
        assert set(по_таблице) <= индексы and по_таблице, (таблица, чтения)
    assert ("lib_row_junk", "seq") not in чтения, чтения
    # Пять поисков по корпусу в десяток строк: диапазоны читают десятки
    # записей, полный проход — три тысячи заполнителей на каждом шаге.
    assert all(n < 300 for n in записей.values()), записей


# ── граница «начинается с» ───────────────────────────────────────────────────

def test_граница_префикса_не_теряет_ключей_ни_в_одном_правиле_сравнения(база):
    """Диапазон [k, portal_prefix_hi(k)) обязан накрывать ВСЕ ключи с началом k.

    Лишнее диапазон захватить может — его снимает starts_with; потерянное не
    вернёт ничто. Слова набраны из знаков на стыках алфавитов (9|a, z|а, и|й|к,
    я) — ровно там граница и ошибалась бы.
    """
    c = база.cursor()
    c.execute(f"set search_path to {ИМЯ}")
    c.execute("select collname from pg_collation where collname in "
              "('C', 'C.utf8', 'und-x-icu', 'en-US-x-icu', 'ru-RU-x-icu')")
    правила = [r[0] for r in c.fetchall()]
    assert "C" in правила
    rnd = random.Random(20260924)
    for знаки in ("09azабийкяеь", "09AZАБИЙКЯЕЬ"):
        слова = sorted({"".join(rnd.choice(знаки) for _ in range(rnd.randint(1, 6)))
                        for _ in range(1500)})
        начала = sorted({w[:rnd.randint(1, len(w))] for w in слова})
        c.execute("create temp table слова (w text)")
        c.execute("create temp table начала (k text, hi text)")
        try:
            c.execute("insert into слова select unnest(%s::text[])", (слова,))
            c.execute("insert into начала select k, portal_prefix_hi(k) from unnest(%s::text[]) k",
                      (начала,))
            c.execute("select count(*) from начала where hi is not null")
            assert c.fetchone()[0] > len(начала) // 2
            for правило in правила:
                c.execute(f"""
                    select count(*) from начала n join слова s on starts_with(s.w, n.k)
                     where n.hi is not null
                       and not (s.w = n.k or (s.w collate "{правило}" > n.k collate "{правило}"
                                              and s.w collate "{правило}" < n.hi collate "{правило}"))""")
                assert c.fetchone()[0] == 0, (знаки, правило)
        finally:
            c.execute("drop table слова")
            c.execute("drop table начала")


def test_граница_префикса_на_примерах(база):
    c = база.cursor()
    c.execute(f"set search_path to {ИМЯ}")
    for k, hi in (("6205", "6206"), ("ab9", "ac"), ("kz", "l"), ("9", None), ("z9", None),
                  ("ри", "рк"), ("рй", "рк"), ("я", None), ("KVS0", "KVS1"), ("", None)):
        c.execute("select portal_prefix_hi(%s)", (k,))
        assert c.fetchone()[0] == hi, k
