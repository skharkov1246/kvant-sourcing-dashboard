"""Карточки портала (portal_code, portal_brand, portal_supplier) на настоящей схеме.

ЗАЧЕМ. Карточка — место, куда ведёт каждая ссылка единого поиска и каждая
ссылка другой карточки. Ошибка в ней не роняет страницу, а тихо показывает не
то: чужой бренд у кода, аналог в списке оригиналов, мусорное количество
«3 163 518 182» штук, сжатый ключ вместо имени компании, карточку «кода» SS316.
Поэтому проверяется ответ по существу, по правилам номенклатуры (PDF владельцу
24.09.2026):
  · бренд позиции — с источником (каталог, спецификация, карточка запроса,
    маска кода, КП) и «спорно», когда источники позиции расходятся;
  · оригинал и аналоги — разными списками, у аналога названа причина;
  · количество — только правдоподобное (правило crossref.КОЛ_ЧИТАЕТСЯ буква в
    букву), иначе null, а строка остаётся;
  · имена — только человеческие: ни companyId портала, ни номера элемента
    СП-176, ни ключа реестра вместо имени;
  · код, отвергнутый правилом правдоподобия, карточки не получает, а в списках
    идёт без ссылки;
  · база без реестра брендов, вида имён и проверки правдоподобия отвечает;
  · функции исполняет только service_role; файл применяется повторно;
  · большие таблицы читаются по индексам — счётчиками чтений, как у поиска.

Корпус придуман (CLAUDE.md, правило 18), ответы посчитаны руками. Работает при
поднятой базе PostgreSQL 16 в локали C.UTF-8 (LIBRARY_SQL_TEST_DSN, правило 21а).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from tests.test_library_schema_sql import операторы

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

ИМЯ = "portal_entity_sql_test"
ГОЛАЯ = "portal_entity_sql_bare"
СХЕМА = "portal_entity_schema.sql"
ФАЙЛЫ = ("schema.sql", "schema_junk.sql", "suppliers_schema.sql", "brands_schema.sql",
         "portal_schema.sql", СХЕМА, СХЕМА)
# Без реестра брендов: так стоит база, где brands_schema.sql ещё не применяли.
ФАЙЛЫ_ГОЛОЙ = ("schema.sql", "schema_junk.sql", "suppliers_schema.sql", "portal_schema.sql", СХЕМА)

# Номера компаний портала и элементов СП-176 — пятизначные, чтобы их появление
# в ответе было видно поиском подстроки и не совпадало с ценами.
КОРПУС = """
insert into lib_segments (id, name) values ('gtu', 'ГТУ выдуманные');
insert into lib_demand (deal_id, item_name, oem, part_number, qty, unit) values
 ('D1','Втулка выдуманная','Kelton','QX-1001',4,'шт'),
 ('D2','Втулка выдуманная','Келтон','QX 1001',6,'шт'),
 ('D3','Втулка выдуманная',null,'QX-1002',null,null),
 ('D4','Пункт договора','Junkbrand','QX-1003',null,null),
 ('D5','Сталь выдуманная',null,'SS316',null,null),
 ('D6','Подшипник выдуманный','SKF','AB-6205',3163518182,'шт'),
 ('D7','Седло выдуманное','SKF','ZC-2002',2,'шт'),
 ('D8','Клапан выдуманный','Kelton','KL-7',1,'шт'),
 ('D9','Камера выдуманная',null,'MW21215M',12,'шт'),
 ('D10','Болт выдуманный',null,'DIN 912',100,'шт');
insert into lib_row_junk (demand_id, rule, run_id)
  select id, 'proza-тест', 'тест' from lib_demand where part_number = 'QX-1003';
insert into lib_units (id, name, name_en, crit) values ('hot', 'Горячая часть', 'Hot section', 'A');
insert into lib_units (id, parent_id, name, name_en, crit) values
 ('hot.liner', 'hot', 'Жаровая труба', 'Combustion liner', 'A');
insert into lib_models (id, name, oem, legacy, aliases, segment_id, kind) values
 ('vm400', 'ВМ-400', 'Kelton GmbH', 'Циклоп', array['Циклоп', 'VM 400'], 'gtu', 'турбина');
