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
# Схема, где pg_roles подменён пустым видом: так выглядит чистый PostgreSQL без
# ролей платформы, хотя в кластере они есть (правило 20).
БЕЗ_РОЛЕЙ = "portal_entity_sql_noroles"
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
-- Условия КП и откуда они взяты: у R4 базис и оплата — из строки, срок
-- поставки — из общих условий файла, срок изготовления проверенно не указан.
update lib_prices set basis_src = 'строка', pay_terms = '30/70', pay_advance_pct = 30, pay_src = 'строка',
                      lead_days = 30, lead_src = 'файл', make_src = 'нет'
 where rfq_id = 'R4';
-- Реестр исполнителей: кто делает KL-7 и у кого проверено наличие. Почта
-- продавца — чтобы проверить, что контакты в карточку не попадают.
insert into lib_suppliers (name, kind, country, contact_email, contact_phone) values
 ('Выдуманный склад', 'дистрибьютор', 'Нигдения', 'sklad@example.test', '+0 000 000-00-00'),
 ('Выдуманный завод', 'OEM', 'Нигдения', null, null);
insert into lib_part_suppliers (part_id, supplier_id, makes, verdict, in_stock, stock_qty, lead_time, price, currency)
  select 'kl7', id, 'клапаны выдуманные', 'in_stock', 'yes', '12', '5 дней', 88, 'EUR'
    from lib_suppliers where name = 'Выдуманный склад';
insert into lib_part_suppliers (part_id, supplier_id, makes)
  select 'kl7', id, 'делает под заказ' from lib_suppliers where name = 'Выдуманный завод';
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

# Машины и узлы (шаг 3). Изготовители — не Kelton и не SKF: ответы карточек
# кода и бренда выше от этого корпуса не меняются. Направлений три: ТВ-10 —
# ГТУ по семейству (сегмента нет), ГП-7 — ГПУ по сегменту, БШ-3 — горно-
# шахтная, типового дерева нет; «Пустая выдуманная» — машина без всего.
# У ТВ-10 деталей 120 — больше предела списка (100): усечение видно числом.
КОРПУС_МАШИН = """
insert into lib_segments (id, name) values ('gpu', 'ГПУ выдуманные'), ('gsho', 'ГШО выдуманное');
insert into lib_units (id, parent_id, name, name_en, crit, aftermarket, note, source) values
 ('rotor', null, 'Ротор выдуманный', 'Rotor', 'A', 'только у изготовителя', null, 'номенклатура выдуманная'),
 ('rotor.bearing', 'rotor', 'Подшипник опорный выдуманный', 'Journal bearing', 'B', null,
  'примечание к узлу выдуманное', 'номенклатура выдуманная'),
 ('fasteners', null, 'Крепёж выдуманный', 'fasteners', 'C', null, null, 'разметка выдуманная'),
 ('gpu.cpg', null, 'ГПУ: ЦПГ выдуманная', 'Power cylinder', 'A', null, null, 'библиотека ГПУ выдуманная'),
 ('gpu.cpg.piston', 'gpu.cpg', 'Поршень выдуманный', 'Piston', 'A', null, null, 'библиотека ГПУ выдуманная');
insert into lib_models (id, name, oem, family, family_title, legacy, power, efficiency, shafts, use_case, aliases,
                        note, source, segment_id, kind) values
 ('тв10', 'ТВ-10', 'Турбовыдумка / Выдумлит', 'sgt', 'Выдуманное семейство', 'Смерч', '≈10 МВт', '≈30%',
  '2 вала', 'ГПА выдуманные', array['ТВ-10', 'Смерч', 'TV 10', 'ТВ10М'], 'Примечание выдуманное',
  'справочник выдуманный', null, null),
 ('гп7', 'ГП-7', 'Выдуммоторс', 'gpu', 'ГПУ выдуманные', null, '700 кВт', '≈40 %', 'V12', null, array['ГП-7'],
  null, 'библиотека ГПУ выдуманная', 'gpu', 'gas_engine'),
 ('бш3', 'БШ-3', 'Горвыдумка', null, null, null, null, null, null, null, array['БШ-3'], null,
  'реестр выдуманный', 'gsho', 'mining_machine'),
 ('пусто1', 'Пустая выдуманная', null, null, null, null, null, null, null, null, null, null, null, null, null);
insert into lib_parts (id, catalog_no, name, oem, unit_id, kv_no, qty_demand)
  select 'tvd' || lpad(g::text, 3, '0'), 'TVD-' || lpad(g::text, 3, '0'), 'Деталь турбины выдуманная ' || g,
         case when g % 10 = 0 then 'Выдумлит' else 'Турбовыдумка' end,
         case when g <= 30 then 'rotor.bearing' when g <= 40 then 'hot.liner' when g <= 45 then 'fasteners' end,
         case when g in (7, 77, 117) then 'KV-00' || lpad(g::text, 4, '0') || '-1' end, g
    from generate_series(1, 120) g;
insert into lib_part_models (part_id, model_id)
  select 'tvd' || lpad(g::text, 3, '0'), 'тв10' from generate_series(1, 120) g;
insert into lib_parts (id, catalog_no, name, oem, unit_id) values
 ('gpv001', 'GPV-001', 'Поршень выдуманный', 'Выдуммоторс', 'gpu.cpg.piston'),
 ('gpv002', 'GPV-002', 'Свеча выдуманная', 'Выдуммоторс', null),
 ('gpv003', 'GPV-003', 'Фильтр выдуманный', null, null),
 ('bsh001', 'BSH-001', 'Болт выдуманный горный', 'Горвыдумка', 'fasteners'),
 ('bsh002', 'BSH-002', 'Коронка выдуманная', 'Горвыдумка', null);
insert into lib_part_models (part_id, model_id) values
 ('gpv001', 'гп7'), ('gpv002', 'гп7'), ('gpv003', 'гп7'), ('bsh001', 'бш3'), ('bsh002', 'бш3');
insert into lib_fleet (id, site, owner, model_id, model_raw, units, year, note, source) values
 ('парк.т1', 'Выдуманная ТЭЦ-1', 'Выдуманная энергетика', 'тв10', 'ТВ-10 (Турбовыдумка)', '2', '2004',
  'Примечание к площадке выдуманное', 'разведка выдуманная'),
 ('парк.т2', 'Выдуманная КС-2', null, 'тв10', 'Смерч', '1', null, null, 'разведка выдуманная'),
 ('парк.г1', 'Выдуманная мини-ТЭЦ', 'Выдуманный завод', null, null, '2 МВт', null, null, 'парк ГПУ выдуманный');
insert into lib_bom (id, machine, model_id, part_id, part_no, qty, name, node, position_no) values
 ('в1', 'ТВ-10', 'тв10', 'tvd001', 'TVD-001', '2', 'Деталь турбины выдуманная 1', 'РОТОР', '1'),
 ('в2', 'ТВ-10', 'тв10', null, 'ZZ-777', '1', 'Нечто по ведомости выдуманное', 'РОТОР', '2'),
 ('в3', 'ТВ-10', 'тв10', null, 'SS316', '4', 'Лист выдуманный', 'КОРПУС', '1');
insert into lib_symptoms (id, name, unit_id, measure, defect, confirm, basis, confidence, source) values
 ('признак.т1', 'Рост вибрации выдуманный', 'rotor.bearing', 'виброскорость на опоре', 'износ вкладыша',
  'осмотр вкладыша', 'общая практика', 'low', 'справочник признаков (заготовка)'),
 ('признак.т2', 'Разброс термопар выдуманный', 'hot', 'температура за турбиной', 'прогар', 'эндоскопия', null,
  'low', 'справочник признаков (заготовка)'),
 ('признак.г1', 'Стук в цилиндре выдуманный', 'gpu.cpg', 'на слух', 'задир поршня', 'эндоскопия', null, 'low',
  'справочник признаков (заготовка)');
insert into lib_defects (id, name, unit_id, part_number, model, cause, consequence, fix, source, deal_id,
                         source_file) values
 ('дефект.т1', 'Износ вкладыша выдуманный', 'rotor.bearing', null, null, 'грязное масло', 'рост вибрации',
  'замена вкладыша', 'справочник типовых дефектов (заготовка)', 'D-777', 'ФАЙЛ-777'),
 ('дефект.т2', 'Риск отказа: деталь выдуманная 2', 'hot.liner', 'TVD-002', 'ТВ-10', null, 'прогар', 'замена',
  'проработка позиции', null, null),
 ('дефект.т3', 'Риск отказа: чужая машина', 'rotor.bearing', null, 'Другая выдуманная ГТУ', null, 'что-то',
  null, 'проработка позиции', null, null),
 ('дефект.т4', 'Ресурс выдуманный', null, null, 'семейство Смерч', null, 'только замена', null,
  'разведка выдуманная', null, null),
 ('дефект.г1', 'Задир поршня выдуманный', 'gpu.cpg.piston', 'SS316', null, null, 'стук', 'замена поршня',
  'справочник типовых дефектов (заготовка)', null, null);
insert into lib_procedures (id, kind, name, unit_id, scope, duration, model_family, performer, source) values
 ('ремонт.т1', 'ремонт', 'Замена вкладыша выдуманная', 'rotor.bearing', 'разборка опоры', '2 смены', null, null,
  'справочник ремонтных операций (заготовка)'),
 ('контроль.т1', 'контроль', 'Эндоскопия выдуманная', 'hot', 'осмотр горячего тракта', null, null, null,
  'справочник методов (заготовка)'),
 ('ремонт.а1', 'ремонт', 'Ремонтный центр выдуманный', null, 'ремонт горячего тракта', null, 'ansaldo',
  'Выдуманный ремонтный центр', 'разведка выдуманная'),
 ('ремонт.а2', 'ремонт', 'Ремонт ротора выдуманный', 'rotor', null, null, 'ansaldo', null, 'разведка выдуманная'),
 ('модерн.с1', 'модернизация', 'Модернизация выдуманная', null, null, '3 недели', 'sgt', null,
  'разведка выдуманная');
insert into lib_symptom_defects (symptom_id, defect_id, source) values ('признак.т1', 'дефект.т1', 'тест');
insert into lib_symptom_ops (symptom_id, procedure_id, source) values ('признак.т2', 'контроль.т1', 'тест');
insert into lib_defect_ops (defect_id, procedure_id, source) values ('дефект.т1', 'ремонт.т1', 'тест');
"""

