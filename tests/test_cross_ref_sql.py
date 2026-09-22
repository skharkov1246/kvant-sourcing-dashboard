"""Замер перекрёстной системы «позиция ↔ поставщик» считается на PostgreSQL.

ЗАЧЕМ ОТДЕЛЬНЫЙ ТЕСТ. По этим числам решается, строить ли карточку товара и
карточку поставщика сейчас или сначала добирать данные. Ошибка здесь стоит
месяца работы не в ту сторону, а глазами она не видна: все четыре ловушки ниже
дают правдоподобное число, просто не то.

1. СЧЁТ ПОЗИЦИЙ ПО НАПИСАНИЮ, А НЕ ПО КЛЮЧУ. «6205» и «62-05» — одна деталь.
   По написанию выйдут две позиции с одним предложением каждая вместо одной с
   двумя, то есть ровно обратная картина той, которую меряем: «сравнивать не с
   чем» вместо «есть выбор».
2. `count(*)` ВМЕСТО `count(distinct rfq_company)`. Один поставщик, приславший
   два файла на одну позицию, — это по-прежнему один поставщик. Иначе «выбор»
   покажется там, где выбора нет.
3. БРЕНДЫ ЧЕРЕЗ `unnest` БЕЗ ОТСЕВА ПУСТОГО ЭЛЕМЕНТА. Полностью пустое
   значение безопасно: `string_to_array('', ',')` даёт пустой массив. Опасны
   «FAG,» с запятой в хвосте и значение из одного пробела — они дают
   элемент-пустышку, и у компании без брендов бренд оказывается один. Корпус
   содержит оба случая: без них фильтр нельзя ни проверить, ни обосновать.
4. СРАВНИМОСТЬ ПО ЦЕНЕ БЕЗ ПРОВЕРКИ ВАЛЮТЫ. 50 USD и 48 EUR на одну позицию —
   не сравнение, а два разных числа.

Корпус придуман (CLAUDE.md, правило 18), ответы посчитаны руками и записаны
рядом. Запросы берутся ИЗ СКРИПТА, а не пересказываются здесь: пересказ
проверяет пересказ.
"""
from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМА = "тест_перекрёстной_системы"