insert into lib_parts (id, catalog_no, name, oem, unit_id, kv_no) values
 ('zc2002', 'ZC-2002', 'Седло выдуманное', 'Kelton GmbH', 'hot.liner', 'KV-000753-4'),
 ('kl7', 'KL-7', 'Клапан выдуманный', 'Kelton GmbH', 'hot.liner', null),
 ('din933', 'DIN 933', 'Болт каталожный выдуманный', 'Kelton GmbH', null, null);
insert into lib_part_models (part_id, model_id) values ('kl7', 'vm400'), ('zc2002', 'vm400');
insert into lib_part_alt (part_id, alt_pn, kind, alt_maker) values
 ('kl7', 'AN-4004', 'аналог', 'Выдуманный литейщик'),
 ('kl7', 'SKF-7', 'замена', 'SKF'),
 ('zc2002', 'SS316', 'аналог', null);
insert into lib_pn_patterns (id, oem, pattern, meaning) values
 ('шифр.тест1', 'Kelton', 'MW#####X[/NN]', 'выдуманная шифровка'),
 ('шифр.тест2', 'SKF', 'числовой PN + суффикс', 'словесное описание — не маска');
insert into lib_prices (feed, source, part_number, item_name, price, currency, qty, total, rfq_id,
                        rfq_company, oem, rfq_brands, price_date, price_date_src, basis) values
 ('разбор КП','КП','PR-3003','Кольцо выдуманное',10,'USD',5,50,'R1','91101','Келтон',null,'2026-02-10','документ','EXW'),
 ('разбор КП','КП','PR 3003','Кольцо выдуманное',11,'USD',3163518182,11,'R2','91101','Kelton',null,null,'нет',null),
 ('разбор КП','КП','PR-3003','Кольцо выдуманное',12,'USD',null,null,'R3','91201','SKF',null,'2026-01-05','письмо',null),
 ('разбор КП','КП','AB-6205','Подшипник выдуманный',5,'USD',null,null,'R1','91101','SKF',null,null,null,null),
 ('разбор КП','КП','KL-7','Клапан выдуманный',100,'EUR',2,200,'R4','91101','Kelton',null,'2026-03-12','документ','DDP'),
 ('разбор КП','КП','KL-7','Клапан, аналог',90,'EUR',null,null,'R5','91301',null,null,'2026-03-01','карточка: создана',null),
 ('разбор КП','КП','KL-7','Клапан выдуманный',95,'EUR',4,999,'R6','91201','Kelton','50501,70777','2026-02-20','документ',null),
 ('разбор КП','КП','ZC-2002','Седло выдуманное',70,'USD',1,70,'R7','91201','Kelton GmbH',null,'2026-04-02','письмо',null),
 ('разбор КП','КП','QX-1001','Втулка выдуманная',7,'USD',10,70,'R8','91401','Kelton',null,'2026-05-01','документ',null),
 ('ТКП КВАНТ (отпускная цена)','КП','ZZ-9999','Нечто выдуманное',99,'USD',null,null,'R1','91101',null,null,null,null,null);
"""

КОРПУС_РЕЕСТРА = """
insert into lib_brands (brand_key, name, owner, country, rule, run_id) values
 ('kelton', 'Kelton GmbH', 'Выдуманный холдинг', 'Нигдения', 'тест', 'тест'),
 ('skf', 'SKF', null, null, 'тест', 'тест');
insert into lib_brand_alias (spelling, spelling_key, source, seen_at, sp176_id, brand_key, status,
                             n_rows, rule, run_id) values
 ('Келтон', lib_brand_key('Келтон'), 'dict/oem.json', 'a', null, 'kelton', 'разрешено', 7, 'тест', 'тест'),
 ('Kelton', lib_brand_key('Kelton'), 'lib_demand.oem', '', null, 'kelton', 'разрешено', 3, 'тест', 'тест'),
 ('Kelton GmbH', lib_brand_key('Kelton GmbH'), 'lib_parts.oem', '', null, 'kelton', 'разрешено', 2, 'тест', 'тест'),
 ('Kelton', lib_brand_key('Kelton'), 'СП-176', 'СП-176#50501', 50501, 'kelton', 'разрешено', 1, 'тест', 'тест'),
 ('Нечто выдуманное', lib_brand_key('Нечто выдуманное'), 'СП-176', 'СП-176#70777', 70777, null,
  'в очереди', 1, 'тест', 'тест'),
 ('SKF', lib_brand_key('SKF'), 'dict/oem.json', 'a', null, 'skf', 'разрешено', 5, 'тест', 'тест'),
 ('Выдуманный литейщик', lib_brand_key('Выдуманный литейщик'), 'lib_parts.oem', 'd', null, null,
  'в очереди', 1, 'тест', 'тест');
