"""Происхождения писем в запросах снимка брендов: старое считается как было.

ЗАЧЕМ. Письма (library/mail_source.py, 24.09.2026) добавили три новых
значения lib_files.origin. Запросы library/codes_sql.py отбирали спрос
литералом «поле сделки»; расширение отбора не должно сдвинуть ни одного числа
по прежним источникам — проверяется сравнением результата ДО и ПОСЛЕ
добавления писем на одной базе (правило 0: считай, у скольких стало хуже).

Корпус придуман (CLAUDE.md, правило 18). Работает при поднятой базе
(LIBRARY_SQL_TEST_DSN), как соседние тесты SQL.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from library import codes_sql, doc_folder
from tests.test_library_schema_sql import операторы

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

ИМЯ = "mail_origins_sql_test"
ФАЙЛЫ = ("schema.sql", "schema_junk.sql", "suppliers_schema.sql")

СТАРОЕ = """
insert into lib_files (file_id, deal_id, origin, field, status) values
 ('fc1','D1','поле сделки','ufCrm_1633502831','разобран'),
 ('fc2','D2','поле сделки','ufCrm_1633502831','разобран'),
 ('fus','D2','поле сделки','ufCrm_1585568303498','разобран'),
 ('fr1','R1','поле запроса','ufCrm18_1','разобран');
insert into lib_demand (deal_id, item_name, oem, part_number, source_file) values
 ('D1','Клапан выдуманный','Келтон','KL-7','fc1'),
 ('D2','Клапан выдуманный','Келтон','KL-8','fc2'),
 ('D2','Втулка выдуманная','Vortex','DD-40','fus'),
 ('R1','Клапан выдуманный','Келтон','KL-9','fr1');
"""

# Письма: сделка D3 (заказчик), лид L5 (заказчик), компания C9 (поставщик).
# Бренд писем — свой (Zeta), чтобы по прежним брендам сравнение шло строка в
# строку. Одно письмо лида без колонки side: сторону даёт запасной путь.
ПИСЬМА = f"""
insert into lib_files (file_id, deal_id, origin, field, field_title, side, status) values
 ('mail:1','D3','{doc_folder.ПИСЬМО_СДЕЛКИ}','письмо 11','входящее письмо','заказчик','разобран'),
 ('mail:2','L5','{doc_folder.ПИСЬМО_ЛИДА}','письмо 12','входящее письмо','заказчик','разобран'),
 ('mail:3','L6','{doc_folder.ПИСЬМО_ЛИДА}','письмо 13','входящее письмо',null,'разобран'),
 ('mail:4','C9','{doc_folder.ПИСЬМО_ПОСТАВЩИКА}','письмо 14','входящее письмо',null,'разобран');
insert into lib_demand (deal_id, item_name, oem, part_number, source_file) values
 ('D3','Муфта выдуманная','Zeta','ZT-1','mail:1'),
 ('L5','Муфта выдуманная','Zeta','ZT-2','mail:2'),
 ('L6','Муфта выдуманная','Zeta','ZT-3','mail:3'),
 ('C9','Муфта выдуманная','Zeta','ZT-4','mail:4');
"""

КАРТА = [("келтон", "kelton"), ("kelton", "kelton"), ("zeta", "zeta")]
# Колонки, которые зависят от числа брендов, а не от самого бренда.
ОБЩИЕ = {"rank", "brands_total", "brands_2plus"}


@pytest.fixture(scope="module")
def снимки():
    import psycopg2
    from psycopg2.extensions import parse_dsn

    assert parse_dsn(DSN)["dbname"] == "library_sql_test"
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    созданные: list[str] = []
    c = conn.cursor()

    def прогнать():
        c.execute("begin")
        c.execute(codes_sql.SETTINGS)
        c.execute(codes_sql.запросы(КАРТА)["brands"])
        колонки = [d[0] for d in c.description]
        бренды = {r[колонки.index("brand_key")]: dict(zip(колонки, r)) for r in c.fetchall()}
        c.execute("with\n" + codes_sql.FILES_CTE + "\nselect file_id, side from files")
        стороны = dict(c.fetchall())
        c.execute("rollback")
        return бренды, стороны

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
        c.execute(СТАРОЕ)
        до = прогнать()
        c.execute(ПИСЬМА)
        после = прогнать()
        yield до, после
    finally:
        c.execute(f"drop schema if exists {ИМЯ} cascade")
        for роль in созданные:
            c.execute(f"drop role if exists {роль}")
        conn.close()


def test_прежние_бренды_не_сдвинулись(снимки):
    (бренды_до, _), (бренды_после, _) = снимки
    assert бренды_до, "корпус не дал ни одного бренда — сравнивать нечего"
    for ключ, строка in бренды_до.items():
        было = {к: v for к, v in строка.items() if к not in ОБЩИЕ}
        стало = {к: v for к, v in бренды_после[ключ].items() if к not in ОБЩИЕ}
        assert было == стало, f"бренд {ключ} сдвинулся от писем"


def test_прежние_стороны_не_сдвинулись(снимки):
    (_, стороны_до), (_, стороны_после) = снимки
    for файл, сторона in стороны_до.items():
        assert стороны_после[файл] == сторона
    assert стороны_до["fr1"] == "поставщик" and стороны_до["fc1"] == "заказчик"


def test_письма_сделки_считаются_сделками_письма_лида_нет(снимки):
    _, (бренды, _) = снимки
    zeta = бренды["zeta"]
    # ZT-1 (сделка), ZT-2 и ZT-3 (лиды) — спрос заказчика; ZT-4 — поставщик.
    assert zeta["codes_customer"] == 3
    assert zeta["codes_kp_file"] == 1
    # «Сделок заказчика» — одна: у писем лидов номер лида, а не сделки.
    assert zeta["deals_customer"] == 1


def test_запасная_сторона_писем_без_колонки(снимки):
    _, (_, стороны) = снимки
    assert стороны["mail:3"] == "заказчик"      # письмо лида
    assert стороны["mail:4"] == "поставщик"     # письмо компании
