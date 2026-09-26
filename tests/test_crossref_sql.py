"""Пять запросов публикатора выполняются на настоящем PostgreSQL и дают снимок.

ЗАЧЕМ ОТДЕЛЬНО ОТ test_crossref_snapshot.py. Тот проверяет СБОРКУ: строки базы
на входе, снимок на выходе, база не нужна. Сами запросы он не выполняет вовсе,
и опечатка в имени колонки его не красит.

Цена такой опечатки — молчаливая. Публикация снимка стоит в ночном прогоне
шагом с continue-on-error: разбор к тому моменту уже сделан, и валить его
из-за недоступного Cloudflare незачем. Значит, упавший запрос не уронит ничего,
а страница останется на вчерашнем снимке и будет выглядеть работающей.

Здесь проверяется вся цепочка целиком: запрос → строки → сборка → снимок →
JSON. Ответы посчитаны руками, корпус придуман (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from library import company_names, crossref

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМА = "тест_запросов_перекрёстной_системы"

# Колонки объявлены ровно теми, что читают запросы: лишние поля скрыли бы
# опечатку в имени, а недостающие уронили бы тест не по делу.
КОРПУС = """
create table lib_models (id text primary key, name text not null);
create table lib_parts (id text primary key, catalog_no text not null, name text not null,
                        oem text, category text, target_equipment text, kv_no text);
create table lib_part_models (part_id text references lib_parts(id),
                              model_id text references lib_models(id),
                              primary key (part_id, model_id));
create table lib_part_alt (part_id text references lib_parts(id), alt_pn text not null,
                           kind text not null, alt_maker text, confidence text,
                           primary key (part_id, alt_pn, kind));
create table lib_suppliers (id bigint generated always as identity primary key,
                            name text not null, country text, kind text);
create table lib_part_suppliers (part_id text references lib_parts(id),
                                 supplier_id bigint references lib_suppliers(id),
                                 makes text, verdict text, confidence text,
                                 primary key (part_id, supplier_id));
create table sup_entity (id text primary key, display_name text not null);
create table sup_identifier (sup_id text references sup_entity(id), kind text not null,
                             value text not null, value_norm text not null,
                             status text not null default 'stated',
                             primary key (sup_id, kind, value_norm));
create table lib_demand (
  id bigserial primary key, deal_id text, item_name text not null,
  part_number text, qty numeric, unit text,
  -- Ячейка изготовителя и файл строки: бренд, названный в спецификации
  -- заказчика (СПРОС_БРЕНДЫ_SQL), и сторона файла, по которой его отличают от
  -- бренда нашего же исходящего ТКП.
  oem text, source_file text);
-- Сторона файла читается запасным путём codes_sql.FILES_CTE: колонка side,
-- поле карточки, код и название поля. Здесь стоит колонка — первая ступень.
create table lib_files (file_id text primary key, origin text, field text, side text);
-- ВИД, А НЕ ТАБЛИЦА: вес позиции обязан считаться по спросу без строк,
-- помеченных как текст тендерного документа. Считать пункт договора спросом
-- значит завысить вес тем, чего никто не спрашивал.
create table lib_row_junk (demand_id bigint, revoked_at timestamptz);
create view lib_demand_live as
  select d.* from lib_demand d
   where not exists (select 1 from lib_row_junk j
                      where j.demand_id = d.id and j.revoked_at is null);
create table lib_prices (
  id bigserial primary key, part_number text, item_name text, feed text,
  rfq_company text, rfq_id text, oem text, rfq_brands text, price numeric,
  currency text, qty numeric, qty_unit text, basis text, lead_days int,
  -- Коммерческие условия и пометки источника: карточка показывает их с
  -- 22.09.2026, и «в КП не указано» отличается на экране от «разбор не дошёл».
  make_days int, pay_terms text, pay_advance_pct numeric, total numeric,
  basis_src text, pay_src text, lead_src text, make_src text,
  confidence text, price_date date, created_at timestamptz default now(),
  -- Файл-источник: по нему сборка считает, откуда точный дубль предложения.
  source_url text);

insert into lib_models values ('sgt400', 'SGT-400');
-- Каталог пишет номер иначе, чем котировки: «6-205» против «6205». Ключ один.
insert into lib_parts values ('6205', '6-205', 'Подшипник', 'SKF', 'подшипники',
                             'ротор', 'KV-000753-4');
-- ВТОРАЯ СТУПЕНЬ СЦЕПКИ. У этой детали id задан по своему («узел-7»), а ключ её
-- каталожного номера — «bolt8». По id котировка на BOLT-8 её не найдёт; по ключу
-- каталожного номера найдёт, и однозначно. Таких деталей в живой базе 82 из
-- 13 501, и тридцать артикулов котировок попали именно в этот хвост.
insert into lib_parts values ('узел-7', 'BOLT-8', 'Болт крепления', 'FAG',
                             'крепёж', 'корпус', null);