"""

КОРПУС_ПОСТАВЩИКОВ = """
insert into sup_entity (id, kind, display_name, resolution, status, country, city) values
 ('KV-S-000011-1','legal','alphabearings','candidate','active','Нигдения', null),
 ('KV-S-000012-2','legal','Бета Уплотнения','resolved','active', 'Нигдения', 'Выдумград');
insert into sup_entity (id, kind, display_name, resolution, status, merged_into) values
 ('KV-S-000013-3','legal','gammaold','merged','active','KV-S-000012-2');
insert into sup_identifier (sup_id, kind, value, value_norm, source, status, run_id) values
 ('KV-S-000011-1','bitrix','91101','91101','bitrix','verified','r1'),
 ('KV-S-000011-1','inn','7700000001','7700000001','реквизиты','verified','r1'),
 ('KV-S-000011-1','domain','alpha-bearings.example','ALPHABEARINGSEXAMPLE','сведение','stated','r1'),
 ('KV-S-000012-2','bitrix','91201','91201','bitrix','verified','r1'),
 ('KV-S-000012-2','alias','Бета Уплотнения','БЕТАУПЛОТНЕНИЯ','сведение','stated','r1'),
 ('KV-S-000013-3','bitrix','91401','91401','bitrix','verified','r1'),
 ('KV-S-000013-3','inn','7700000003','7700000003','реквизиты','verified','r1');
insert into sup_number_registry (sup_id, seq, run_id) values ('KV-S-000012-2', 12, 'r1');
insert into sup_fact (subject_kind, subject_id, field, value, status, source_type, method, method_ver, run_id)
values ('entity', 'KV-S-000012-2', 'rfq_stats',
        '{"sent": 5, "answered": 3, "quoted": 2, "silent": 1, "no_outcome": 1, "cards": 6,
          "who": "nobody@example.test"}', 'stated', 'bitrix', 'тест', '1', 'r1');
"""

# Имя из карточки Битрикса и реквизиты: у KV-S-000011-1 реестровое имя — ключ
# («alphabearings»), люди знают её как «Альфа-Подшипник».
КОРПУС_ИМЁН = """
insert into sup_display_name (sup_id, source, name, run_id) values
 ('KV-S-000011-1', 'bitrix:title', 'Альфа-Подшипник', 'r1');
insert into sup_display_name (sup_id, source, name, inn, run_id) values
 ('KV-S-000012-2', 'bitrix:requisite', null, '7700000012', 'r1');
