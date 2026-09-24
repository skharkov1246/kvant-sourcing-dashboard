"""Запросы снимка брендов выполняются на НАСТОЯЩЕЙ схеме и дают снимок.

ЗАЧЕМ ОТДЕЛЬНО ОТ test_brands_snapshot.py. Тот проверяет сборку на готовых
строках; опечатку в имени колонки, порт ключа бренда, разошедшийся с SQL, и
пустую карту словаря он не видит. Публикация стоит в ночном прогоне, и упавший
запрос не уронил бы ничего, кроме страницы, — она осталась бы на вчерашнем
снимке, выглядя работающей.

Все три файла миграции (schema.sql, schema_junk.sql, suppliers_schema.sql)
применяются в свою схему одноразовой базы, как это делает прогон миграций, —
так запросы проверяются против колонок, которые есть на самом деле.

Корпус придуман (CLAUDE.md, правило 18); ответы посчитаны руками.
Работает при поднятой базе (LIBRARY_SQL_TEST_DSN), как соседние тесты SQL.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from library import brands, codes_sql, company_names
from tests.test_library_schema_sql import операторы

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

ИМЯ = "brands_sql_test"
ФАЙЛЫ = ("schema.sql", "schema_junk.sql", "suppliers_schema.sql")

# Написания изготовителя в каталоге — на РАЗНЫЕ коды, чтобы каждое дало свой
# ключ в запросе брендов: так ключ запроса сверяется с портом в Python.
НАПИСАНИЯ = ["Wärtsilä Oyj", "SКF", "Brändström AB", "Cobalt Pumps", "ООО Ромашка",
             "Grundfos A/S"]

КОРПУС = """
insert into lib_files (file_id, deal_id, origin, field, status) values
 ('fc1','D1','поле сделки','ufCrm_1633502831','разобран'),   -- спецификация: заказчик
 ('fus','D2','поле сделки','ufCrm_1585568303498','разобран'), -- наше предложение
 ('fr1','R1','поле запроса','ufCrm18_1','разобран');
insert into lib_demand (deal_id, item_name, oem, part_number, source_file) values
 ('D1','Подшипник выдуманный','SKF/FAG','AB-6205','fc1'),
 ('D2','Подшипник выдуманный',null,'AB 6205','fus'),
 ('D1','Клапан выдуманный','Келтон','KL-7','fc1'),
 ('D1','Клапан выдуманный','Келтон','KL-8','fc1'),
 ('D2','Втулка выдуманная','Vortex','DD-40','fus'),
 ('D1','Пункт договора','Junkbrand','JN-55','fc1');
insert into lib_row_junk (demand_id, rule, run_id)
  select id, 'proza-тест', 'тест' from lib_demand where part_number = 'JN-55';

insert into lib_units (id, name, crit) values ('hot', 'Горячая часть', 'A');
insert into lib_models (id, name, oem, use_case) values
 ('vm1', 'Выдуманная машина', 'Kelton GmbH', 'насосная станция');
insert into lib_fleet (id, site, model_id) values ('пл1', 'Выдуманная площадка', 'vm1');
insert into lib_parts (id, catalog_no, name, oem, unit_id) values
 ('kl7', 'KL-7', 'Клапан выдуманный', 'Kelton GmbH', 'hot'),
 ('kl9', 'KL-9', 'Седло выдуманное', 'Kelton GmbH', null);
insert into lib_part_models (part_id, model_id) values ('kl7', 'vm1'), ('kl9', 'vm1');
insert into lib_part_alt (part_id, alt_pn, kind, alt_maker) values
 ('kl7', 'KL-7-A', 'номер изготовителя', 'Выдуманный литейщик');
insert into lib_suppliers (name, kind, country) values ('Разведанная компания', 'oem', 'Нигде');
insert into lib_part_suppliers (part_id, supplier_id, source, verdict)
  select 'kl7', id, 'каталог ЗИП', 'есть' from lib_suppliers;

insert into sup_entity (id, kind, display_name, resolution, status) values
 ('KV-S-000011-1','legal','alphabearings','candidate','active'),
 ('KV-S-000012-2','legal','betaseal','resolved','active');
insert into sup_identifier (sup_id, kind, value, value_norm, source, status, run_id) values
 ('KV-S-000011-1','bitrix','1101','1101','bitrix','verified','r1'),
 ('KV-S-000011-1','bitrix','1102','1102','bitrix','verified','r1'),
 ('KV-S-000012-2','bitrix','1201','1201','bitrix','verified','r1');