-- ЛОВУШКА ВТОРОЙ СТУПЕНИ: два каталожных номера дают ОДИН ключ «nutm8». Такая
-- связь неоднозначна, и брать её нельзя — привязали бы котировку к чужой детали.
insert into lib_parts values ('гайка-а', 'NUT-M8',  'Гайка А', null, null, null, null),
                             ('гайка-б', 'NUT.M8',  'Гайка Б', null, null, null, null);
insert into lib_part_models values ('6205', 'sgt400'), ('узел-7', 'sgt400');
insert into lib_part_alt values ('6205', '180205', 'номер изготовителя', 'ГПЗ', 'high');
insert into lib_suppliers (name, country, kind) values ('Завод', 'Швеция', 'OEM');
insert into lib_part_suppliers values ('6205', 1, 'подшипники', null, 'high');
insert into sup_entity values ('KV-S-000001-1', 'Компания');
insert into sup_identifier (sup_id, kind, value, value_norm)
  values ('KV-S-000001-1', 'bitrix', '101', '101');
-- СПРОС. Веса подобраны так, чтобы ПОРЯДОК позиций без выбора был проверяем:
-- nutm8 — две сделки, seal1 — одна сделка две строки, bolt8 — одна сделка одна
-- строка. Будь у всех одинаковый вес, порядок нельзя было бы отличить от
-- случайного, и его снятие прошло бы мимо тестов.
--
-- 6205 спрашивали две сделки; у одной штуки, у другой килограммы — сумму по
-- такой позиции давать нельзя.
insert into lib_demand (deal_id, item_name, part_number, qty, unit) values
  ('D-1', 'Подшипник',  '6205',        10, 'шт'),
  ('D-1', 'Подшипник',  '62-05',        2, 'шт'),
  ('D-2', 'Подшипник',  '6205',         5, 'кг'),
  ('D-3', 'Уплотнение', 'SEAL-1',       7, 'шт'),
  ('D-3', 'Уплотнение', 'seal1',        3, 'шт'),
  ('D-4', 'Болт',       'BOLT-8',      50, 'шт'),
  ('D-5', 'Гайка',      'NUT-M8',     100, 'шт'),
  ('D-6', 'Гайка',      'NUT-M8',     200, 'шт'),
  -- Склейка ячеек количества (до 24.09.2026): в сумму спроса не идёт.
  ('D-6', 'Гайка',      'NUT-M8', 3163518182.316, 'шт'),
  -- МАРКА СТАЛИ ВМЕСТО КОДА. Строки спроса, где «SS316» стоит номером, — это
  -- разные позиции из одной стали; карточкой они стать не должны.
  ('D-7', 'Труба (19mm) SS316', 'SS316',  8, 'шт'),
  ('D-8', 'Фланец SS316',       'SS 316', 2, 'шт'),
  -- Текст тендерного документа под артикулом 6205: в вес позиции попасть не
  -- должен. Без этой строки чтение таблицы вместо вида выглядело бы верным.
  ('D-9', 'Пункт 5.2 Условия оплаты', '6205', 999, 'шт');
insert into lib_row_junk (demand_id, revoked_at)
  select id, null from lib_demand where item_name like 'Пункт 5.2%';

-- БРЕНД В СТРОКЕ СПЕЦИФИКАЦИИ. D-1 — спецификация заказчика (SKF у обоих
-- написаний 6205), D-2 — наш исходящий ТКП с аналогом: в бренд позиции он не
-- идёт, только в счёт «не со стороны заказчика». D-3 — заказчик назвал Parker.
-- Строка тендерного текста (D-9) бренда не даёт: вид её не пускает.
insert into lib_files values ('ф-заказчик', 'поле сделки', null, 'заказчик'),
                             ('ф-наш-ткп',  'поле сделки', null, 'мы');
update lib_demand set oem = 'SKF',               source_file = 'ф-заказчик' where deal_id = 'D-1';
update lib_demand set oem = 'Выдуманный Аналог', source_file = 'ф-наш-ткп'  where deal_id = 'D-2';
update lib_demand set oem = 'Parker',            source_file = 'ф-заказчик' where deal_id = 'D-3';
update lib_demand set oem = 'FAG',               source_file = 'ф-заказчик' where deal_id = 'D-9';