"""

# Номера портала и справочника, которых в ответе быть не должно ни в каком поле.
НОМЕРА_СПРАВОЧНИКОВ = ("50501", "70777")


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


def вызвать(conn, функция: str, аргумент, схема: str = ИМЯ):
    c = conn.cursor()
    c.execute(f"set search_path to {схема}")
    c.execute(f"select {функция}(%s)", (аргумент,))
    return c.fetchone()[0]


def код(conn, q, схема: str = ИМЯ):
    return вызвать(conn, "portal_code", q, схема)


def бренд(conn, k, схема: str = ИМЯ):
    return вызвать(conn, "portal_brand", k, схема)


def поставщик(conn, s, схема: str = ИМЯ):
    return вызвать(conn, "portal_supplier", s, схема)


def коды_списка(строки) -> list:
    return [r.get("code") for r in строки]


# ── правило количества — то же, что у номенклатуры ───────────────────────────

def test_количество_правилом_номенклатуры_буква_в_букву():
    sys.path.insert(0, str(ROOT / "library"))
    import crossref
    sql = (ROOT / "library" / "supabase" / СХЕМА).read_text(encoding="utf-8")
    тело = sql[sql.index("create or replace function portal_qty"):]
    тело = тело[:тело.index("$$;")]
    # Псевдоним «p.» снят: функция встраивается в запрос, только когда её тело —
    # одно выражение без FROM (замер в шапке функции).
    assert crossref.КОЛ_ЧИТАЕТСЯ.replace("p.", "") in тело, "portal_qty разошлась с crossref.КОЛ_ЧИТАЕТСЯ"


def test_количество_мусор_скрыто(база):
    c = база.cursor()
    c.execute(f"set search_path to {ИМЯ}")
    for (qty, price, total), ждём in (
            ((4, 100, 400), 4), ((4, 100, None), 4), ((3163518182, 11, 11), None),
            ((4, 95, 999), None), ((0, 5, None), None), ((1000000, 1, None), 1000000),
            ((1000001, 1, None), None), ((10, 7, 70.02), 10)):
        c.execute("select portal_qty(%s, %s, %s)", (qty, price, total))
        got = c.fetchone()[0]
        assert (None if got is None else float(got)) == (None if ждём is None else float(ждём)), (qty, price, total)


def test_маска_кода():
    """Маска с «#» — выражение по ключу; словесное описание — не маска."""
    import psycopg2
    conn = psycopg2.connect(DSN)
    try:
        c = conn.cursor()
        c.execute(f"set search_path to {ИМЯ}")
        for маска, ключ, да in (("MW#####X[/NN]", "mw21215m", True), ("MW#####X[/NN]", "mw21215m01", True),
                                ("MW#####X[/NN]", "mw2121m", False), ("MW#####X[/NN]", "xmw21215m", False)):
            c.execute("select %s ~ portal_mask_re(%s)", (ключ, маска))
            assert c.fetchone()[0] is да, (маска, ключ)
        for не_маска in ("числовой PN + суффикс", "Typhoon / Tornado", "AB[#", None):
            c.execute("select portal_mask_re(%s)", (не_маска,))
            assert c.fetchone()[0] is None, не_маска
    finally:
        conn.close()


# ── карточка кода ────────────────────────────────────────────────────────────

def test_код_оригинал_и_аналог_отдельно(база):
    r = код(база, "KL-7")
    assert (r["key"], r["written"], r["name"], r["catalog"]) == ("kl7", "KL-7", "Клапан выдуманный", True)
    assert r["brand"] == {"key": "kelton", "name": "Kelton GmbH", "src": "каталог", "disputed": False}
    o = r["offers"]
    assert (o["rows"], o["original_n"], o["analog_n"], o["brand_judged"]) == (3, 2, 1, True)
    # Оригинал — новые первыми; у второй строки количество не сходится с суммой.
    assert [x["price"] for x in o["original"]] == [100, 95]
    первая, вторая = o["original"]
    assert (первая["qty"], первая["qty_hidden"], первая["total"], первая["basis"]) == (2, False, 200, "DDP")
    assert (первая["month"], первая["month_src"]) == ("2026-03", "документ")
    assert (вторая["qty"], вторая["qty_hidden"]) == (None, True)
    assert первая["company"] == {"id": "KV-S-000011-1", "name": "Альфа-Подшипник", "src": "bitrix:title",
                                 "number": None}
    assert первая["brand"] == {"key": "kelton", "name": "Kelton GmbH"}
    # Аналог по слову поставщика; компания не сведена с реестром — её нет, а
    # не номер портала.
    [аналог] = o["analog"]
    assert (аналог["price"], аналог["why"], аналог["company"]) == (90, "поставщик пишет «аналог»", None)


def test_код_аналог_по_бренду_строки_и_спор_когда_бренд_называют_только_кп(база):
    r = код(база, "PR-3003")
    # Позицию называют только КП: Келтон + Kelton (один бренд) против SKF.
    assert r["brand"] == {"key": "kelton", "name": "Kelton GmbH", "src": "КП", "disputed": True}
    o = r["offers"]
    assert [x["price"] for x in o["original"]] == [10, 11]
    [аналог] = o["analog"]
    assert аналог["price"] == 12 and аналог["brand"] == {"key": "skf", "name": "SKF"}
    assert аналог["why"] == "бренд строки — SKF, у позиции — Kelton GmbH"
    # Количество-мусор скрыто, строка осталась.
    вторая = o["original"][1]
    assert (вторая["qty"], вторая["qty_hidden"], вторая["month"], вторая["month_src"]) == (None, True, None, "нет")