def скрипт():
    spec = importlib.util.spec_from_file_location(
        "kvant_cross_ref_coverage", ROOT / "scripts" / "cross_ref_coverage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def функция_ключа() -> str:
    текст = (ROOT / "library" / "supabase" / "schema.sql").read_text(encoding="utf-8")
    m = re.search(r"create or replace function lib_pn_key.*?\$\$;", текст, re.S | re.I)
    assert m, "в schema.sql больше нет функции lib_pn_key"
    return m.group(0)


КОРПУС = """
create table lib_models (id text primary key, name text not null);
create table lib_parts (id text primary key, catalog_no text not null,
                        name text not null, oem text);
create table lib_part_models (part_id text references lib_parts(id),
                              model_id text references lib_models(id),
                              primary key (part_id, model_id));
create table lib_part_alt (part_id text references lib_parts(id),
                           alt_pn text not null, kind text not null,
                           primary key (part_id, alt_pn, kind));
create table lib_suppliers (id bigint generated always as identity primary key,
                            name text not null);
create table lib_part_suppliers (part_id text references lib_parts(id),
                                 supplier_id bigint references lib_suppliers(id),
                                 primary key (part_id, supplier_id));
create table sup_entity (id text primary key, display_name text not null);
create table sup_identifier (sup_id text references sup_entity(id), kind text not null,
                             value text not null, value_norm text not null,
                             status text not null default 'stated',
                             primary key (sup_id, kind, value_norm));
create table lib_prices (
  id bigserial primary key, part_number text, feed text, rfq_company text,
  oem text, rfq_brands text, price numeric, currency text);

insert into lib_models values ('m1', 'машина один'), ('m2', 'машина два');
insert into lib_parts values ('6205', '6205', 'подшипник', 'SKF'),
                             ('sealkit12', 'SEAL-KIT-12', 'комплект уплотнений', null);
-- У детали ПО ДВЕ связи каждого вида намеренно: подшипник стоит в двух машинах,
-- заменяется двумя номерами и делается двумя заводами. Это не редкость, а норма,
-- и на ней ломается count(*) вместо count(distinct): позиция посчиталась бы
-- дважды, а доля «с аналогом» перевалила бы за сто процентов.
insert into lib_part_models values ('6205', 'm1'), ('6205', 'm2');
insert into lib_part_alt values ('6205', '6205-2RS', 'аналог'),
                                ('6205', '180205', 'номер изготовителя');
insert into lib_suppliers (name) values ('завод один'), ('завод два');
insert into lib_part_suppliers values ('6205', 1), ('6205', 2);
insert into sup_entity values ('KV-S-000001-1', 'компания сто один'),
                             ('KV-S-000002-2', 'компания сто два');
-- Отменённое слияние остаётся строкой со status='rejected' — так оно обратимо.
-- Считать его связью значит объявить сведённой компанию, которую человек от
-- этой записи отцепил: карточка откроется на чужие данные.
insert into sup_identifier (sup_id, kind, value, value_norm, status)
  values ('KV-S-000001-1', 'bitrix', '101', '101', 'stated'),
         ('KV-S-000002-2', 'bitrix', '102', '102', 'rejected');

-- ПОЗИЦИЯ «6205»: два написания, три строки, ДВЕ разных компании (102 прислала
-- два файла — это по-прежнему одна компания). Все три цены в евро.
insert into lib_prices (part_number, feed, rfq_company, oem, rfq_brands, price, currency) values
  ('6205',        'разбор КП', '101', 'SKF', 'SKF', 100, 'EUR'),
  ('62-05',       'разбор КП', '102', null,  null,  120, 'EUR'),
  ('6205',        'разбор КП', '102', 'SKF', null,  118, 'EUR'),
-- ПОЗИЦИЯ «sealkit12»: одна компания, две цены в РАЗНЫХ валютах — не сравнение.
  ('SEAL-KIT-12', 'разбор КП', '101', null,  'PARKER', 50, 'USD'),
  ('sealkit12',   'разбор КП', '101', null,  null,     48, 'EUR'),
-- ПОЗИЦИЯ «gasket9»: в каталоге её нет, цены нет, изготовитель назван.
  ('GASKET-9',    'разбор КП', '103', 'ELRING', null, null, null),
-- Строка без артикула: компания 104 в счёт компаний идёт, в счёт позиций — нет.
  ('',            'разбор КП', '104', null,  null,  77, 'EUR'),
-- Артикул из одних знаков препинания: ключ пустой, позицией не считается.
  ('---',         'разбор КП', '104', null,  null,  78, 'EUR'),
-- ПОЗИЦИЯ «oring5»: поставщик не назван — позиция есть, предложения адресата нет.
  ('O-RING-5',    'разбор КП', null,  null,  null,  5,  'EUR'),
-- Бренд с запятой в хвосте: элемент-пустышка, брендов у компании ОДИН.
  ('BOLT-M8',     'разбор КП', '105', null,  'FAG,',  10, 'EUR'),
-- Бренд из одного пробела: брендов НОЛЬ, поле при этом не пустое.
  ('NUT-M8',      'разбор КП', '106', null,  ' ',     11, 'EUR'),
-- Чужой поток: в замер попасть не должен ни одной строкой.
  ('6205',        'прайс',     '999', 'FAG', 'FAG', 999, 'EUR');
"""


@pytest.fixture()
def база():
    import psycopg2
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'drop schema if exists "{СХЕМА}" cascade')
        cur.execute(f'create schema "{СХЕМА}"')
        cur.execute(f'set search_path to "{СХЕМА}"')
        cur.execute(функция_ключа())
        cur.execute(КОРПУС)
    yield conn
    with conn.cursor() as cur:
        cur.execute(f'drop schema if exists "{СХЕМА}" cascade')
    conn.close()


def выполнить(conn, sql, параметры):
    with conn.cursor() as cur:
        cur.execute(f'set search_path to "{СХЕМА}"')
        cur.execute(sql, параметры)
        return cur.fetchone()