-- КОММЕРЧЕСКИЕ УСЛОВИЯ ТРЕМЯ РАЗНЫМИ СОСТОЯНИЯМИ. Первая строка: всё прочитано
-- из строки предложения. Вторая: разбор ПРОВЕРИЛ и условий не нашёл («нет» —
-- верифицированное отсутствие, можно спросить поставщика). Третья: разбор до
-- условий не дошёл («не проверено» — наш недочёт). На экране это три разных
-- надписи, и путать их нельзя.
insert into lib_prices (part_number, item_name, feed, rfq_company, rfq_id, oem,
                        rfq_brands, price, currency, qty, qty_unit, basis, lead_days,
                        make_days, pay_terms, pay_advance_pct, total,
                        basis_src, pay_src, lead_src, make_src,
                        confidence, price_date) values
  -- Поставщик назвал китайский бренд, каталог знает SKF, на карточке стоит SKF.
  ('6205',   'Подшипник', 'разбор КП', '101', 'RFQ-1', 'CHINA-BRG', 'SKF',
   100, 'EUR', 4, 'шт', 'EXW', 30,
   45, 'LC, 30 на 70', 30, 400,
   'строка', 'строка', 'строка', 'файл',
   'med', '2026-09-01'),
  ('62-05',  'Подшипник', 'разбор КП', '102', 'RFQ-2', null, null,
   120, 'EUR', null, null, null, null,
   null, null, null, null,
   'нет', 'нет', 'нет', 'нет',
   'low', '2026-09-02'),
  -- Бренд с запятой в хвосте: элемент-пустышка отсеивается сборкой.
  ('SEAL-1', 'Уплотнение', 'разбор КП', '101', 'RFQ-3', null, 'PARKER,',
   50, 'USD', 1, 'шт', null, null,
   null, null, null, null,
   'не проверено', 'не проверено', 'не проверено', 'не проверено',
   'med', null),
  -- Артикул, который найдётся ТОЛЬКО второй ступенью сцепки.
  ('BOLT-8', 'Болт',      'разбор КП', '101', 'RFQ-4', null, null,
   7, 'EUR', 100, 'шт', null, null,
   null, null, null, null, null, null, null, null, 'med', null),
  -- Артикул, у которого второй путь неоднозначен: связи быть не должно.
  -- «Количество × цена ≠ сумма»: 500 × 1 не 999 — количество не читается.
  ('NUT-M8', 'Гайка',     'разбор КП', '101', 'RFQ-5', null, null,
   1, 'EUR', 500, 'шт', null, null,
   null, null, null, 999, null, null, null, null, 'med', null),
  -- Карточка владельца 24.09.2026: марка стали кодом, склеенное количество.
  -- Компания своя (103): уцелей строка — число компаний выросло бы до трёх.
  ('SS316',  '(19mm) SS316 8 3200.0 25600.0', 'разбор КП', '103', 'RFQ-6', null, '1138',
   316, 'USD', 3163518182.316, 'шт', null, null,
   null, null, null, 316, null, null, null, null, 'med', null),
  -- Чужой поток: не должен попасть ни в один запрос.
  ('6205',   'Подшипник', 'прайс',     '999', 'RFQ-9', 'FAG', 'FAG',
   999, 'EUR', 1, 'шт', null, null,
   null, null, null, null, null, null, null, null, 'med', null);

-- ТОЧНЫЙ ДУБЛЬ (ревизия 25.09.2026, n.o_dup): тот же КП карточки RFQ-1 лежит во
-- втором поле КП — у копии свой файл, а компания, цена, валюта, количество и
-- дата те же. Строк базы две, предложение одно.
update lib_prices set source_url = 'вложение-11' where rfq_id = 'RFQ-1';
insert into lib_prices (part_number, item_name, feed, rfq_company, rfq_id, oem,
                        rfq_brands, price, currency, qty, qty_unit, basis, lead_days,
                        make_days, pay_terms, pay_advance_pct, total,
                        basis_src, pay_src, lead_src, make_src,
                        confidence, price_date, source_url)
  select part_number, item_name, feed, rfq_company, rfq_id, oem,
         rfq_brands, price, currency, qty, qty_unit, basis, lead_days,
         make_days, pay_terms, pay_advance_pct, total,
         basis_src, pay_src, lead_src, make_src,
         confidence, price_date, 'вложение-12'
    from lib_prices where rfq_id = 'RFQ-1';