def test_спорный_бренд_когда_каталог_и_спецификация_расходятся(база):
    r = код(база, "ZC-2002")
    assert r["brand"] == {"key": "kelton", "name": "Kelton GmbH", "src": "каталог", "disputed": True}
    бренды = {b["key"]: b for b in r["brands"]}
    assert бренды["kelton"]["sources"] == ["каталог", "КП"]
    assert бренды["skf"]["sources"] == ["спецификация"]
    assert r["kv_no"] == "KV-000753-4"
    # Аналог каталога с отвергнутым кодом — без ссылки, но в списке.
    [ss] = r["analogs"]
    assert (ss["code"], ss["written"], ss["brand"]) == (None, "SS316", None)


def test_бренд_из_карточки_запроса_и_маски(база):
    r = код(база, "KL-7")
    бренды = {(b["key"], b["name"]): b for b in r["brands"]}
    assert "карточка запроса" in бренды[("kelton", "Kelton GmbH")]["sources"]
    # Элемент справочника без бренда — написанием справочника, словом.
    assert бренды[(None, "Нечто выдуманное")]["sources"] == ["карточка запроса"]
    м = код(база, "MW21215M")
    assert м["brand"] == {"key": "kelton", "name": "Kelton GmbH", "src": "маска кода", "disputed": False}


def test_спрос_правдоподобными_единицами(база):
    r = код(база, "QX-1001")
    d = r["demand"]
    assert (d["deals"], d["rows"], d["qty"], d["unit"], d["qty_hidden"]) == (2, 2, 10, "шт", 0)
    assert d["customers"] is None and d["last_month"]
    assert r["brand"]["src"] == "спецификация" and r["brand"]["key"] == "kelton"
    мусор = код(база, "AB-6205")["demand"]
    assert (мусор["qty"], мусор["qty_hidden"], мусор["rows"]) == (None, 1, 1)


def test_аналоги_машины_узлы_и_кому_писать(база):
    r = код(база, "KL-7")
    assert [(a["code"], a["written"], a["kind"], a["brand"]) for a in r["analogs"]] == [
        ("an4004", "AN-4004", "аналог", {"key": None, "name": "Выдуманный литейщик"}),
        ("skf7", "SKF-7", "замена", {"key": "skf", "name": "SKF"}),
    ]
    assert r["machines"] == [{"id": "vm400", "name": "ВМ-400", "kind": "турбина", "segment": "gtu",
                              "brand": {"key": "kelton", "name": "Kelton GmbH"}}]
    assert r["units"] == [{"id": "hot.liner", "name": "Жаровая труба", "parent": "Горячая часть", "crit": "A"}]
    # Обратная связь: код аналога ведёт на деталь, к которой он аналог.
    a = код(база, "AN-4004")
    assert [(x["code"], x["written"], x["kind"]) for x in a["analog_of"]] == [("kl7", "KL-7", "аналог")]
    # Бренд кода, известного только каталогу аналогов, — изготовитель аналога;
    # в реестре его нет — словом, и спор не судится.
    assert a["brand"] == {"key": None, "name": "Выдуманный литейщик", "src": "каталог аналогов",
                          "disputed": None}
    # Кому ещё писать по ZC-2002: по бренду Kelton давал цену Альфа (PR-3003,
    # KL-7), а Бета уже дала цену на этот код; несведённая компания не идёт.
    z = код(база, "ZC-2002")
    assert [(w["company"]["name"], w["codes"]) for w in z["write_to"]] == [("Альфа-Подшипник", 2)]


def test_отвергнутый_код_карточки_не_получает_каталог_защищает(база):
    assert код(база, "SS316") == {"key": "ss316", "rejected": True}
    assert код(база, "DIN 912") == {"key": "din912", "rejected": True}
    # Номер курируемого каталога — код всегда, даже похожий на стандарт.
    assert код(база, "DIN 933")["catalog"] is True


def test_нет_нигде_и_пустой_ввод(база):
    assert код(база, "QX-1003") is None      # только помеченная строка спроса
    assert код(база, "NOPE-000") is None
    for q in ("", " ", "x", "я" * 121):
        assert код(база, q) is None, q