def test_позиции(база):
    м = скрипт()
    (позиций, с_поставщиком, с_выбором, с_изготовителем, с_брендом, сравнимых,
     медиана, p90, максимум) = выполнить(
        база, м.ПОЗИЦИИ, (м.FEED, м.МИН_ПОСТАВЩИКОВ, м.МИН_ПОСТАВЩИКОВ))

    # Шесть ключей: 6205, sealkit12, gasket9, oring5, boltm8, nutm8. Пустой и
    # «---» отброшены, строка потока «прайс» не считается вовсе.
    assert позиций == 6
    # У oring5 поставщик не назван — значит их пять.
    assert с_поставщиком == 5
    # Две разных компании только у 6205. Будь здесь count(*), «выбор» нашёлся бы
    # и у sealkit12 (две строки одной компании) — счёт стал бы 2.
    assert с_выбором == 1
    assert с_изготовителем == 2          # 6205 и gasket9
    # 6205 (SKF), sealkit12 (PARKER), boltm8 («FAG,» — поле заполнено). У nutm8
    # в поле один пробел: это не бренд, и btrim его отсекает.
    assert с_брендом == 3
    # Сравнима только 6205: три цены в одной валюте. У sealkit12 две цены, но
    # USD и EUR — снятие проверки валюты дало бы здесь 2.
    assert сравнимых == 1
    assert (медиана, p90, максимум) == (1, 2, 2)


def test_каталог(база):
    м = скрипт()
    позиций, в_каталоге, с_оем, с_аналогом, с_машиной, с_исп = выполнить(
        база, м.КАТАЛОГ, (м.FEED,))
    assert позиций == 6
    assert в_каталоге == 2               # только 6205 и sealkit12
    assert с_оем == 1                    # изготовитель в каталоге только у 6205
    # По две связи каждого вида у одной детали — позиция всё равно одна.
    assert с_аналогом == 1
    assert с_машиной == 1
    assert с_исп == 1


def test_поставщики(база):
    м = скрипт()
    (компаний, с_несколькими, с_брендами, мед_поз, p90_поз, макс_поз,
     мед_бр, макс_бр) = выполнить(база, м.ПОСТАВЩИКИ, (м.FEED, м.МИН_ПОСТАВЩИКОВ))

    assert компаний == 6                 # 101 … 106
    assert с_несколькими == 1            # только у 101 позиций две
    # Бренды: 101 → SKF и PARKER, 105 → FAG. У 106 в поле пробел — это ноль
    # брендов. Без отсева пустого элемента компаний с брендами стало бы три,
    # а у 105 брендов оказалось бы два.
    assert с_брендами == 2
    assert макс_бр == 2
    assert мед_бр == 0
    # Позиции на компанию: 101→2, 102→1, 103→1, 104→0 (артикулы пустые),
    # 105→1, 106→1.
    assert (мед_поз, p90_поз, макс_поз) == (1, 2, 2)


def test_сведение_с_реестром(база):
    м = скрипт()
    всего, сведено = выполнить(база, м.СВЕДЕНИЕ, (м.FEED,))
    assert всего == 6
    # В реестре найден только ключ 101: у 102 строка отменена (rejected), и она
    # связью не считается. У остальных пяти карточка не откроется.
    assert сведено == 1


def test_рёбра(база):
    м = скрипт()
    (рёбер,) = выполнить(база, м.РЁБРА, (м.FEED,))
    # (6205,101) (6205,102) (sealkit12,101) (gasket9,103) (boltm8,105)
    # (nutm8,106). Пара с пустым ключом и пара без компании — не рёбра.
    assert рёбер == 6


def test_чужой_поток_не_попадает(база):
    """Строка потока «прайс» не должна влиять ни на один счётчик.

    Отдельным тестом, потому что подмена `where feed = %s` на `where true`
    оставила бы все предыдущие проверки зелёными по трём из пяти запросов.
    """
    м = скрипт()
    (позиций, *_) = выполнить(база, м.ПОЗИЦИИ, ("прайс", м.МИН_ПОСТАВЩИКОВ,
                                                м.МИН_ПОСТАВЩИКОВ))
    assert позиций == 1
    (рёбер,) = выполнить(база, м.РЁБРА, ("прайс",))
    assert рёбер == 1