"""


def функция_ключа() -> str:
    """lib_pn_key и lib_pn_plausible из миграции: запросы зовут обе."""
    текст = (ROOT / "library" / "supabase" / "schema.sql").read_text(encoding="utf-8")
    части = []
    for имя in ("lib_pn_key", "lib_pn_plausible"):
        m = re.search(rf"create or replace function {имя}\(.*?\$\$;", текст, re.S | re.I)
        assert m, f"в schema.sql больше нет функции {имя}"
        части.append(m.group(0))
    return "\n".join(части)


@pytest.fixture(scope="module")
def наборы():
    import psycopg2
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    собрано = []
    try:
        with conn.cursor() as cur:
            cur.execute(f'drop schema if exists "{СХЕМА}" cascade')
            cur.execute(f'create schema "{СХЕМА}"')
            cur.execute(f'set search_path to "{СХЕМА}"')
            cur.execute(функция_ключа())
            cur.execute(КОРПУС)
            # Как у публикатора: вида имён в этом корпусе нет — на его месте
            # пустая выборка, имя компании остаётся display_name.
            есть = company_names.вид_имён_есть(cur)
            for sql in (crossref.ПРЕДЛОЖЕНИЯ_SQL, crossref.КАТАЛОГ_SQL,
                        crossref.АНАЛОГИ_SQL, crossref.МАШИНЫ_SQL,
                        crossref.ИЗГОТОВИТЕЛИ_SQL, crossref.СПРОС_SQL):
                cur.execute(company_names.имена_sql(sql, есть), (crossref.FEED,))
                собрано.append(cur.fetchall())
        yield собрано
    finally:
        with conn.cursor() as cur:
            cur.execute(f'drop schema if exists "{СХЕМА}" cascade')
        conn.close()


@pytest.fixture(scope="module")
def снимок(наборы):
    return crossref.собрать(*наборы)


def test_каждый_запрос_вернул_ожидаемое_число_строк(наборы):
    предложения, каталог, аналоги, машины, изготовители, спрос = наборы
    # Шесть строк потока «разбор КП» с непустым артикулом (одна — копия RFQ-1 во
    # втором файле); строка потока «прайс» не считается. Если запрос перестанет
    # фильтровать по feed, здесь будет 7.
    assert len(предложения) == 6
    # Файл-источник — последней колонкой: по нему сборка считает происхождение дублей.
    assert sorted(r[26] for r in предложения if r[26]) == ["вложение-11", "вложение-12"]
    # Каталог отдаёт ДВЕ строки: 6205 нашлась по id, «BOLT-8» — второй ступенью
    # сцепки. «NUT-M8» не нашлась: два каталожных номера дают один ключ.
    assert len(каталог) == 2
    assert len(аналоги) == 1
    assert len(машины) == 2                    # 6205 и найденный второй ступенью узел
    assert len(изготовители) == 1


def test_снимок_собирается_из_живых_строк(снимок):
    t = dict(снимок["totals"])
    # Бренд позиции без реестра: каталог даёт SKF и FAG словом ведомости,
    # карточка — PARKER, а nutm8 не назвал никто (подробно — ниже, с реестром).
    б = t.pop("brand")
    assert (б["codes"], б["determined"], б["none"]) == (4, 3, 1)
    assert б["by"] == {"каталог": 2, "строка": 0, "карточка": 1, "маска": 0}
    # 6205: у 101 цена за штуку, 102 единицы не назвал — по правилу 26.09.2026
    # (crossref.сравнима, единица как в /brands) это разные группы, и позиция
    # больше не «сравнима»; потеря видна числом cmp_lost_unit_empty.
    assert t.pop("rule") == {"cmp_before": 1, "cmp_after": 0, "cmp_lost_units": 0,
                             "cmp_lost_unit_empty": 1, "cmp_lost_one_ent": 0, "cmp_gained": 0,
                             "choice_bitrix": 1, "choice_ent": 1, "choice_lost_one_ent": 0}
    assert t == {"positions": 4, "with_choice": 1, "comparable": 0, "in_catalog": 2,
                 "companies": 2, "companies_resolved": 1,
                 # Строк шесть, предложений пять: копия RFQ-1 схлопнута.
                 "offers": 5, "offer_rows": 6,
                 # Позиций вне спроса нет: в корпусе спрос заведён на все
                 # четыре. На живой базе так же — артикулы котировок берутся из
                 # спецификаций, и это тавтология, видимая числом.
                 "no_demand": 0}
    # Одна карточка, два файла — копия КП во втором поле карточки.
    assert снимок["dups"] == {"groups": 1, "rows": 1, "cards": 0, "one_card": 1,
                              "one_file": 0, "no_file": 0, "differ": 0}


def test_позиция_несёт_всё_обещанное_карточкой(снимок):
    поз = {p["k"]: p for p in снимок["positions"]}
    assert set(поз) == {"6205", "seal1", "bolt8", "nutm8"}
    p = поз["6205"]
    # Номер и наименование — каталожные, а не из котировки.
    assert p["n"] == "6-205"
    assert p["name"] == "Подшипник"
    assert p["kv"] == "KV-000753-4"
    assert p["unit"] == "ротор"
    # Два написания артикула свелись к одной позиции с двумя компаниями.
    assert p["co"] == 2 and p["offers"] == 2
    # Три утверждения об изготовителе — три разных значения.
    assert p["oem_cat"] == "SKF"
    assert p["oem_file"] == ["CHINA-BRG"]
    assert p["brands"] == ["SKF"]
    assert [(a["pn"], a["kind"], a["maker"]) for a in p["alts"]] == [
        ("180205", "номер изготовителя", "ГПЗ")]
    assert p["models"] == ["SGT-400"]
    assert [(m["name"], m["role"], m["country"]) for m in p["makers"]] == [
        ("Завод", "OEM", "Швеция")]
    # Позиция вне каталога: разделы пусты, и это отсутствие связи.
    q = поз["seal1"]
    assert q["cat"] is False
    assert q["alts"] == [] and q["models"] == [] and q["makers"] == []
    assert q["brands"] == ["PARKER"]        # «PARKER,» — один бренд, не два


def test_вес_позиции_считается_спросом_а_не_нашими_запросами(снимок):
    """Значимость позиции — сколько раз спрашивали НАС, а не сколько мы рынок.

    Восемь процентов позиций имеют выбор из двух поставщиков; по остальным
    придётся рассылать запросы вторым поставщикам, и порядок должны задавать
    деньги. Число карточек запроса для этого не годится: мы спрашивали по одному
    разу и о позиции из тридцати спецификаций, и о позиции из одной.

    Количество суммируется ТОЛЬКО в одной единице: «10 штук» и «5 килограммов» в
    сумме не дают «15» ничего — то же правило, по которому цены не складываются
    через валюты.
    """
    поз = {p["k"]: p for p in снимок["positions"]}

    # 6205: две сделки, три строки спроса, ДВЕ единицы — суммы нет.
    d = поз["6205"]["demand"]
    assert d["deals"] == 2
    assert d["rows"] == 3
    assert d["units"] == 2
    assert d["qty"] is None, "сумма выдана при двух разных единицах"

    # seal1: одна сделка, две строки, одна единица — сумма законна.
    d = поз["seal1"]["demand"]
    assert d["deals"] == 1 and d["rows"] == 2 and d["units"] == 1
    assert d["qty"] == 10.0
    # И ЕДИНИЦА НАЗВАНА. Голое «10» читается как штуки, метры и килограммы
    # одинаково; страница обязана получить имя единицы вместе с числом.
    assert d["unit_name"] == "шт"

    # 6205: единиц две, поэтому ни суммы, ни имени единицы быть не должно —
    # «шт» рядом с числом, в котором есть килограммы, было бы прямой ложью.
    assert "unit_name" not in поз["6205"]["demand"]

    # bolt8: одна сделка, одна строка — самый малый вес из трёх.
    assert поз["bolt8"]["demand"]["deals"] == 1
    assert поз["bolt8"]["demand"]["rows"] == 1

    # Позиций вне спроса в корпусе нет.
    assert снимок["totals"]["no_demand"] == 0


def test_позиции_без_выбора_идут_по_спросу(снимок):
    """Порядок внутри «без выбора» задаёт спрос, а не алфавит.

    Рассылать запросы вторым поставщикам придётся по порядку, и позиция из двух
    спецификаций важнее позиции из одной. Без этой проверки снятие спроса из
    сортировки прошло бы незаметно: страница выглядела бы так же.
    """
    порядок = [p["k"] for p in снимок["positions"]]
    # 6205 первая: у неё выбор из двух компаний.
    assert порядок[0] == "6205"
    # Дальше по спросу: nutm8 (две сделки), seal1 (одна сделка, две строки),
    # bolt8 (одна сделка, одна строка).
    assert порядок[1:] == ["nutm8", "seal1", "bolt8"]


def test_вторая_ступень_сцепки_берётся_и_только_однозначная(снимок):
    """Деталь с иначе заданным id находится; неоднозначная связь не берётся.

    У 82 деталей из 13 501 id не равен ключу каталожного номера, и по id такая
    деталь из соединения выпадает МОЛЧА — связи нет, и это неотличимо от «детали
    в каталоге нет». Тридцать артикулов котировок попали именно в этот хвост.

    Вторая ловушка ровно противоположная: «NUT-M8» и «NUT.M8» дают один ключ.
    Взять такую связь значило бы привязать котировку к чужой детали, и на
    странице это было бы неотличимо от правды. Пустой раздел честнее неверного.
    """
    поз = {p["k"]: p for p in снимок["positions"]}

    # НАШЛОСЬ второй ступенью: id детали «узел-7», ключ её номера «bolt8».
    b = поз["bolt8"]
    assert b["cat"] is True
    assert b["n"] == "BOLT-8"
    assert b["name"] == "Болт крепления"
    assert b["oem_cat"] == "FAG"
    assert b["models"] == ["SGT-400"]

    # НЕ НАШЛОСЬ, и правильно: второй путь неоднозначен.
    n = поз["nutm8"]
    assert n["cat"] is False
    assert n["models"] == [] and n["alts"] == [] and n["makers"] == []
    assert n["oem_cat"] is None


def test_поставщик_видит_свои_позиции(снимок):
    комп = {c["co"]: c for c in снимок["companies"]}
    assert set(комп) == {"101", "102"}
    assert комп["101"]["ent"] == "KV-S-000001-1"
    # 101 дала предложения по четырём позициям: 6205, seal1, bolt8, nutm8.
    assert комп["101"]["parts"] == 4 and комп["101"]["brands"] == ["PARKER", "SKF"]
    # Строк базы у 101 пять (копия RFQ-1), предложений — четыре.
    assert комп["101"]["rows"] == 4
    # Списка позиций в снимке нет: страница собирает его из positions. Здесь
    # проверяется, что счётчики при этом остались точными.
    assert "list" not in комп["101"]
    # 102 в реестре не найдена — карточка не откроется, и это видно.
    assert комп["102"]["ent"] is None
    assert комп["102"]["parts"] == 1


def test_снимок_уходит_в_json_целиком(снимок):
    """Decimal и date psycopg2 отдаёт объектами, json их не берёт.

    Проверять надо после ЖИВОГО запроса: в сборочном тесте типы придуманы, а
    здесь они настоящие — numeric приходит Decimal, price_date приходит date.
    """
    raw = json.dumps(снимок, ensure_ascii=False)
    assert '"6-205"' in raw
    assert json.loads(raw)["totals"]["positions"] == 4


# ── КОММЕРЧЕСКИЕ УСЛОВИЯ В СНИМКЕ ──────────────────────────────────────────
# Владелец просил читать из каждого КП базис, условия оплаты, срок производства и
# срок поставки, а отсутствие — помечать ВЕРИФИЦИРОВАННЫМ. Разбор это делает с
# 22.09.2026, но до карточки условия не доходили: закупщик видел цену без условий
# оплаты и не мог сравнить два предложения. Здесь проверяется, что доходят.

def test_условия_предложения_доезжают_до_снимка(снимок):
    позиция = {p["k"]: p for p in снимок["positions"]}["6205"]
    # Строка RFQ-1: всё прочитано из строки предложения.
    полная = [o for o in позиция["list"] if o.get("f") == "RFQ-1"]
    assert len(полная) == 1, "предложение RFQ-1 не попало в снимок или не схлопнуто с копией"
    o = полная[0]
    assert o["x"] == 2, "копия RFQ-1 во втором файле не посчитана"
    assert o["s"] == "EXW", "базис"
    assert o["l"] == 30, "срок поставки"
    assert o["k"] == 45, "срок ИЗГОТОВЛЕНИЯ — отдельно от срока поставки"
    assert o["y"] == "LC, 30 на 70", "условия оплаты словами"
    assert o["a"] == 30, "доля аванса"
    assert o["t"] == 400, "сумма строки — ею проверяется «цена × количество»"


def test_пометки_источника_различают_три_состояния(снимок):
    """«Проверено, условия нет» и «разбор не дошёл» — разные вещи.

    Первое можно спросить у поставщика, второе чинить нам. На экране это две
    разные надписи, а в снимке — разные буквы в одной строке «б|о|с|и».
    """
    по_карточке = {o.get("f"): o for p in снимок["positions"] for o in p.get("list", [])}
    # RFQ-1: базис, оплата и срок поставки из строки, срок изготовления из файла.
    assert по_карточке["RFQ-1"]["r"] == "сссф"
    # RFQ-2: разбор проверил и не нашёл ничего — верифицированное отсутствие.
    assert по_карточке["RFQ-2"]["r"] == "нннн"
    # RFQ-3: разбор до условий не дошёл. Ключ не едет вовсе: все четыре «?» —
    # это отсутствие сведений, а не сведения, и рисовать по ним нечего.
    assert "r" not in по_карточке["RFQ-3"], \
        "«ничего не проверено» уехало в снимок как утверждение"


def test_ключи_предложения_короткие_и_все_из_таблицы(снимок):
    """Имена ключей — провод, а не документ: таблица одна, в crossref.py.

    Длинное имя, случайно оставшееся в сборке, съело бы запас снимка (замер:
    имена ключей стоили 2,4 МиБ из 6,9) и молча разошлось бы со страницей.
    """
    разрешённые = set(crossref.ПОЛЯ_ПРЕДЛОЖЕНИЯ.values())
    assert len(разрешённые) == len(crossref.ПОЛЯ_ПРЕДЛОЖЕНИЯ), \
        "две длинных имени сокращены в одну букву — снимок потеряет поле"
    for p in снимок["positions"]:
        for o in p.get("list", []):
            лишние = set(o) - разрешённые
            assert not лишние, f"в предложении ключи вне таблицы: {лишние}"
            assert all(len(k) == 1 for k in o), o


def test_марка_стали_кодом_не_становится(наборы, снимок):
    """«SS316» — марка нержавеющей стали, а не артикул (карточка 24.09.2026).

    Правило судит при чтении (lib_pn_plausible): строка цены с таким номером в
    снимок не едет, ключ не набирает спрос чужих позиций, строки базы целы."""
    предложения, *_, спрос = наборы
    assert all(r[0] != "ss316" for r in предложения)
    assert all(r[0] != "ss316" for r in спрос)
    assert "ss316" not in {p["k"] for p in снимок["positions"]}
    assert "103" not in {c["co"] for c in снимок["companies"]}


def test_количество_которое_не_читается_не_показывается(снимок):
    """Сумма спроса без склеенной ячейки; предложение, где «кол-во × цена ≠
    сумма», — без количества (на экране «Не знаем», а не мусор)."""
    поз = {p["k"]: p for p in снимок["positions"]}
    n = поз["nutm8"]
    assert n["demand"]["qty"] == 300.0
    assert n["demand"]["rows"] == 3            # строка не выброшена — только число
    предложение = n["list"][0]
    assert "q" not in предложение and предложение["t"] == 999.0
    # Сошедшаяся тройка количество сохраняет: 4 × 100 = 400.
    assert any(о.get("q") == 4.0 for о in поз["6205"]["list"])


def test_бренд_карточки_берёт_имя_из_реестра():
    """Номер элемента СП-176 → имя бренда; реестра нет — номера как были."""
    import psycopg2
    схема = СХЕМА + "_бренды"
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'drop schema if exists "{схема}" cascade')
            cur.execute(f'create schema "{схема}"')
            cur.execute(f'set search_path to "{схема}"')
            # Реестра ещё нет: чтение не падает и имён не даёт.
            assert crossref.имена_брендов(cur) == {}
            cur.execute("""
              create table lib_brands (brand_key text primary key, name text not null);
              create table lib_brand_alias (spelling text, source text, sp176_id bigint,
                                            brand_key text, status text);
              create view lib_brand_sp176 as
                select sp176_id, min(brand_key) as brand_key from lib_brand_alias
                 where sp176_id is not null and status in ('разрешено', 'проверено')
                   and brand_key is not null
                 group by sp176_id having count(distinct brand_key) = 1;
              insert into lib_brands values ('skf', 'SKF');
              insert into lib_brand_alias values
                ('SKF Group', 'СП-176', 1138, 'skf', 'разрешено'),
                ('Учебная марка', 'СП-176', 340, null, 'в очереди');
            """)
            имена = crossref.имена_брендов(cur)
    finally:
        with conn.cursor() as cur:
            cur.execute(f'drop schema if exists "{схема}" cascade')
        conn.close()
    # Разрешённый — каноническим именем реестра, неразрешённый — написанием
    # справочника, неизвестный номер остаётся номером.
    assert имена == {"1138": "SKF", "340": "Учебная марка"}
    assert crossref._бренды("1138, 340,3448", имена) == ["3448", "SKF", "Учебная марка"]


# ── БРЕНД ПОЗИЦИИ: ПАРА «БРЕНД + КОД» (П1, П2) ─────────────────────────────
# Бренд, названный в строке спецификации, читается запросом СПРОС_БРЕНДЫ_SQL, а
# узнаётся реестром брендов — настоящей схемой brands_schema.sql. Здесь вся
# цепочка: запросы → реестр (brands.читать_реестр) → сборка → пары.

def _выполнить_схему(cur, файл):
    from tests.test_library_schema_sql import операторы
    for оператор in операторы((ROOT / "library" / "supabase" / файл).read_text(encoding="utf-8")):
        cur.execute(оператор)


РЕЕСТР = """
insert into lib_brands (brand_key, name, rule, run_id) values
  ('skf', 'SKF', 'проверка', 'r-test'), ('fag', 'FAG', 'проверка', 'r-test'),
  ('parker', 'Parker Hannifin', 'проверка', 'r-test');
