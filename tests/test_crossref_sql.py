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

from library import crossref

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
create table lib_prices (
  id bigserial primary key, part_number text, item_name text, feed text,
  rfq_company text, rfq_id text, oem text, rfq_brands text, price numeric,
  currency text, qty numeric, qty_unit text, basis text, lead_days int,
  confidence text, price_date date, created_at timestamptz default now());

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
insert into lib_prices (part_number, item_name, feed, rfq_company, rfq_id, oem,
                        rfq_brands, price, currency, qty, qty_unit, basis, lead_days,
                        confidence, price_date) values
  -- Поставщик назвал китайский бренд, каталог знает SKF, на карточке стоит SKF.
  ('6205',   'Подшипник', 'разбор КП', '101', 'RFQ-1', 'CHINA-BRG', 'SKF',
   100, 'EUR', 4, 'шт', 'EXW', 30, 'med', '2026-09-01'),
  ('62-05',  'Подшипник', 'разбор КП', '102', 'RFQ-2', null, null,
   120, 'EUR', null, null, null, null, 'low', '2026-09-02'),
  -- Бренд с запятой в хвосте: элемент-пустышка отсеивается сборкой.
  ('SEAL-1', 'Уплотнение', 'разбор КП', '101', 'RFQ-3', null, 'PARKER,',
   50, 'USD', 1, 'шт', null, null, 'med', null),
  -- Артикул, который найдётся ТОЛЬКО второй ступенью сцепки.
  ('BOLT-8', 'Болт',      'разбор КП', '101', 'RFQ-4', null, null,
   7, 'EUR', 100, 'шт', null, null, 'med', null),
  -- Артикул, у которого второй путь неоднозначен: связи быть не должно.
  ('NUT-M8', 'Гайка',     'разбор КП', '101', 'RFQ-5', null, null,
   1, 'EUR', 500, 'шт', null, null, 'med', null),
  -- Чужой поток: не должен попасть ни в один запрос.
  ('6205',   'Подшипник', 'прайс',     '999', 'RFQ-9', 'FAG', 'FAG',
   999, 'EUR', 1, 'шт', null, null, 'med', null);
"""


def функция_ключа() -> str:
    текст = (ROOT / "library" / "supabase" / "schema_junk.sql").read_text(encoding="utf-8")
    m = re.search(r"create or replace function lib_pn_key.*?\$\$;", текст, re.S | re.I)
    assert m, "в миграции больше нет функции lib_pn_key"
    return m.group(0)


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
            for sql in (crossref.ПРЕДЛОЖЕНИЯ_SQL, crossref.КАТАЛОГ_SQL,
                        crossref.АНАЛОГИ_SQL, crossref.МАШИНЫ_SQL,
                        crossref.ИЗГОТОВИТЕЛИ_SQL):
                cur.execute(sql, (crossref.FEED,))
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
    предложения, каталог, аналоги, машины, изготовители = наборы
    # Пять строк потока «разбор КП» с непустым артикулом; строка потока «прайс»
    # не считается. Если запрос перестанет фильтровать по feed, здесь будет 6.
    assert len(предложения) == 5
    # Каталог отдаёт ДВЕ строки: 6205 нашлась по id, «BOLT-8» — второй ступенью
    # сцепки. «NUT-M8» не нашлась: два каталожных номера дают один ключ.
    assert len(каталог) == 2
    assert len(аналоги) == 1
    assert len(машины) == 2                    # 6205 и найденный второй ступенью узел
    assert len(изготовители) == 1


def test_снимок_собирается_из_живых_строк(снимок):
    t = снимок["totals"]
    assert t == {"positions": 4, "with_choice": 1, "comparable": 1, "in_catalog": 2,
                 "companies": 2, "companies_resolved": 1, "offers": 5}


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
