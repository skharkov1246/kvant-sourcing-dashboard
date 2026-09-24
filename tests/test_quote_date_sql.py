"""Дата квотации и индексация на инфляцию — на настоящем PostgreSQL.

Схема библиотеки применяется целиком в отдельной схеме одноразовой базы
(LIBRARY_SQL_TEST_DSN, локаль C.UTF-8 — CLAUDE.md, правило 21а). Проверяется то,
что без базы не видно: проверка значений источника даты, досчёт только в пустую
дату и его откат, загрузка индексов с пересмотром и откатом, приведение цены
к месяцу — с оговоркой у каждой строки.

Корпус придуман (правило 18): страны настоящие, индексы — нет.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import date

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "library"))

from tests.test_library_schema_sql import DSN, операторы  # noqa: E402

pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

ИМЯ = "quote_date_smoke"

XML = """<?xml version='1.0' encoding='UTF-8'?>
<message:StructureSpecificData xmlns:message="urn:m" xmlns:ss="urn:ss">
 <message:DataSet>
  <Series COUNTRY="RUS" INDEX_TYPE="CPI" COICOP_1999="_T" TYPE_OF_TRANSFORMATION="IX"
          FREQUENCY="M" COMMON_REFERENCE_PERIOD="2010A">
   <Obs TIME_PERIOD="2025-M01" OBS_VALUE="200" REFERENCE_PERIOD="2010A"/>
   <Obs TIME_PERIOD="2025-M02" OBS_VALUE="202" REFERENCE_PERIOD="2010A"/>
   <Obs TIME_PERIOD="2026-M01" OBS_VALUE="220" REFERENCE_PERIOD="2010A"/>
  </Series>
  <Series COUNTRY="DEU" INDEX_TYPE="CPI" COICOP_1999="_T" TYPE_OF_TRANSFORMATION="IX"
          FREQUENCY="M" COMMON_REFERENCE_PERIOD="2020A">
   <Obs TIME_PERIOD="2025-M01" OBS_VALUE="120" REFERENCE_PERIOD="2020A"/>
   <Obs TIME_PERIOD="2026-M01" OBS_VALUE="100" REFERENCE_PERIOD="2025A"/>
  </Series>
 </message:DataSet>