insert into lib_brand_alias (spelling, spelling_key, source, seen_at, sp176_id, brand_key,
                             status, rule, run_id)
  select s, lib_brand_key(s), src, seen, sp, k, 'разрешено', 'проверка', 'r-test'
    from (values ('SKF', 'dict/oem.json', 'выдумка', null::bigint, 'skf'),
                 ('FAG', 'dict/oem.json', 'выдумка', null, 'fag'),
                 ('Parker', 'dict/oem.json', 'выдумка', null, 'parker'),
                 ('SKF Group', 'СП-176', 'СП-176#1138', 1138, 'skf')) v(s, src, seen, sp, k);
"""


@pytest.fixture(scope="module")
def с_реестром():
    """Тот же корпус плюс реестр брендов (brands_schema.sql) в своей схеме."""
    import psycopg2

    from library import brands
    схема = СХЕМА + "_пары"
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'drop schema if exists "{схема}" cascade')
            cur.execute(f'create schema "{схема}"')
            cur.execute(f'set search_path to "{схема}"')
            cur.execute(функция_ключа())
            cur.execute(КОРПУС)
            # Реестра ещё нет — читатель отвечает None, а не падает.
            нет_таблиц = brands.читать_реестр(cur)
            _выполнить_схему(cur, "brands_schema.sql")
            # Таблицы есть, строк нет — тоже None: пустой реестр оставил бы
            # сборку без единого бренда, а словарь-файл их даёт.
            пустой = brands.читать_реестр(cur)
            cur.execute(РЕЕСТР)
            есть = company_names.вид_имён_есть(cur)
            наборы = []
            for sql in (crossref.ПРЕДЛОЖЕНИЯ_SQL, crossref.КАТАЛОГ_SQL,
                        crossref.АНАЛОГИ_SQL, crossref.МАШИНЫ_SQL,
                        crossref.ИЗГОТОВИТЕЛИ_SQL, crossref.СПРОС_SQL):
                cur.execute(company_names.имена_sql(sql, есть), (crossref.FEED,))
                наборы.append(cur.fetchall())
            имена = crossref.имена_брендов(cur)
            cur.execute(crossref.СПРОС_БРЕНДЫ_SQL, (crossref.FEED,))
            спрос_бренды = cur.fetchall()
            реестр = brands.читать_реестр(cur)
        yield {"наборы": наборы, "имена": имена, "спрос_бренды": спрос_бренды,
               "реестр": реестр, "пустой": пустой, "нет_таблиц": нет_таблиц}
    finally:
        with conn.cursor() as cur:
            cur.execute(f'drop schema if exists "{схема}" cascade')
        conn.close()


def test_бренд_спецификации_читается_со_стороной(с_реестром):
    """Ячейки изготовителя спроса — по ключу кода и со стороной файла.

    Наш исходящий ТКП (сторона «мы») едет со своей стороной, чтобы сборка его не
    взяла; строка тендерного текста (вид lib_demand_live) не едет вовсе; код,
    отвергнутый правилом правдоподобия, — тоже."""
    строки = sorted((к, с, я, int(n)) for к, с, я, n in с_реестром["спрос_бренды"])
    assert строки == [
        ("6205", "наш документ", "Выдуманный Аналог", 1),
        ("6205", "спецификация", "SKF", 2),
        ("seal1", "спецификация", "Parker", 2),
    ]


def test_реестра_нет_или_он_пуст(с_реестром):
    """Сборка тогда узнаёт бренд по словарю-файлу, а не по пустому реестру."""
    assert с_реестром["нет_таблиц"] is None
    assert с_реестром["пустой"] is None
    assert с_реестром["реестр"]["карточка"] == {"1138": "skf"}


def test_пары_бренд_код_из_живых_строк(с_реестром):
    """Бренд у каждой пары — с источником по П2, предложения не теряются."""
    р = crossref.реестр_брендов(с_реестром["реестр"]["словарь"],
                                карточка=с_реестром["реестр"]["карточка"], откуда="реестр базы")
    s = crossref.собрать(*с_реестром["наборы"], имена_брендов=с_реестром["имена"],
                         спрос_бренды=с_реестром["спрос_бренды"], реестр=р)
    поз = {crossref.ид_позиции(p): p for p in s["positions"]}
    assert set(поз) == {"6205~skf", "seal1~parker", "bolt8~fag", "nutm8"}
    # 6205: каталог SKF — выше спецификации и карточки; обе компании в одной паре.
    p = поз["6205~skf"]
    assert (p["bn"], p["bs"], p["co"], p["offers"]) == ("SKF", "каталог", 2, 2)
    # Предложение RFQ-1 назвало SKF маркой карточки, RFQ-2 не назвало ничего.
    assert sorted(o.get("o", "") for o in p["list"]) == ["", "к:skf"]
    assert p["spec"] == ["SKF"]
    # seal1: в каталоге нет; спецификация заказчика (строка) выше карточки.
    q = поз["seal1~parker"]
    assert (q["bn"], q["bs"]) == ("Parker Hannifin", "строка")
    assert поз["bolt8~fag"]["bs"] == "каталог"
    # nutm8: не назвал никто — «Бренд не определён», причина «нет».
    assert поз["nutm8"]["bw"] == "нет" and "bk" not in поз["nutm8"]
    б = s["totals"]["brand"]
    assert б["determined"] == 3 and б["none"] == 1 and б["disputed"] == 0
    assert б["by"] == {"каталог": 2, "строка": 1, "карточка": 0, "маска": 0}
    assert б["offers_coded"] == s["totals"]["offers"] == 5
    assert б["spec_other_side"] == 1
    # Спецификация назвала SKF, каталог тоже: спора нет, счёт «каталог против
    # строки» пуст.
    assert б["cat_vs_row"] == 0
    json.dumps(crossref.разложить(s), ensure_ascii=False)