def test_имена_только_человеческие_и_без_номеров_справочников(база):
    for q in ("KL-7", "PR-3003", "ZC-2002", "QX-1001", "AB-6205"):
        r = код(база, q)
        текст = json.dumps(r, ensure_ascii=False)
        for номер in НОМЕРА_СПРАВОЧНИКОВ + ("91101", "91201", "91301", "91401"):
            assert номер not in текст, (q, номер)
        assert "alphabearings" not in текст and "R4" not in текст and "D1" not in текст, q


# ── карточка бренда ──────────────────────────────────────────────────────────

def test_бренд(база):
    r = бренд(база, "kelton")
    assert (r["name"], r["country"], r["owner"], r["registry"]) == ("Kelton GmbH", "Нигдения", "Выдуманный холдинг", True)
    assert r["spellings"] == ["Келтон", "Kelton"]
    # Спрос дословными написаниями: D1 (Kelton), D2 (Келтон), D8 (Kelton).
    assert (r["demand"]["rows"], r["demand"]["deals"], r["demand"]["codes"]) == (3, 3, 2)
    assert r["demand"]["registry_rows"] == 3
    assert [(x["code"], x["deals"]) for x in r["codes_demand"]] == [("qx1001", 2), ("kl7", 1)]
    # КП: словом (R1, R2, R4, R6, R7, R8), карточкой (R6 ещё раз) и каталогом (R5).
    assert r["offers"]["rows"] == 7
    assert [(x["code"], x["rows"], x["suppliers"]) for x in r["codes_offers"]] == [
        ("kl7", 3, 3), ("pr3003", 2, 1), ("qx1001", 1, 1), ("zc2002", 1, 1)]
    assert r["catalog"]["parts"] == 3
    assert {x["code"] for x in r["catalog"]["list"]} == {"zc2002", "kl7", "din933"}
    assert [m["name"] for m in r["machines"]] == ["ВМ-400"] and r["machines"][0]["parts"] == 2
    # Поставщики — сведённые, по имени; слитая Гамма пришла Бетой.
    assert [(s["company"]["name"], s["codes"], s["rows"]) for s in r["suppliers"]] == [
        ("Бета Уплотнения", 3, 3), ("Альфа-Подшипник", 2, 3)]
    # Несведённая компания (R5) в списке не стоит, но в счёте строк есть.
    assert (r["offers"]["suppliers"], r["offers"]["rows_unresolved"]) == (2, 1)
    # Аналоги к кодам бренда — других брендов; отвергнутый код — без ссылки.
    assert sorted((a["written"], a["alt_written"], a["alt_code"], (a["brand"] or {}).get("name"))
                  for a in r["analogs"]) == [
        ("KL-7", "AN-4004", "an4004", "Выдуманный литейщик"), ("KL-7", "SKF-7", "skf7", "SKF"),
        ("ZC-2002", "SS316", None, None)]
    текст = json.dumps(r, ensure_ascii=False)
    for номер in НОМЕРА_СПРАВОЧНИКОВ + ("91101", "91201", "91301", "91401"):
        assert номер not in текст, номер


def test_бренд_неизвестный_и_пустой(база):
    assert бренд(база, "nope") is None
    assert бренд(база, "") is None


# ── карточка поставщика ──────────────────────────────────────────────────────

def test_поставщик(база):
    r = поставщик(база, "KV-S-000012-2")
    assert (r["id"], r["name"], r["number"], r["merged_from"]) == ("KV-S-000012-2", "Бета Уплотнения",
                                                                   "KV-S-000012-2", None)
    # ИНН — и слитой в неё сущности, и из реквизитов Битрикса.
    assert r["inn"] == ["7700000003", "7700000012"]
    assert (r["country"], r["city"]) == ("Нигдения", "Выдумград")
    assert r["bitrix"] == ["91201", "91401"]
    # Отзывчивость — только числа, посторонних полей факта нет.
    assert r["rfq"] == {"sent": 5, "answered": 3, "quoted": 2, "silent": 1, "no_outcome": 1, "cards": 6}
    assert (r["quotes"]["rows"], r["quotes"]["codes"], r["quotes"]["cards"]) == (4, 4, 4)
    бренды = [(b["brand"]["key"], b["brand"]["name"], b["codes"]) for b in r["brands"]]
    assert бренды == [("kelton", "Kelton GmbH", 3), ("skf", "SKF", 1)]
    коды = {x["code"]: x for x in r["codes"]}
    assert set(коды) == {"qx1001", "zc2002", "kl7", "pr3003"}
    # KL-7 у Беты — количество не сходится с суммой: не знаем.
    assert (коды["kl7"]["qty"], коды["kl7"]["price"], коды["kl7"]["brand"]["key"]) == (None, 95, "kelton")
    assert коды["qx1001"]["qty"] == 10
    текст = json.dumps(r, ensure_ascii=False)
    assert "@" not in текст and "nobody" not in текст