КОРПУС_МАШИН_РЕЕСТРА = """
insert into lib_brands (brand_key, name, rule, run_id) values ('turbovyd', 'Турбовыдумка', 'тест', 'тест');
insert into lib_brand_alias (spelling, spelling_key, source, seen_at, brand_key, status, n_rows, rule, run_id) values
 ('Турбовыдумка', lib_brand_key('Турбовыдумка'), 'lib_models.oem', 'm', 'turbovyd', 'разрешено', 1, 'тест', 'тест');
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
        c.execute(КОРПУС_МАШИН)
        c.execute(КОРПУС_МАШИН_РЕЕСТРА)
        c.execute("analyze")
        yield conn
    finally:
        c.execute("reset search_path")
        c.execute(f"drop schema if exists {ИМЯ} cascade")
        c.execute(f"drop schema if exists {ГОЛАЯ} cascade")
        c.execute(f"drop schema if exists {БЕЗ_РОЛЕЙ} cascade")
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
    # Количество не читается — и сумма под вопросом: её нет, есть признак.
    assert (вторая["qty"], вторая["qty_hidden"], вторая["total"], вторая["total_hidden"]) == (None, True, None, True)
    assert первая["total_hidden"] is False
    assert первая["company"] == {"id": "KV-S-000011-1", "name": "Альфа-Подшипник", "src": "bitrix:title",
                                 "number": None}
    assert первая["brand"] == {"key": "kelton", "name": "Kelton GmbH"}
    # Бренд строки назван и совпал с позицией — оригинал подтверждён.
    assert первая["unconfirmed"] is None and o["unconfirmed_n"] == 0
    # Условия КП и их источник — как на /nomenclature: «в КП не указано» и
    # «разбор не дошёл» различимы.
    assert (первая["pay_terms"], первая["pay_advance_pct"], первая["pay_src"]) == ("30/70", 30, "строка")
    assert (первая["lead_days"], первая["lead_src"], первая["make_days"], первая["make_src"]) == (30, "файл", None, "нет")
    assert (первая["basis_src"], вторая["basis_src"]) == ("строка", None)
    # Аналог по слову поставщика; компания не сведена с реестром — имени нет,
    # номер её карточки Битрикса — только полем для ссылки.
    [аналог] = o["analog"]
    assert (аналог["price"], аналог["why"], аналог["company"]) == (90, "поставщик пишет «аналог»", None)
    assert (аналог["bx"], аналог["unresolved"], первая["bx"], первая["unresolved"]) == ("91301", True, None, False)
    # Поставщиков — по компаниям: Альфа, Бета и несведённая 91301.
    assert (o["suppliers"], o["brand_judged"], o["brand_disputed"]) == (3, True, False)


def test_код_ориентир_цены_по_валюте(база):
    o = код(база, "KL-7")["offers"]
    цены = {(x["group"], x["currency"]): x for x in o["prices"]}
    assert set(цены) == {("original", "EUR"), ("analog", "EUR")}
    ор = цены[("original", "EUR")]
    assert (ор["rows"], ор["companies"], ор["min"], ор["max"]) == (2, 2, 95, 100)
    assert (ор["last"]["price"], ор["last"]["month"], ор["last"]["company"]["name"]) == (100, "2026-03", "Альфа-Подшипник")
    ан = цены[("analog", "EUR")]
    assert (ан["rows"], ан["companies"], ан["last"]["company"], ан["last"]["bx"]) == (1, 1, None, "91301")


def test_кто_делает_деталь_без_контактов(база):
    r = код(база, "KL-7")
    assert r["makers_n"] == 2
    склад, завод = r["makers"]
    assert (склад["name"], склад["role"], склад["verdict"], склад["in_stock"], склад["price"], склад["currency"]) == (
        "Выдуманный склад", "дистрибьютор", "in_stock", "yes", 88, "EUR")
    assert (завод["name"], завод["verdict"], завод["price"], завод["currency"]) == ("Выдуманный завод", None, None, None)
    текст = json.dumps(r, ensure_ascii=False)
    assert "@" not in текст and "000-00-00" not in текст


def test_спор_только_между_кп_не_решается_большинством(база):
    r = код(база, "PR-3003")
    # Позицию называют только КП: Келтон + Kelton (один бренд) против SKF.
    assert r["brand"] == {"key": "kelton", "name": "Kelton GmbH", "src": "КП", "disputed": True}
    o = r["offers"]
    # Голосованием поставщиков оригинал не выбирается: строки по бренду не
    # делятся, и SKF не объявлен аналогом только потому, что его назвал один.
    assert (o["brand_judged"], o["brand_disputed"]) == (False, True)
    # Новые первыми, строка без даты — последней.
    assert [x["price"] for x in o["original"]] == [10, 12, 11] and o["analog"] == []
    assert all(x["unconfirmed"] is None for x in o["original"])
    assert o["original"][1]["brand"] == {"key": "skf", "name": "SKF"}
    # Подбирать «кому писать» по неустановленному бренду нельзя.
    assert r["write_to"] == []
    # Количество-мусор скрыто, строка осталась; сумма при нём — тоже.
    вторая = o["original"][2]
    assert (вторая["qty"], вторая["qty_hidden"], вторая["month"], вторая["month_src"]) == (None, True, None, "нет")
    assert (вторая["total"], вторая["total_hidden"]) == (None, True)


def test_аналог_по_бренду_строки_когда_бренд_позиции_установлен(база):
    """Каталог называет Kelton — строка КП с SKF у того же кода уходит в аналоги."""
    c = база.cursor()
    c.execute(f"set search_path to {ИМЯ}")
    c.execute("begin")
    try:
        c.execute("insert into lib_prices (feed, source, part_number, item_name, price, currency, rfq_id, "
                  "rfq_company, oem, price_date) values ('разбор КП','КП','KL-7','Клапан выдуманный',60,'EUR',"
                  "'R20','91101','SKF, FAG','2026-07-01'), ('разбор КП','КП','KL-7','Клапан выдуманный',61,'EUR',"
                  "'R21','91101','Kelton, SKF','2026-07-02'), ('разбор КП','КП','KL-7','Клапан выдуманный',62,"
                  "'EUR','R22','91101','China','2026-07-03'), ('разбор КП','КП','KL-7','Клапан выдуманный',63,"
                  "'EUR','R23','91101',null,'2026-07-04')")
        c.execute("select portal_code('KL-7')")
        o = c.fetchone()[0]["offers"]
    finally:
        c.execute("rollback")
    аналоги = {x["price"]: x for x in o["analog"]}
    # «SKF, FAG» у позиции Kelton: FAG в реестре нет, SKF — чужой бренд.
    assert аналоги[60]["why"] == "бренд строки — SKF, у позиции — Kelton GmbH"
    оригинал = {x["price"]: x for x in o["original"]}
    # «Kelton, SKF» у позиции Kelton — показывается Kelton, а не алфавитно первый.
    assert оригинал[61]["brand"] == {"key": "kelton", "name": "Kelton GmbH"} and оригинал[61]["unconfirmed"] is None
    # Слово не из реестра и пустой бренд — в оригинале, но с пометкой.
    assert оригинал[62]["unconfirmed"] == "в КП «China» — такого бренда в реестре нет"
    assert оригинал[63]["unconfirmed"] == "бренд в КП не назван"
    assert o["unconfirmed_n"] == 2


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
                              "segment_name": "ГТУ выдуманные", "brand": {"key": "kelton", "name": "Kelton GmbH"}}]
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


def _значения(v, ключ=None):
    """Все строковые значения ответа вместе с именем поля, где они лежат."""
    if isinstance(v, dict):
        for k, x in v.items():
            yield from _значения(x, k)
    elif isinstance(v, list):
        for x in v:
            yield from _значения(x, ключ)
    elif isinstance(v, str):
        yield ключ, v


def test_имена_только_человеческие_и_без_номеров_справочников(база):
    for q in ("KL-7", "PR-3003", "ZC-2002", "QX-1001", "AB-6205"):
        r = код(база, q)
        for поле, значение in _значения(r):
            for номер in НОМЕРА_СПРАВОЧНИКОВ:
                assert номер not in значение, (q, поле, значение)
            # Номер карточки компании портала — только полем для ссылки в
            # Битрикс (компании нет в справочнике), и нигде в тексте.
            for номер in ("91101", "91201", "91301", "91401"):
                assert номер not in значение or (поле == "bx" and значение == номер), (q, поле, значение)
        текст = json.dumps(r, ensure_ascii=False)
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
    # КП: словом (R1, R2, R4, R6, R7, R8), карточкой (R6 ещё раз) и каталогом
    # (R5). R5 — «Клапан, аналог»: ответ аналогом на спрос по бренду, не цена
    # по бренду — только числом.
    assert (r["offers"]["rows"], r["offers"]["analog_rows"]) == (6, 1)
    assert [(x["code"], x["rows"], x["suppliers"]) for x in r["codes_offers"]] == [
        ("kl7", 2, 2), ("pr3003", 2, 1), ("qx1001", 1, 1), ("zc2002", 1, 1)]
    assert r["catalog"]["parts"] == 3
    assert {x["code"] for x in r["catalog"]["list"]} == {"zc2002", "kl7", "din933"}
    assert [m["name"] for m in r["machines"]] == ["ВМ-400"] and r["machines"][0]["parts"] == 2
    assert r["machines"][0]["segment_name"] == "ГТУ выдуманные"
    # Поставщики — сведённые, по имени; слитая Гамма пришла Бетой.
    assert [(s["company"]["name"], s["codes"], s["rows"]) for s in r["suppliers"]] == [
        ("Бета Уплотнения", 3, 3), ("Альфа-Подшипник", 2, 3)]
    # Несведённая компания дала только аналог (R5): в строках оригинала её нет.
    assert (r["offers"]["suppliers"], r["offers"]["rows_unresolved"]) == (2, 0)
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
    бренды = [(b["brand"]["key"], b["brand"]["name"], b["named_codes"], b["asked_codes"]) for b in r["brands"]]
    assert бренды == [("kelton", "Kelton GmbH", 3, 0), ("skf", "SKF", 1, 0)]
    коды = {x["code"]: x for x in r["codes"]}
    assert set(коды) == {"qx1001", "zc2002", "kl7", "pr3003"}
    # KL-7 у Беты — количество не сходится с суммой: не знаем.
    assert (коды["kl7"]["qty"], коды["kl7"]["price"], коды["kl7"]["brand"]["key"]) == (None, 95, "kelton")
    assert коды["qx1001"]["qty"] == 10
    # Бренд назвал сам поставщик; KL-7 спрашивали Kelton (карточка запроса),
    # ZC-2002 — Kelton по каталогу: оригинал. PR-3003 спросить не у кого — не судим.
    assert (коды["kl7"]["brand_src"], коды["kl7"]["verdict"]) == ("назвал поставщик", "оригинал")
    assert (коды["zc2002"]["verdict"], коды["pr3003"]["verdict"], коды["qx1001"]["verdict"]) == ("оригинал", None, None)
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


# ── карточки машины и узла (шаг 3) ───────────────────────────────────────────

def машина(conn, i, схема: str = ИМЯ):
    return вызвать(conn, "portal_model", i, схема)


def узел(conn, i, схема: str = ИМЯ):
    return вызвать(conn, "portal_unit", i, схема)


def test_каждая_машина_и_каждый_узел_открываются(база):
    """Мерило шага 3 в малом: у каждой машины lib_models и каждого узла
    lib_units есть карточка (на живой базе — 132 и 126)."""
    c = база.cursor()
    c.execute(f"set search_path to {ИМЯ}")
    c.execute("select id from lib_models order by id")
    машины = [r[0] for r in c.fetchall()]
    c.execute("select id from lib_units order by id")
    узлы = [r[0] for r in c.fetchall()]
    assert (len(машины), len(узлы)) == (5, 7)
    открылось_м = [i for i in машины if (машина(база, i) or {}).get("id") == i]
    открылось_у = [i for i in узлы if (узел(база, i) or {}).get("id") == i]
    assert (открылось_м, открылось_у) == (машины, узлы)


def test_машина_имя_изготовитель_паспорт(база):
    r = машина(база, "тв10")
    assert (r["name"], r["legacy"], r["maker_cell"]) == ("ТВ-10", "Смерч", "Турбовыдумка / Выдумлит")
    # Написания — без повторов имени и прежнего имени.
    assert r["aliases"] == ["TV 10", "ТВ10М"]
    # Ячейка «Турбовыдумка / Выдумлит»: разрешилась часть — бренд реестра, а
    # сама ячейка рядом, чтобы несведённое «Выдумлит» не пропало.
    assert r["makers"] == [{"key": "turbovyd", "name": "Турбовыдумка"}]
    assert (r["power"], r["efficiency"], r["shafts"], r["shafts_label"]) == ("≈10 МВт", "≈30%", "2 вала", "Валы")
    assert (r["use_case"], r["note"], r["family"], r["source"]) == (
        "ГПА выдуманные", "Примечание выдуманное", "Выдуманное семейство", "справочник выдуманный")
    # Сегмента нет — направление по семейству справочника моделей.
    assert (r["segment"], r["dir"], r["dir_via"]) == (None, "gtu", "семейство")
    г = машина(база, "гп7")
    assert (г["dir"], г["dir_via"], г["shafts_label"], г["segment_name"]) == ("gpu", "сегмент", "Цилиндры",
                                                                          "ГПУ выдуманные")
    # Изготовителя нет в реестре — словом, ключа нет.
    assert г["makers"] == [{"key": None, "name": "Выдуммоторс"}]
    assert машина(база, "vm400")["makers"] == [{"key": "kelton", "name": "Kelton GmbH"}]


def test_машина_детали_код_и_бренд_рядом_усечение_видно(база):
    r = машина(база, "тв10")
    p = r["parts"]
    # 120 деталей, показаны 100 — общее число рядом со списком.
    assert (p["total"], len(p["list"]), p["with_unit"], p["no_unit"]) == (120, 100, 45, 75)
    # Сначала — с нашим номером KV, потом по потребности из сводки.
    assert [x["code"] for x in p["list"][:5]] == ["tvd117", "tvd077", "tvd007", "tvd120", "tvd119"]
    первая = p["list"][0]
    assert первая == {"code": "tvd117", "written": "TVD-117", "name": "Деталь турбины выдуманная 117",
                      "kv_no": "KV-000117-1", "brand": {"key": "turbovyd", "name": "Турбовыдумка"}, "unit": None}
    # У каждой детали списка бренд — соседним полем: реестра нет — словом.
    assert all(x["brand"] and x["brand"]["name"] for x in p["list"])
    assert {"key": None, "name": "Выдумлит"} == [x for x in p["list"] if x["code"] == "tvd120"][0]["brand"]
    с_узлом = [x for x in p["list"] if x["code"] == "tvd030"][0]
    assert с_узлом["unit"] == {"id": "rotor.bearing", "name": "Подшипник опорный выдуманный"}


def test_машина_узлы_по_деталям_и_типовое_дерево(база):
    r = машина(база, "тв10")
    assert [(u["id"], u["parts"], u["typical"], (u["parent"] or {}).get("id")) for u in r["units"]] == [
        ("rotor.bearing", 30, True, "rotor"), ("hot.liner", 10, True, "hot"), ("fasteners", 5, True, None)]
    # Типовое дерево ГТУ — корни не «gpu.»; число деталей машины — по поддереву;
    # порядок — критичность, потом имя.
    t = r["tree"]
    assert (t["dir"], t["via"]) == ("gtu", "семейство")
    assert [(s["id"], s["crit"], s["children"], s["parts"]) for s in t["systems"]] == [
        ("hot", "A", 1, 10), ("rotor", "A", 1, 30), ("fasteners", "C", 0, 5)]
    г = машина(база, "гп7")
    assert [(s["id"], s["parts"]) for s in г["tree"]["systems"]] == [("gpu.cpg", 1)]
    assert [(u["id"], u["parts"]) for u in г["units"]] == [("gpu.cpg.piston", 1)]
    assert (г["parts"]["total"], г["parts"]["no_unit"]) == (3, 2)
    # Горно-шахтной машине типового дерева нет — не подставляется чужое.
    б = машина(база, "бш3")
    assert (б["dir"], б["tree"]) == (None, None)
    assert [(u["id"], u["parts"], u["typical"]) for u in б["units"]] == [("fasteners", 1, False)]
    assert б["symptoms"]["n"] == 0


def test_машина_парк_и_ведомость(база):
    r = машина(база, "тв10")
    assert r["fleet"]["n"] == 2
    assert r["fleet"]["list"][0] == {"site": "Выдуманная КС-2", "owner": None, "units": "1", "year": None,
                                     "written": "Смерч", "note": None}
    assert r["fleet"]["list"][1]["owner"] == "Выдуманная энергетика"
    # Ведомость: строка с деталью каталога — её код и бренд; без каталога — ключ
    # номера; марка стали кодом не становится.
    b = r["bom"]
    assert b["n"] == 3
    assert [(x["code"], x["written"], x["node"], x["brand"]) for x in b["list"]] == [
        (None, "SS316", "КОРПУС", None),
        ("tvd001", "TVD-001", "РОТОР", {"key": "turbovyd", "name": "Турбовыдумка"}),
        ("zz777", "ZZ-777", "РОТОР", None)]


def test_машина_признаки_дефекты_ремонт_по_её_узлам(база):
    r = машина(база, "тв10")
    assert [x["name"] for x in r["symptoms"]["list"]] == ["Разброс термопар выдуманный", "Рост вибрации выдуманный"]
    # Признак узла ГПУ к турбине не идёт.
    assert r["symptoms"]["n"] == 2
    вибрация = r["symptoms"]["list"][1]
    assert (вибрация["confidence"], вибрация["defects"]) == ("low", [{"name": "Износ вкладыша выдуманный"}])
    assert r["symptoms"]["list"][0]["ops"] == [{"kind": "контроль", "name": "Эндоскопия выдуманная"}]
    # Дефекты: по детали машины, по имени машины (прежнее имя «Смерч»), по узлу.
    # Дефект, записанный для другой машины, к этой не идёт.
    д = r["defects"]
    assert [(x["name"], x["via"]) for x in д["list"]] == [
        ("Риск отказа: деталь выдуманная 2", "деталь"), ("Ресурс выдуманный", "машина"),
        ("Износ вкладыша выдуманный", "узел")]
    assert д["n"] == 3
    деталь = д["list"][0]
    assert (деталь["code"], деталь["written"], деталь["brand"]) == (
        "tvd002", "TVD-002", {"key": "turbovyd", "name": "Турбовыдумка"})
    assert д["list"][2]["ops"] == [{"kind": "ремонт", "name": "Замена вкладыша выдуманная"}]
    # Ремонт: операции узлов машины без семейства и своего семейства (sgt);
    # операции семейства ansaldo к машине sgt не идут.
    assert [(x["kind"], x["name"]) for x in r["procedures"]["list"]] == [
        ("контроль", "Эндоскопия выдуманная"), ("ремонт", "Замена вкладыша выдуманная"),
        ("модернизация", "Модернизация выдуманная")]
    # Дефект узла ГПУ — у газопоршневой машины; номер-марка стали — без кода.
    г = машина(база, "гп7")
    assert [x["name"] for x in г["symptoms"]["list"]] == ["Стук в цилиндре выдуманный"]
    [задир] = г["defects"]["list"]
    assert (задир["code"], задир["written"], задир["brand"], задир["via"]) == (None, "SS316", None, "узел")
    # У машины ВМ-400 (сегмент gtu) дефект детали чужой машины ТВ-10 не идёт.
    assert [x["name"] for x in машина(база, "vm400")["defects"]["list"]] == ["Износ вкладыша выдуманный"]


def test_машина_без_всего_открывается_пустой(база):
    r = машина(база, "пусто1")
    assert (r["name"], r["makers"], r["maker_cell"], r["dir"], r["tree"]) == ("Пустая выдуманная", [], None, None, None)
    assert (r["parts"]["total"], r["parts"]["list"], r["units"], r["fleet"]["n"], r["bom"]["n"]) == (0, [], [], 0, 0)
    assert r["partial"] == []


def test_узел_путь_машины_детали(база):
    r = узел(база, "rotor.bearing")
    assert (r["name"], r["name_en"], r["crit"], r["dir"], r["note"]) == (
        "Подшипник опорный выдуманный", "Journal bearing", "B", "gtu", "примечание к узлу выдуманное")
    assert (r["path"], r["children"]) == ([{"id": "rotor", "name": "Ротор выдуманный"}], [])
    # Машины: с деталями в узле — числом; того же направления — типово.
    м = r["machines"]
    assert (м["n"], м["typical_n"], м["with_parts"]) == (2, 2, 1)
    assert [(x["id"], x["parts"], x["typical"]) for x in м["list"]] == [("тв10", 30, True), ("vm400", 0, True)]
    assert м["list"][0]["brand"] == {"key": "turbovyd", "name": "Турбовыдумка"}
    assert (r["parts"]["total"], r["parts"]["here"], len(r["parts"]["list"])) == (30, 30, 30)
    assert all(x["brand"] for x in r["parts"]["list"])
    корень = узел(база, "rotor")
    assert (корень["path"], корень["parts"]["total"], корень["parts"]["here"]) == ([], 30, 0)
    assert корень["children"] == [{"id": "rotor.bearing", "name": "Подшипник опорный выдуманный", "crit": "B",
                                   "children": 0, "parts": 30}]
    assert корень["aftermarket"] == "только у изготовителя"
    # Крепёж: две машины ГТУ типово и горная — по детали, без «типово».
    к = узел(база, "fasteners")
    assert [(x["id"], x["parts"], x["typical"]) for x in к["machines"]["list"]] == [
        ("тв10", 5, True), ("бш3", 1, False), ("vm400", 0, True)]
    # Узел ГПУ: машины — ГПУ, признак системы — у её компонента.
    п = узел(база, "gpu.cpg.piston")
    assert (п["dir"], п["path"]) == ("gpu", [{"id": "gpu.cpg", "name": "ГПУ: ЦПГ выдуманная"}])
    assert [(x["id"], x["parts"], x["typical"]) for x in п["machines"]["list"]] == [("гп7", 1, True)]
    assert [(x["name"], x["unit"]["id"]) for x in п["symptoms"]["list"]] == [("Стук в цилиндре выдуманный", "gpu.cpg")]


def test_узел_признаки_дефекты_ремонт(база):
    r = узел(база, "rotor.bearing")
    [признак] = r["symptoms"]["list"]
    assert (признак["name"], признак["measure"], признак["defects"]) == (
        "Рост вибрации выдуманный", "виброскорость на опоре", [{"name": "Износ вкладыша выдуманный"}])
    # На карточке узла — все дефекты узла, и для какой машины записан — видно.
    assert [(x["name"], x["model"]) for x in r["defects"]["list"]] == [
        ("Износ вкладыша выдуманный", None), ("Риск отказа: чужая машина", "Другая выдуманная ГТУ")]
    assert r["defects"]["list"][0]["ops"] == [{"kind": "ремонт", "name": "Замена вкладыша выдуманная"}]
    # Операции узла и его предка (ротор), с подписью семейства.
    assert [(x["name"], x["unit"]["id"], x["family"]) for x in r["procedures"]["list"]] == [
        ("Замена вкладыша выдуманная", "rotor.bearing", None), ("Ремонт ротора выдуманный", "rotor", "ansaldo")]


def test_машина_и_узел_неизвестные_и_пустые(база):
    for i in ("nope", "", " ", "я" * 121):
        assert машина(база, i) is None, i
        assert узел(база, i) is None, i


def test_машина_и_узел_без_номеров_сделок_файлов_и_внутренних_ключей(база):
    for r in (машина(база, "тв10"), узел(база, "rotor.bearing"), машина(база, "vm400")):
        текст = json.dumps(r, ensure_ascii=False)
        for лишнее in ("D-777", "ФАЙЛ-777", "признак.", "дефект.", "ремонт.", "контроль.", "парк.т"):
            assert лишнее not in текст, (r["id"], лишнее)


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
    # Машина и узел без реестра: изготовитель и бренд детали — словом, ключа нет.
    c.execute(КОРПУС_МАШИН)
    m = машина(база, "тв10", ГОЛАЯ)
    assert (m["registry"], m["makers"]) == (False, [{"key": None, "name": "Турбовыдумка / Выдумлит"}])
    assert m["parts"]["list"][0]["brand"] == {"key": None, "name": "Турбовыдумка"}
    assert m["parts"]["total"] == 120 and m["tree"]["dir"] == "gtu"
    u = узел(база, "rotor.bearing", ГОЛАЯ)
    assert u["machines"]["list"][0]["brand"] == {"key": None, "name": "Турбовыдумка / Выдумлит"}


def test_усечение_по_бюджету_видно(база):
    c = база.cursor()
    c.execute("set portal_entity.budget_ms = '0'")
    try:
        r = код(база, "ZC-2002")
        b = бренд(база, "kelton")
        s = поставщик(база, "KV-S-000012-2")
        m = машина(база, "тв10")
        u = узел(база, "rotor.bearing")
    finally:
        c.execute("reset portal_entity.budget_ms")
    assert r["partial"] == ["предложения", "аналоги", "машины", "кто делает", "кому ещё писать"]
    assert r["offers"]["rows"] == 0 and r["write_to"] == []
    assert b["partial"] == ["предложения", "поставщики", "машины", "аналоги"]
    assert s["partial"] == ["бренды", "коды"]
    assert m["partial"] == ["узлы", "детали", "парк", "ведомость", "признаки", "дефекты", "ремонт"]
    # Несчитанный список — пустой, но число деталей посчитано и видно.
    assert (m["parts"]["total"], m["parts"]["list"], m["units"]) == (120, [], [])
    assert u["partial"] == ["машины", "детали", "признаки", "дефекты", "ремонт"]
    assert (u["parts"]["total"], u["machines"]["list"]) == (30, [])


def test_права_только_сервису(база):
    """Права — у каждой функции файла. Список берётся из цикла схемы, а полнота
    цикла сверяется с тем, что файл создаёт: новая функция без строки в цикле
    осталась бы исполнимой для anon через /rest/v1/rpc."""
    import re
    sql = (ROOT / "library" / "supabase" / СХЕМА).read_text(encoding="utf-8")
    без_пояснений = re.sub(r"--[^\n]*", "", sql)
    созданные = set(re.findall(r"create (?:or replace )?function (portal_\w+)\(", без_пояснений))
    цикл = без_пояснений[без_пояснений.index("foreach ф in array array["):]
    цикл = цикл[:цикл.index("] loop")]
    сигнатуры = re.findall(r"'(portal_\w+\([^']*\))'", цикл)
    assert {s.split("(")[0] for s in сигнатуры} == созданные, (созданные, сигнатуры)
    c = база.cursor()
    c.execute(f"set search_path to {ИМЯ}")
    for функция in сигнатуры:
        for роль, можно in (("anon", False), ("authenticated", False), ("service_role", True), ("public", False)):
            if роль == "public":
                # Пустой proacl — права по умолчанию, то есть execute у PUBLIC.
                c.execute("select p.proacl is null or exists (select 1 from aclexplode(p.proacl) a "
                          "where a.grantee = 0 and a.privilege_type = 'EXECUTE') "
                          "from pg_proc p where p.oid = %s::regprocedure", (f"{ИМЯ}.{функция}",))
                assert c.fetchone()[0] is False, (функция, "PUBLIC")
                continue
            c.execute("select has_function_privilege(%s, %s, 'execute')", (роль, f"{ИМЯ}.{функция}"))
            assert c.fetchone()[0] is можно, (функция, роль)


def test_схема_применяется_без_ролей_supabase(база):
    """Файл применяется на чистом PostgreSQL, где ролей anon, authenticated и
    service_role нет (правило 20), — по-настоящему, а не чтением текста.

    Роли кластерные, и в базе тестов они есть (фикстура их заводит). Поэтому в
    отдельной схеме заводится пустой вид pg_roles, а pg_catalog ставится в
    search_path ПОСЛЕ неё: блок прав спрашивает pg_roles без схемы и видит
    пустоту — ровно как на чистой базе. Файл обязан примениться целиком, снять
    права у PUBLIC и не выдать ничего несуществующей (для него) роли."""
    import re
    sql = (ROOT / "library" / "supabase" / СХЕМА).read_text(encoding="utf-8")
    без_пояснений = re.sub(r"--[^\n]*", "", sql)
    цикл = без_пояснений[без_пояснений.index("foreach ф in array array["):]
    сигнатуры = re.findall(r"'(portal_\w+\([^']*\))'", цикл[:цикл.index("] loop")])
    c = база.cursor()
    try:
        c.execute(f"drop schema if exists {БЕЗ_РОЛЕЙ} cascade")
        c.execute(f"create schema {БЕЗ_РОЛЕЙ}")
        c.execute(f"create view {БЕЗ_РОЛЕЙ}.pg_roles as select rolname from pg_catalog.pg_roles where false")
        c.execute(f"set search_path to {БЕЗ_РОЛЕЙ}, pg_catalog")
        c.execute("select count(*) from pg_roles where rolname in ('anon', 'authenticated', 'service_role')")
        assert c.fetchone()[0] == 0, "подмена pg_roles не действует — проверка ничего бы не доказала"
        for оператор in операторы(sql):
            c.execute(оператор)
        for функция in сигнатуры:
            c.execute("select p.proacl is null or exists (select 1 from aclexplode(p.proacl) a "
                      "where a.grantee = 0 and a.privilege_type = 'EXECUTE') "
                      "from pg_proc p where p.oid = %s::regprocedure", (f"{БЕЗ_РОЛЕЙ}.{функция}",))
            assert c.fetchone()[0] is False, (функция, "PUBLIC")
            # Для файла роли service_role нет — выдачи ей быть не должно.
            c.execute("select has_function_privilege('service_role', %s, 'execute')", (f"{БЕЗ_РОЛЕЙ}.{функция}",))
            assert c.fetchone()[0] is False, функция
        assert {"portal_model(text)", "portal_unit(text)"} <= set(сигнатуры)
    finally:
        c.execute("reset search_path")
        c.execute(f"drop schema if exists {БЕЗ_РОЛЕЙ} cascade")


# ── замечания скептиков 25.09.2026: каждый случай — на своей пробе ──────────
#
# Пробы кладут строки внутри транзакции и откатывают её: общий корпус и ответы
# остальных тестов от них не меняются.

def _в_пробе(conn, строки_sql: str, вызовы):
    c = conn.cursor()
    c.execute(f"set search_path to {ИМЯ}")
    c.execute("begin")
    try:
        c.execute(строки_sql)
        c.execute("analyze")
        out = []
        for функция, аргумент in вызовы:
            c.execute(f"select {функция}(%s)", (аргумент,))
            out.append(c.fetchone()[0])
        return out
    finally:
        c.execute("rollback")


def test_прилагательное_аналоговый_не_делает_аналогом(база):
    c = база.cursor()
    c.execute(f"set search_path to {ИМЯ}")
    for текст, да in (("Клапан, аналог", True), ("Аналоги: SKF", True), ("из аналогов", True),
                      ("equivalent to KL-7", True), ("эквивалент Kelton", True),
                      ("Датчик давления аналоговый 4-20 мА", False), ("выход аналоговая", False),
                      ("аналогичный клапан", False), ("эквивалентный диаметр 40", False),
                      ("Каналог выдуманный", False), (None, False)):
        c.execute("select portal_says_analog(%s)", (текст,))
        assert c.fetchone()[0] is да, текст
    [r] = _в_пробе(база, """
        insert into lib_prices (feed, source, part_number, item_name, price, currency, rfq_id, rfq_company, oem,
                                price_date)
        values ('разбор КП','КП','KL-7','Датчик давления аналоговый 4-20 мА',80,'EUR','R30','91201','Kelton',
                '2026-06-01')""", [("portal_code", "KL-7")])
    assert 80 in [x["price"] for x in r["offers"]["original"]]
    assert 80 not in [x["price"] for x in r["offers"]["analog"]]


def test_цепочка_слияний_до_корня(база):
    """A(91501) → B → C = KV-S-000012-2: ключ A — это C, и C видит строки A."""
    проба = """
        insert into sup_entity (id, kind, display_name, resolution, status, merged_into) values
         ('KV-S-000015-5','legal','Бета Промежуточная','merged','active','KV-S-000012-2');
        insert into sup_entity (id, kind, display_name, resolution, status, merged_into) values
         ('KV-S-000014-4','legal','Бета Старейшая','merged','active','KV-S-000015-5');
        insert into sup_identifier (sup_id, kind, value, value_norm, source, status, run_id) values
         ('KV-S-000014-4','bitrix','91501','91501','bitrix','verified','r1'),
         ('KV-S-000015-5','bitrix','91601','91601','bitrix','verified','r1');
        insert into lib_prices (feed, source, part_number, item_name, price, currency, rfq_id, rfq_company, oem,
                                price_date)
        values ('разбор КП','КП','ZC-2002','Седло выдуманное',71,'USD','R31','91501','Kelton GmbH','2026-06-02'),
               ('разбор КП','КП','PR-3003','Кольцо выдуманное',13,'USD','R32','91601','Kelton','2026-06-03'),
               ('разбор КП','КП','KL-7','Клапан выдуманный',96,'EUR','R33','91401','Kelton','2026-06-04');
    """
    карта, бета, дальняя, kl7, кл = _в_пробе(база, проба, [
        ("portal_companies", ["91501", "91601"]), ("portal_supplier", "KV-S-000012-2"),
        ("portal_supplier", "KV-S-000014-4"), ("portal_code", "KL-7"), ("portal_code", "ZC-2002")])
    assert {k: v["id"] for k, v in карта.items()} == {"91501": "KV-S-000012-2", "91601": "KV-S-000012-2"}
    assert 71 in [x["price"] for x in бета["codes"]] and "91501" in бета["bitrix"]
    assert (дальняя["id"], дальняя["merged_from"]) == ("KV-S-000012-2", "KV-S-000014-4")
    # Две карточки Битрикса одной компании (91201 и 91401) — один поставщик.
    assert kl7["offers"]["suppliers"] == 3
    # Бета уже давала цену на ZC-2002 — «Бета Промежуточная» в «кому писать» не
    # встаёт отдельной компанией.
    assert [w["company"]["id"] for w in кл["write_to"]] == ["KV-S-000011-1"]


def test_двусмысленное_написание_не_засчитывается_ни_одному_бренду(база):
    проба = """
        insert into lib_brand_alias (spelling, spelling_key, source, seen_at, brand_key, status, n_rows, rule, run_id)
        values ('Kel', lib_brand_key('Kel'), 'dict/oem.json', 'x1', 'kelton', 'разрешено', 1, 'тест', 'тест'),
               ('Kel', lib_brand_key('Kel'), 'lib_demand.oem', 'x2', 'skf', 'разрешено', 1, 'тест', 'тест');
        insert into lib_demand (deal_id, item_name, oem, part_number, qty, unit)
        values ('D20','Нечто выдуманное','Kel','YY-555',1,'шт');
    """
    skf, kelton, yy = _в_пробе(база, проба, [("portal_brand", "skf"), ("portal_brand", "kelton"),
                                             ("portal_code", "YY-555")])
    for б in (skf, kelton):
        assert "yy555" not in коды_списка(б["codes_demand"]) and "Kel" not in б["spellings"]
    assert kelton["demand"]["rows"] == 3 and kelton["demand"]["registry_rows"] == 3
    # Карточка кода: «Kel» — слово, бренда реестра нет.
    assert yy["brand"]["key"] is None


def test_обрубок_в_конце_карточки_запроса_снимается(база):
    """«…,5050» на 200-м знаке — обрубок «50501», а не элемент 5050 (SKF)."""
    проба = """
        insert into lib_brand_alias (spelling, spelling_key, source, seen_at, sp176_id, brand_key, status, n_rows,
                                     rule, run_id)
        values ('SKF', lib_brand_key('SKF'), 'СП-176', 'СП-176#5050', 5050, 'skf', 'разрешено', 1, 'тест', 'тест');
        insert into lib_prices (feed, source, part_number, item_name, price, currency, rfq_id, rfq_company, oem,
                                rfq_brands, price_date)
        values ('разбор КП','КП','TR-808','Труба выдуманная',5,'USD','R34','91201',null,
                repeat('99999,', 33) || '5050', '2026-06-05');
    """
    skf, бета = _в_пробе(база, проба, [("portal_brand", "skf"), ("portal_supplier", "KV-S-000012-2")])
    assert "tr808" not in коды_списка(skf["codes_offers"])
    tr = [x for x in бета["codes"] if x["code"] == "tr808"]
    assert tr and tr[0]["brand"] is None
    assert "skf" not in [(b["brand"] or {}).get("key") for b in бета["brands"] if b["asked_codes"]]


def test_слово_не_бренд_и_столкновение_колонок_не_становятся_брендом(база):
    проба = """
        insert into lib_demand (deal_id, item_name, oem, part_number, qty, unit) values
         ('D30','Прокладка выдуманная','любой','PQ-4040',2,'шт'),
         ('D31','Прокладка выдуманная','PQ-4040','PQ-4040',3,'шт');
        insert into lib_brand_alias (spelling, spelling_key, source, seen_at, brand_key, status, n_rows, rule, run_id)
        values ('любой', lib_brand_key('любой'), 'lib_demand.oem', 'z', null, 'не бренд', 3, 'тест', 'тест');
    """
    [r] = _в_пробе(база, проба, [("portal_code", "PQ-4040")])
    assert r["brand"] is None and r["brands"] == []


def test_деталь_с_id_не_равным_ключу_номера(база):
    """id «ht55norm», номер HT-55: по ссылке из каталога и по номеру — одна карточка."""
    проба = """
        insert into lib_parts (id, catalog_no, name, oem) values ('ht55norm', 'HT-55', 'Втулка нормальная', 'Kelton GmbH');
        insert into lib_demand (deal_id, item_name, oem, part_number, qty, unit)
        values ('D40','Втулка нормальная',null,'HT-55',1,'шт');
        insert into lib_prices (feed, source, part_number, item_name, price, currency, rfq_id, rfq_company, oem,
                                price_date)
        values ('разбор КП','КП','HT 55','Втулка нормальная',3,'USD','R35','91201',null,'2026-06-06');
    """
    по_id, по_номеру, kelton, бета = _в_пробе(база, проба, [
        ("portal_code", "ht55norm"), ("portal_code", "HT-55"), ("portal_brand", "kelton"),
        ("portal_supplier", "KV-S-000012-2")])
    for r in (по_id, по_номеру):
        assert (r["catalog"], r["demand"]["rows"], r["offers"]["rows"]) == (True, 1, 1), r["key"]
        assert r["brand"]["key"] == "kelton"
    assert "ht55" in коды_списка(kelton["codes_offers"])
    ht = [x for x in бета["codes"] if x["code"] == "ht55"]
    assert ht and ht[0]["brand"] == {"key": "kelton", "name": "Kelton GmbH"} and ht[0]["brand_src"] == "по каталогу"


def test_бренд_не_засчитывает_ответ_аналогом(база):
    """Запрос на Kelton (карточка), ответ SKF: не цена по бренду Kelton."""
    проба = """
        insert into lib_prices (feed, source, part_number, item_name, price, currency, rfq_id, rfq_company, oem,
                                rfq_brands, price_date)
        values ('разбор КП','КП','ZC-2002','Седло выдуманное',55,'USD','R36','91101','SKF','50501','2026-06-07');
    """
    kelton, zc, альфа = _в_пробе(база, проба, [("portal_brand", "kelton"), ("portal_code", "ZC-2002"),
                                                ("portal_supplier", "KV-S-000011-1")])
    assert kelton["offers"]["analog_rows"] == 2
    zc_строка = [x for x in kelton["codes_offers"] if x["code"] == "zc2002"]
    assert zc_строка and zc_строка[0]["suppliers"] == 1
    assert [x["price"] for x in zc["offers"]["analog"]] == [55]
    коды = {x["code"]: x for x in альфа["codes"]}
    assert (коды["zc2002"]["verdict"], коды["zc2002"]["why"]) == ("аналог", "назвал SKF, а спрашивали Kelton GmbH")
    assert коды["zc2002"]["brand"] == {"key": "skf", "name": "SKF"}


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