insert into lib_prices (feed, source, part_number, item_name, price, currency, qty, qty_unit,
                        basis, rfq_id, rfq_company, rfq_brands, oem, source_url) values
 ('разбор КП','КП','AB-6205','Подшипник выдуманный',10,'USD',2,'шт','EXW','R1','1101','501,502','SKF','u1'),
 ('разбор КП','КП','ab 6205','Подшипник выдуманный',12,'usd ',4,'шт','ddp','R2','1102','502','SKF AB','u2'),
 ('разбор КП','КП','AB-6205','Подшипник выдуманный',11,'EUR',1,'pcs',null,'R3','1201',null,'FAG','u3'),
 ('разбор КП','КП','AB-6205','Подшипник выдуманный',0,'EUR',1,'шт',null,'R3','1201',null,null,'u3'),
 ('разбор КП','КП','KL-7','Клапан выдуманный',1500,'RUB',2,'шт',null,'R4','1201',null,'Kelton','u4'),
 ('разбор КП','КП','KL-7','Клапан выдуманный',1400,'RUB',null,null,null,'R5',null,null,null,'u5'),
 ('ТКП КВАНТ (отпускная цена)','КП','AB-6205','Подшипник',99,'USD',1,'шт',null,'R1','1101',null,'SKF','u1');