def test_слитый_ведёт_на_того_в_кого_слит_и_имя_без_ключа(база):
    r = поставщик(база, "KV-S-000013-3")
    assert (r["id"], r["merged_from"]) == ("KV-S-000012-2", "KV-S-000013-3")
    a = поставщик(база, "kv-s-000011-1")
    assert (a["name"], a["name_src"], a["number"]) == ("Альфа-Подшипник", "bitrix:title", None)
    assert a["domains"] == ["alpha-bearings.example"]
    assert поставщик(база, "KV-S-000099-9") is None
    assert поставщик(база, "KV-S-99") is None
    assert поставщик(база, "") is None


# ── база без необязательных опор ─────────────────────────────────────────────

def test_без_реестра_вида_имён_и_проверки_правдоподобия(база):
    c = база.cursor()
    _схема(c, ГОЛАЯ, ФАЙЛЫ_ГОЛОЙ)
    c.execute(КОРПУС)
    c.execute(КОРПУС_ПОСТАВЩИКОВ)
    c.execute("drop view sup_name_shown")
    c.execute("drop function lib_pn_plausible(text)")
    r = код(база, "KL-7", ГОЛАЯ)
    # Бренд — словом каталога, ключа нет, спор не судится.
    assert r["brand"] == {"key": None, "name": "Kelton GmbH", "src": "каталог", "disputed": None}
    assert r["registry"] is False and r["offers"]["brand_judged"] is False
    # Аналог по слову поставщика находится и без реестра.
    assert [x["price"] for x in r["offers"]["analog"]] == [90]
    assert r["write_to"] == []
    # Без проверки правдоподобия марка стали идёт кодом, как в поиске.
    assert код(база, "SS316", ГОЛАЯ)["key"] == "ss316"
    assert бренд(база, "kelton", ГОЛАЯ) == {"registry": False}
    # Имя поставщика: вида нет, реестровое имя похоже на ключ — имени нет.
    s = поставщик(база, "KV-S-000011-1", ГОЛАЯ)
    assert (s["name"], s["name_src"]) == (None, None)
    assert поставщик(база, "KV-S-000012-2", ГОЛАЯ)["name"] == "Бета Уплотнения"


def test_усечение_по_бюджету_видно(база):
    c = база.cursor()
    c.execute("set portal_entity.budget_ms = '0'")
    try:
        r = код(база, "ZC-2002")
        b = бренд(база, "kelton")
        s = поставщик(база, "KV-S-000012-2")
    finally:
        c.execute("reset portal_entity.budget_ms")
    assert r["partial"] == ["предложения", "аналоги", "машины", "кому ещё писать"]
    assert r["offers"]["rows"] == 0 and r["write_to"] == []
    assert b["partial"] == ["предложения", "поставщики", "машины", "аналоги"]
    assert s["partial"] == ["бренды", "коды"]


def test_права_только_сервису(база):
    c = база.cursor()
    c.execute(f"set search_path to {ИМЯ}")
    for функция in ("portal_code(text)", "portal_brand(text)", "portal_supplier(text)",
                    "portal_brand_rows(text, int)", "portal_companies(text[])"):
        for роль, можно in (("anon", False), ("authenticated", False), ("service_role", True)):
            c.execute("select has_function_privilege(%s, %s, 'execute')", (роль, f"{ИМЯ}.{функция}"))
            assert c.fetchone()[0] is можно, (функция, роль)


# ── планы: большие таблицы — только по индексам ──────────────────────────────