</message:StructureSpecificData>"""


@pytest.fixture()
def cur():
    import psycopg2
    from psycopg2.extensions import parse_dsn

    assert parse_dsn(DSN)["dbname"] == "library_sql_test"
    созданные: list[str] = []
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    try:
        with conn.cursor() as c:
            for роль in ("anon", "authenticated"):
                c.execute("select 1 from pg_roles where rolname = %s", (роль,))
                if not c.fetchone():
                    c.execute(f"create role {роль} nologin")
                    созданные.append(роль)
            c.execute(f"drop schema if exists {ИМЯ} cascade")
            c.execute(f"create schema {ИМЯ}")
            c.execute(f"set search_path to {ИМЯ}")
            for имя in ("schema.sql", "schema_junk.sql"):
                for оператор in операторы(
                        (ROOT / "library" / "supabase" / имя).read_text(encoding="utf-8")):
                    c.execute(оператор)
            yield c
    finally:
        with conn.cursor() as c:
            c.execute(f"drop schema if exists {ИМЯ} cascade")
            for роль in созданные:
                c.execute(f"drop role if exists {роль}")
        conn.close()


def цена(cur, rfq, price, currency, дата=None, источник=None, feed="разбор КП"):
    cur.execute("insert into lib_prices (feed, rfq_id, part_number, price, currency, "
                "price_date, price_date_src) values (%s,%s,'ВЫДУМ-1',%s,%s,%s,%s) returning id",
                (feed, rfq, price, currency, дата, источник))
    return cur.fetchone()[0]


def test_источник_даты_проверяется_списком(cur):
    import psycopg2
    цена(cur, "1", 10, "RUB", "2025-03-12", "документ")
    with pytest.raises(psycopg2.errors.CheckViolation):
        цена(cur, "1", 10, "RUB", "2025-03-12", "дата разбора")


def test_схема_применяется_повторно(cur):
    """Идемпотентность: второй прогон схемы на уже заполненной базе не падает."""
    цена(cur, "1", 10, "RUB", "2025-03-12", "документ")
    for оператор in операторы((ROOT / "library/supabase/schema.sql").read_text(encoding="utf-8")):
        cur.execute(оператор)
    cur.execute("select count(*) from lib_prices")
    assert cur.fetchone()[0] == 1


def test_запись_разбора_кладёт_дату_и_источник(cur):
    import price_store
    import psycopg2.extras
    строка = price_store.строка(
        {"source_file": "7", "item_name": "Насос ВЫДУМ-1", "deal_id": "77",
         "price_date": "2025-03-12", "price_date_src": "письмо"},
        {"price": 5, "currency": "RUB", "confidence": "med"}, lambda s: str(s or ""))
    price_store.записать(cur, [строка], psycopg2.extras.execute_values)
    cur.execute("select price_date, price_date_src, rfq_id from lib_prices where source_url = '7'")
    assert cur.fetchone() == (date(2025, 3, 12), "письмо", "77")


def test_досчёт_пишет_только_в_пустую_дату_и_откатывается(cur):
    import psycopg2.extras
    import quote_dates_backfill as b
    пустая = цена(cur, "501", 10, "RUB")
    выведенная = цена(cur, "501", 11, "RUB", feed="разбор КП: выведено")
    из_документа = цена(cur, "501", 12, "RUB", "2025-03-12", "документ")
    чужой_поток = цена(cur, "501", 13, "RUB", feed="прайс")
    другая = цена(cur, "502", 14, "RUB")

    n = b.записать(cur, {501: date(2025, 1, 10)}, "qd-проба", psycopg2.extras.execute_values)
    assert n == 2
    cur.execute("select id, price_date, price_date_src, price_date_run from lib_prices order by id")
    по_id = {r[0]: r[1:] for r in cur.fetchall()}
    assert по_id[пустая] == (date(2025, 1, 10), "карточка: создана", "qd-проба")
    assert по_id[выведенная] == (date(2025, 1, 10), "карточка: создана", "qd-проба")
    assert по_id[из_документа] == (date(2025, 3, 12), "документ", None), "дата документа тронута"
    assert по_id[чужой_поток] == (None, None, None)
    assert по_id[другая] == (None, None, None)

    cur.execute(b.ОТКАТ, ("qd-проба",))
    assert cur.rowcount == 2
    cur.execute("select count(*) from lib_prices where price_date_src = 'карточка: создана'")
    assert cur.fetchone()[0] == 0
    cur.execute("select price_date_src from lib_prices where id = %s", (из_документа,))
    assert cur.fetchone()[0] == "документ"


def test_досчёт_находит_недатированные_карточки(cur):
    import quote_dates_backfill as b
    цена(cur, "501", 10, "RUB")
    цена(cur, "501", 11, "RUB")
    цена(cur, "502", 12, "RUB", "2025-03-12", "документ")
    цена(cur, "не-номер", 13, "RUB")
    cur.execute(b.БЕЗ_ДАТЫ, (list(b.ПОТОКИ),))
    assert dict(cur.fetchall()) == {"501": 2}
    cur.execute(b.СВОДКА, (list(b.ПОТОКИ),))
    assert cur.fetchone() == (4, 3, 1)


def test_индексы_загружаются_пересматриваются_и_откатываются(cur):
    import load_cpi as L
    import psycopg2.extras
    ev = psycopg2.extras.execute_values
    точки = L.разобрать(XML)
    assert L.записать(cur, точки, "cpi-1", ev) == 5
    # Повтор того же ответа ничего не меняет.
    assert L.записать(cur, точки, "cpi-2", ev) == 0
    # Пересмотр одной точки: хранит прежнее значение.
    пересмотр = [dict(т, index_value=201.0) if т["country"] == "RUS"
                 and т["month"] == date(2025, 1, 1) else т for т in точки]
    assert L.записать(cur, пересмотр, "cpi-3", ev) == 1
    cur.execute("select index_value, prev_value, prev_run from lib_cpi "
                "where country = 'RUS' and month = '2025-01-01'")
    assert tuple(float(x) if x is not None and not isinstance(x, str) else x
                 for x in cur.fetchone()) == (201.0, 200.0, "cpi-1")
    assert L.откатить(cur, "cpi-3") == (0, 1)
    cur.execute("select index_value, run_id from lib_cpi where country = 'RUS' "
                "and month = '2025-01-01'")
    v, run = cur.fetchone()
    assert (float(v), run) == (200.0, "cpi-1")
    assert L.откатить(cur, "cpi-1") == (5, 0)
    cur.execute("select count(*) from lib_cpi")
    assert cur.fetchone()[0] == 0


def test_приведение_цены_к_месяцу(cur):
    import load_cpi as L
    import psycopg2.extras
    L.записать(cur, L.разобрать(XML), "cpi-1", psycopg2.extras.execute_values)

    cur.execute("select lib_cpi_factor('rus', '2025-01-15', '2026-01-31')")
    assert float(cur.fetchone()[0]) == pytest.approx(1.1)
    # Германия: база сменилась между месяцами — делить нельзя, ответ пуст.
    cur.execute("select lib_cpi_factor('DEU', '2025-01-01', '2026-01-01')")
    assert cur.fetchone()[0] is None

    рубли = цена(cur, "1", 1000, "RUB", "2025-01-20", "документ")
    евро = цена(cur, "1", 1000, "EUR", "2025-02-03", "карточка: создана")
    без_даты = цена(cur, "1", 1000, "RUB")
    без_индекса = цена(cur, "1", 1000, "RUB", "2024-06-01", "письмо")
    cur.execute("select price_id, price_indexed, note from lib_prices_indexed('2026-01-01', 'RUS')")
    по_id = {r[0]: r[1:] for r in cur.fetchall()}

    assert float(по_id[рубли][0]) == pytest.approx(1100.0)
    assert "валюта не пересчитана" in по_id[рубли][1]
    assert "не совпадает" not in по_id[рубли][1]

    assert float(по_id[евро][0]) == pytest.approx(1000 * 220 / 202, rel=1e-6)
    assert "EUR не совпадает с валютой страны RUB" in по_id[евро][1]
    assert "по дате карточки запроса" in по_id[евро][1]

    assert по_id[без_даты] == (None, "нет даты квотации — не приведена")
    assert по_id[без_индекса][0] is None
    assert "нет индекса" in по_id[без_индекса][1]