"""

# Ключи словаря: «Келтон» и «Kelton GmbH» — один бренд kelton.
КАРТА = [("келтон", "kelton"), ("kelton", "kelton"), ("skf", "skf"), ("fag", "fag")]


# Живой поиск и его планы собирает та же фикстура, пока схема стоит.
ПОИСК: dict = {}
ПЛАНЫ: dict = {}


@pytest.fixture(scope="module")
def наборы():
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
        c.execute(f"drop schema if exists {ИМЯ} cascade")
        c.execute(f"create schema {ИМЯ}")
        c.execute(f"set search_path to {ИМЯ}")
        for имя in ФАЙЛЫ:
            for оператор in операторы((ROOT / "library" / "supabase" / имя).read_text(encoding="utf-8")):
                c.execute(оператор)
        c.execute(КОРПУС)
        for i, н in enumerate(НАПИСАНИЯ):
            c.execute("insert into lib_parts (id, catalog_no, name, oem) values (%s, %s, %s, %s)",
                      (f"нап{i}", f"НАП-{i}0{i}", "Деталь выдуманная", н))
        # Тот же порядок, что у публикатора: одна транзакция, настройки сеанса.
        c.execute("begin")
        c.execute(codes_sql.SETTINGS)
        коды = {}
        # Вид имён есть (схема поставщиков применена) — как у публикатора.
        for имя, sql in codes_sql.запросы(КАРТА, имена=company_names.вид_имён_есть(c)).items():
            c.execute(sql)
            колонки = [d[0] for d in c.description]
            коды[имя] = [dict(zip(колонки, r)) for r in c.fetchall()]
        каталог = {}
        for имя, sql in brands.КАТАЛОГ_ЗАПРОСЫ.items():
            c.execute(sql)
            каталог[имя] = c.fetchall()
        c.execute("rollback")
        # Живой поиск по спросу (lib_code_search) и планы его двух путей.
        поиск = {}
        for q in ("ab 6205", "JN-55", "клапан выдуманный"):
            c.execute("select lib_code_search(%s)", (q,))
            поиск[q] = c.fetchone()[0]
        планы = {}
        c.execute("begin")
        c.execute("set local enable_seqscan = off")
        c.execute("explain select 1 from lib_demand d where lib_pn_key(d.part_number) = lib_pn_key('AB-6205')")
        планы["код"] = "\n".join(r[0] for r in c.fetchall())
        c.execute("explain select 1 from lib_demand d "
                  "where d.fts @@ websearch_to_tsquery('russian', 'клапан выдуманный')")
        планы["слова"] = "\n".join(r[0] for r in c.fetchall())
        c.execute("rollback")
        ПОИСК.update(поиск)
        ПЛАНЫ.update(планы)
        yield коды, каталог
    finally:
        c.execute(f"drop schema if exists {ИМЯ} cascade")
        for роль in созданные:
            c.execute(f"drop role if exists {роль}")
        conn.close()


def test_локаль_базы_складывает_кириллицу(наборы):
    коды, _ = наборы
    проверки = [r for r in коды["totals"] if r["section"] == "проверка"]
    assert проверки and all(r["value"] == 1 for r in проверки), \
        "база не в локали UTF-8 — ключи кодов на ней считаются иначе (правило 21а)"


def test_ключ_написания_в_python_тот_же_что_в_sql(наборы):
    коды, _ = наборы
    по_каталогу = {r["brand_key"] for r in коды["brands"] if r.get("codes_catalog")}
    for н in НАПИСАНИЯ:
        assert codes_sql.ключ_написания(н) in по_каталогу, н


def test_карта_словаря_сводит_написания_в_один_бренд(наборы):
    коды, _ = наборы
    ключи = {r["brand_key"]: r for r in коды["brands"]}
    assert "келтон" not in ключи, "написание из словаря осталось своим ключом"
    kelton = ключи["kelton"]
    # KL-7 и KL-8 — спецификация, KL-7 и KL-9 — каталог: три разных кода.
    assert kelton["codes_any"] == 3
    assert kelton["codes_customer"] == 2 and kelton["codes_catalog"] == 2
    # Бренд из нашего документа в облако не идёт: Vortex назван только в «Offer from us».
    vortex = ключи.get("vortex")
    assert vortex is None or not vortex["codes_any"]


def test_помеченная_строка_спроса_не_даёт_бренда(наборы):
    коды, _ = наборы
    assert "junkbrand" not in {r["brand_key"] for r in коды["brands"]}


def test_цены_по_коду_поставщику_валюте_и_единице(наборы):
    коды, _ = наборы
    по = {(r["код_ключ"], r["поставщик_ключ"], r["валюта"], r["единица"]): r for r in коды["match"]}
    a = по[("ab6205", "KV-S-000011-1", "USD", "шт")]
    # Две карточки портала одного поставщика сведены в одну группу.
    assert (float(a["цена_мин"]), float(a["цена_макс"]), float(a["цена_медиана"])) == (10, 12, 11)
    assert a["строк_цены"] == 2 and a["спрошен_нами"] is True
    b = по[("ab6205", "KV-S-000012-2", "EUR", "шт")]
    # Нулевая цена отсеяна и видна числом, а «pcs» сведено к «шт».
    assert b["цена_мин"] == 11 or float(b["цена_мин"]) == 11
    assert b["строк_отсеяно"] == 1
    без = по[("kl7", "(не указан)", "RUB", "(не указана)")]
    assert float(без["цена_мин"]) == 1400
    # Наша отпускная цена в сопоставление не идёт.
    assert not any(float(r["цена_макс"] or 0) == 99 for r in коды["match"])


def test_каталожная_часть_карточки(наборы):
    _, каталог = наборы
    assert [(r[0], r[2], r[5], r[6]) for r in каталог["models"]] == [
        ("Kelton GmbH", "Выдуманная машина", "насосная станция", 1)]
    детали = {r[0]: r for r in каталог["parts"]}
    assert детали["Kelton GmbH"][1:3] == (2, 1)
    узлы = {(r[0], r[2]): r[5] for r in каталог["units"] if r[1] == "Kelton GmbH"}
    assert узлы == {("машина", "hot"): 1, ("машина", None): 1, ("деталь", "hot"): 1, ("деталь", None): 1}
    assert каталог["alts"] == [("Kelton GmbH", "номер изготовителя", "Выдуманный литейщик", 1)]
    assert [(r[1], r[4], r[5], r[6]) for r in каталог["registry"]] == [
        ("Разведанная компания", "каталог ЗИП", 1, 1)]


def test_сквозной_снимок_из_строк_базы(наборы):
    коды, каталог = наборы
    словарь = {"records": [{"oem_key": "kelton", "name": "Kelton",
                            "spellings": [{"spelling": "Келтон"}, {"spelling": "Kelton GmbH"}]}]}
    снимки = brands.собрать(коды, каталог, словарь=словарь,
                            имена_портала={"1201": "Бета-Уплотнение"}, собран="2026-09-24T00:00:00Z")
    сводка = снимки[brands.КЛЮЧ]
    kelton = next(b for b in сводка["brands"] if b["k"] == "kelton")
    assert kelton["dict"] and kelton["name"] == "Kelton"
    assert kelton["models"][0]["use"] == "насосная станция" and kelton["fleet"] == 1
    assert kelton["units"]["machine"]["undefined"] == 1
    assert kelton["alts"]["makers"] == [["Выдуманный литейщик", 1]]
    бета = next(s for s in сводка["suppliers"] if s["k"] == "KV-S-000012-2")
    assert бета["name"] == "Бета-Уплотнение"
    связи = снимки[brands.КЛЮЧ_СВЯЗЕЙ]
    код = next(c for c in связи["codes"] if c[0] == "ab6205")
    assert "ab6205" in снимки[brands.КЛЮЧИ_КОРЗИН[код[2]]]["codes"]
    for ключ, объект in снимки.items():
        json.dumps(объект, ensure_ascii=False)      # Decimal и даты сведены к JSON


def test_живой_поиск_по_коду_и_словам(наборы):
    поиск = ПОИСК
    по_коду = поиск["ab 6205"]
    assert по_коду["key"] == "ab6205"
    # Две живые строки спроса (спецификация и наше предложение), две сделки.
    assert [(r["code"], r["rows"], r["deals"]) for r in по_коду["by_code"]] == [("ab6205", 2, 2)]
    # Строка, помеченная текстом документа, в поиск не попадает.
    assert поиск["JN-55"]["by_code"] == []
    слова = {r["code"]: r for r in поиск["клапан выдуманный"]["by_words"]}
    assert set(слова) == {"kl7", "kl8"} and поиск["клапан выдуманный"]["capped"] is False
    # Номеров сделок в ответе нет — только агрегаты.
    assert all(set(r) == {"code", "rows", "deals", "written", "name"}
               for r in по_коду["by_code"] + list(слова.values()))


def test_поиск_держится_за_индексы(наборы):
    """Индекс берётся, только если условие — ровно выражение индекса. На
    придуманном корпусе планировщику дешевле пройти таблицу, поэтому проход
    запрещён: вопрос не «выберет ли», а «может ли» он взять индекс."""
    assert "lib_demand_pnkey" in ПЛАНЫ["код"], ПЛАНЫ["код"]
    assert "lib_demand_fts" in ПЛАНЫ["слова"], ПЛАНЫ["слова"]