def test_спрос_и_кп_читаются_по_индексам(база):
    """Счётчики чтений транзакции при запрещённом последовательном чтении.

    Как у поиска (tests/test_portal_search_sql.py): перед вызовами в таблицы
    кладутся три тысячи строк-заполнителей с ключами и написаниями, которых
    карточки не спрашивают. Чтение по индексу их не касается, полный проход —
    касается всех. Строки живут только внутри откатываемой транзакции.

    Исключение одно и названо: путь «карточка запроса» строк КП по бренду
    (portal_brand_rows) читает строки КП целиком — у rfq_brands индекса нет.
    Поэтому lib_prices проверяется на карточке кода без бренда реестра и на
    карточке поставщика, а спрос — везде.
    """
    c = база.cursor()
    c.execute(f"set search_path to {ИМЯ}")

    def счета():
        c.execute("""
            select t.relname, 'seq', pg_stat_get_xact_numscans(t.oid), 0
              from pg_class t join pg_namespace n on n.oid = t.relnamespace
             where n.nspname = %s and t.relname in ('lib_demand', 'lib_prices')
            union all
            select t.relname, ix.relname, pg_stat_get_xact_numscans(ix.oid),
                   pg_stat_get_xact_tuples_returned(ix.oid)
              from pg_class t join pg_namespace n on n.oid = t.relnamespace
              join pg_index i on i.indrelid = t.oid join pg_class ix on ix.oid = i.indexrelid
             where n.nspname = %s and t.relname in ('lib_demand', 'lib_prices')""", (ИМЯ, ИМЯ))
        return {(r[0], r[1]): (int(r[2]), int(r[3])) for r in c.fetchall()}

    def прочитано(вызовы):
        c.execute("begin")
        try:
            c.execute("insert into lib_demand (deal_id, item_name, oem, part_number) "
                      "select 'F', 'Заполнитель выдуманный', 'Заполнитель', 'AA-' || g "
                      "from generate_series(1, 3000) g")
            c.execute("insert into lib_prices (feed, source, part_number, item_name, price, currency, "
                      "rfq_company, oem) select 'разбор КП', 'КП', 'AA-' || g, 'Заполнитель', 1, 'USD', "
                      "'8' || g, 'Заполнитель' from generate_series(1, 3000) g")
            c.execute("set local enable_seqscan = off")
            до = счета()
            for функция, аргумент in вызовы:
                c.execute(f"select {функция}(%s)", (аргумент,))
            после = счета()
        finally:
            c.execute("rollback")
        чтения, записей = {}, {}
        for k, (n, t) in после.items():
            n0, t0 = до.get(k, (0, 0))
            if n - n0:
                чтения[k] = n - n0
                записей[k] = t - t0
        return чтения, записей

    # Карточка кода, чей бренд реестра не назван: ни спрос, ни КП не читаются
    # целиком. Код с брендом — спрос тоже только по индексу.
    чтения, записей = прочитано([("portal_code", "QX-1002"), ("portal_code", "AB-6205x"),
                                 ("portal_code", "KL-7")])
    assert {путь for (t, путь) in чтения if t == "lib_demand"} <= {"lib_demand_pnkey"}, чтения
    assert all(n < 300 for (t, путь), n in записей.items() if t == "lib_demand"), записей
    чтения, записей = прочитано([("portal_code", "QX-1002"), ("portal_code", "AN-4004")])
    assert {путь for (t, путь) in чтения if t == "lib_prices"} <= {"lib_prices_pn_key"}, чтения
    assert all(n < 300 for n in записей.values()), записей
    # Карточка поставщика: КП — по ключу компании портала.
    чтения, записей = прочитано([("portal_supplier", "KV-S-000012-2")])
    assert {путь for (t, путь) in чтения if t == "lib_prices"} <= {"lib_prices_rfqco"}, чтения
    assert all(n < 300 for n in записей.values()), записей
    # Карточка бренда: спрос — по написаниям (lib_demand_oem), не целиком.
    чтения, записей = прочитано([("portal_brand", "kelton")])
    assert {путь for (t, путь) in чтения if t == "lib_demand"} <= {"lib_demand_oem"}, чтения
    assert all(n < 300 for (t, путь), n in записей.items() if t == "lib_demand"), записей
