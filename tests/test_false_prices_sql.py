"""Пометка ложных строк цены на настоящей схеме: замер, запись, откат, страницы.

ЗАЧЕМ. Правило ложной строки цены (quotes.ложная_цена) живёт в Python, а
исключение помеченных — в базе, видом lib_prices_live. Разойтись они могут в
двух местах: запись пометит не то, что обвинил замер, или вид исключит не то,
что помечено. Здесь проверяется совпадение Python и SQL по номерам строк и то,
что страницы — карточки портала, поиск, бренды и коды, перекрёстная система —
помеченную строку больше не показывают, а после отката показывают снова.

Что закреплено:
  • холостой замер ничего не пишет;
  • запись помечает ровно обвинённые правилом строки потоков разбора КП и
    писем, чужой поток («прайс») не трогает; вид исключает ровно помеченные;
  • журнал lib_mark_runs получает строку прогона, откат снимает все его пометки;
  • гейт массового обвинения файла с таблицей отменяет запись целиком;
  • страницы читают вид: код, чья единственная строка цены помечена, пропадает
    из карточки, поиска, снимков брендов и перекрёстной системы;
  • вида нет (схема без schema_junk.sql) — чтение идёт по таблице.

Корпус придуман (CLAUDE.md, правило 18). Работает при поднятой базе
PostgreSQL 16 в локали C.UTF-8 (LIBRARY_SQL_TEST_DSN).
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from library import codes_sql, company_names, crossref, price_store
from tests.test_library_schema_sql import операторы

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

ИМЯ = "false_prices_sql_test"
ГОЛАЯ = "false_prices_sql_bare"
ФАЙЛЫ = ("schema.sql", "schema_junk.sql", "suppliers_schema.sql", "portal_schema.sql",
         "portal_entity_schema.sql")

КП = price_store.FEED
ПИСЬМО = price_store.FEED_ПИСЬМА
ТЕКСТ = "цена опознана в тексте по равенству кол-во × цена = сумма; валюта в файле не названа"
ИЗ_СУММЫ = "цена выведена делением суммы строки на количество"

# (поток, файл, наименование, код, единица, кол-во, цена, сумма, оговорка, ложная)
СТРОКИ = [
    (КП, "Ф-ТЕКСТ", "Total Amount (RMB): 143880", "143880", None, 1, 143880, 143880, None, True),
    (КП, "Ф-ТЕКСТ", "CIN NO L70109MH2010PLC123456 12 1 12", "L70109MH2010PLC123456", None,
     12, 1, None, ТЕКСТ, True),
    (КП, "Ф-ТЕКСТ", "PAYMENT TERMS 20 1 20", "", None, 20, 1, None, ТЕКСТ, True),
    (КП, "Ф-ТЕКСТ", "Подшипник выдуманный VX-500 2 шт 150,00 300,00", "VX-500", None,
     2, 150, None, ТЕКСТ, False),
    (КП, "Ф-ТАБЛ", "Page 1 of 1", "", None, 1, 1, 1, ИЗ_СУММЫ, True),
    (КП, "Ф-ТАБЛ", "Шайба выдуманная", "ВЫД-125-01", "шт", 1, 1, 1, None, False),
    (КП, "Ф-ТАБЛ", "Втулка выдуманная", "VX-500", "шт", 5, 12, 60, None, False),
    (КП, "Ф-ТАБЛ", "Втулка выдуманная", "VX-501", "шт", 3, 20, 60, None, False),
    (КП, "Ф-ТАБЛ", "Кольцо выдуманное", "VX-502", "шт", 4, 5, 20, None, False),
    (ПИСЬМО, "mail:Ф-ПИСЬМО", "Phone: +91 22 2345 6789", "2345", None, 12, 1, 12, None, True),
    (ПИСЬМО, "mail:Ф-ПИСЬМО", "Клапан выдуманный", "VX-503", "pcs", 2, 800, 1600, None, False),
    # Чужой поток: правило его не судит, даже если строка похожа на ложную.
    ("прайс", None, "Total Amount", "143880", None, 1, 1, 1, None, False),
] + (
    # Настоящие строки других файлов: доля обвинённых в потоке — как в жизни,
    # ниже порога гейта 1, иначе запись отменилась бы по нему.
    [(КП, "Ф-ДРУГОЙ", "Кольцо выдуманное", "VX-502", "шт", 4, 5, 20, None, False)] * 20
    + [(ПИСЬМО, "mail:Ф-ПИСЬМО-2", "Клапан выдуманный", "VX-503", "pcs", 2, 800, 1600, None,
        False)] * 10)
ФАЙЛЫ_ЦЕН = [("Ф-ТЕКСТ", "текст"), ("Ф-ТАБЛ", "таблица"), ("mail:Ф-ПИСЬМО", "таблица"),
             ("Ф-ДРУГОЙ", "таблица"), ("mail:Ф-ПИСЬМО-2", "таблица")]


def _прибор():
    spec = importlib.util.spec_from_file_location("mark_false_prices_sql",
                                                  ROOT / "scripts" / "mark_false_prices.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def база():
    import psycopg2
    from psycopg2.extensions import parse_dsn

    assert parse_dsn(DSN)["dbname"] == "library_sql_test"
    conn = psycopg2.connect(DSN)
    conn.autocommit = True      # в миграции CREATE INDEX CONCURRENTLY, как у psql
    c = conn.cursor()
    try:
        for схема, файлы in ((ИМЯ, ФАЙЛЫ), (ГОЛАЯ, ("schema.sql",))):
            c.execute(f"drop schema if exists {схема} cascade")
            c.execute(f"create schema {схема}")
            c.execute(f"set search_path to {схема}")
            for имя in файлы:
                for оператор in операторы((ROOT / "library" / "supabase" / имя).read_text(encoding="utf-8")):
                    c.execute(оператор)
        yield conn
    finally:
        c.execute("reset search_path")
        c.execute(f"drop schema if exists {ИМЯ} cascade")
        c.execute(f"drop schema if exists {ГОЛАЯ} cascade")
        conn.close()


def _наполнить(conn, строки=СТРОКИ, файлы=ФАЙЛЫ_ЦЕН) -> None:
    c = conn.cursor()
    c.execute(f"set search_path to {ИМЯ}")
    c.execute("truncate lib_price_junk, lib_mark_runs, lib_prices, lib_files cascade")
    for файл, путь in файлы:
        c.execute("insert into lib_files (file_id, status, parse_path) values (%s, 'разобран', %s)",
                  (файл, путь))
    for поток, файл, имя, код, ед, кол, цена, сумма, оговорка, _л in строки:
        c.execute("insert into lib_prices (feed, source, source_url, item_name, part_number, qty_unit,"
                  " qty, price, total, note, currency, confidence, rfq_company, rfq_id)"
                  " values (%s, 'КП', %s, %s, %s, %s, %s, %s, %s, %s, 'USD', 'low', '91001', 'R1')",
                  (поток, файл, имя, код, ед, кол, цена, сумма, оговорка))


def _соединение():
    """Отдельное соединение без autocommit — как у прогона: запись и сверка в
    одной транзакции, откат при расхождении действительно откатывает."""
    import psycopg2
    return psycopg2.connect(DSN, options=f"-c search_path={ИМЯ}")


def _выполнить(**к) -> int:
    м = _прибор()
    conn = _соединение()
    try:
        return м.выполнить(conn, **к)
    finally:
        conn.close()


def _выбрать(conn, sql, *п) -> list:
    c = conn.cursor()
    c.execute(f"set search_path to {ИМЯ}")
    c.execute(sql, п)
    return c.fetchall()


def _исключены(conn) -> set[str]:
    return {r[0] for r in _выбрать(conn, "select item_name from lib_prices p where not exists"
                                         " (select 1 from lib_prices_live l where l.id = p.id)")}


ЛОЖНЫЕ = {r[2] for r in СТРОКИ if r[9]}


def test_холостой_замер_ничего_не_пишет(база, capsys):
    _наполнить(база)
    assert _выполнить(apply=False) == 0
    assert _выбрать(база, "select count(*) from lib_price_junk") == [(0,)]
    вывод = capsys.readouterr().out
    # Журнал публичный — ни наименований, ни кодов (правило 17).
    for r in СТРОКИ:
        assert r[2] not in вывод and (not r[3] or r[3] not in вывод)


def test_запись_помечает_ровно_обвинённое_и_вид_их_исключает(база):
    _наполнить(база)
    assert _выполнить(apply=True, run_id="ложная-цена-тест-1") == 0
    # Python ↔ SQL: вид исключает ровно те строки, что обвинило правило, — и
    # правило, спрошенное заново по строкам из базы, согласно с пометками.
    assert _исключены(база) == ЛОЖНЫЕ
    строки = _выбрать(база, "select id, feed, item_name, part_number, qty_unit, qty, price, total,"
                            " note from lib_prices")
    имена = ("id", "feed", "item_name", "part_number", "qty_unit", "qty", "price", "total", "note")
    по_правилу = {r[0] for r in строки
                  if r[1] in (КП, ПИСЬМО) and price_store.причина_отказа(dict(zip(имена, r)))}
    помечены = {r[0] for r in _выбрать(база, "select price_id from lib_price_junk"
                                             " where run_id = 'ложная-цена-тест-1'")}
    assert по_правилу == помечены
    # Причины — закрытым списком, строки прогона — в журнале.
    причины = {r[0] for r in _выбрать(база, "select distinct reason from lib_price_junk")}
    assert причины <= set(price_store.quotes.ПРИЧИНЫ_ЛОЖНОЙ)
    журнал = _выбрать(база, "select rule, mode, rows_marked, finished_at is not null"
                            " from lib_mark_runs where run_id = 'ложная-цена-тест-1'")
    assert журнал == [("ложная-цена-v1", "разметка", len(ЛОЖНЫЕ), True)]
    # Чужой поток не тронут, хоть строка и похожа на итог.
    assert _выбрать(база, "select count(*) from lib_prices_live where feed = 'прайс'") == [(1,)]
    # Повторная запись не ставит пометок заново: уже помечены.
    assert _выполнить(apply=True, run_id="ложная-цена-тест-2") == 0
    assert _выбрать(база, "select count(*) from lib_price_junk"
                          " where run_id = 'ложная-цена-тест-2'") == [(0,)]
    # Откат прогона — вид снова равен таблице.
    assert _выполнить(revert="ложная-цена-тест-1") == 0
    assert _исключены(база) == set()
    assert _выбрать(база, "select reverted_at is not null from lib_mark_runs"
                          " where run_id = 'ложная-цена-тест-1'") == [(True,)]


def test_гейт_массового_обвинения_отменяет_запись(база, capsys):
    """Файл с таблицей, у которого обвинено пять строк из восьми, — стоп."""
    ложная = (КП, "Ф-ТАБЛ", "Page 1 of 1", "", None, 1, 1, 1, ИЗ_СУММЫ, True)
    настоящая = (КП, "Ф-ТАБЛ", "Втулка выдуманная", "VX-500", "шт", 5, 12, 60, None, False)
    много = (КП, "Ф-ДРУГОЙ", "Кольцо выдуманное", "VX-502", "шт", 4, 5, 20, None, False)
    _наполнить(база, [ложная] * 5 + [настоящая] * 3 + [много] * 40,
               ФАЙЛЫ_ЦЕН)
    assert _выполнить(apply=True, run_id="ложная-цена-тест-3") == 1
    assert _выбрать(база, "select count(*) from lib_price_junk") == [(0,)]
    assert "НЕ ПРОЙДЕН" in capsys.readouterr().out


def test_сверка_после_записи_откатывает_транзакцию(база, monkeypatch):
    """Вид, не исключающий пометок, — расхождение Python и SQL: ничего не пишется."""
    _наполнить(база)
    c = база.cursor()
    c.execute(f"set search_path to {ИМЯ}")
    c.execute("create or replace view lib_prices_live as select p.* from lib_prices p")
    try:
        assert _выполнить(apply=True, run_id="ложная-цена-тест-4") == 1
        assert _выбрать(база, "select count(*) from lib_price_junk") == [(0,)]
        assert _выбрать(база, "select count(*) from lib_mark_runs"
                              " where run_id = 'ложная-цена-тест-4'") == [(0,)]
    finally:
        for оператор in операторы((ROOT / "library" / "supabase" / "schema_junk.sql").read_text(encoding="utf-8")):
            if оператор.lower().startswith(("drop view if exists lib_prices_live",
                                             "create view lib_prices_live")):
                c.execute(оператор)


# ── страницы читают вид ──────────────────────────────────────────────────────

def _страницы(conn) -> dict:
    """Что покажут страницы по коду 143880 (единственная строка — ложный итог)."""
    c = conn.cursor()
    c.execute(f"set search_path to {ИМЯ}")
    out = {}
    c.execute("select portal_code('143880')")
    out["карточка"] = c.fetchone()[0]
    c.execute("select key from portal_search('143880', 10) where kind = 'код'")
    out["поиск"] = [r[0] for r in c.fetchall()]
    живые = price_store.живые_есть(c)
    c.execute(price_store.живые_sql(company_names.имена_sql(
        crossref.ПРЕДЛОЖЕНИЯ_SQL, company_names.вид_имён_есть(c)), живые), (crossref.FEED,))
    out["перекрёстная"] = "143880" in repr(c.fetchall())
    c.execute("begin")
    c.execute(codes_sql.SETTINGS)
    текст = []
    for имя, sql in codes_sql.запросы([], имена=company_names.вид_имён_есть(c), живые=живые).items():
        c.execute(sql)
        текст.append(repr(c.fetchall()))
    c.execute("rollback")
    out["бренды"] = "143880" in "".join(текст)
    return out


def test_страницы_не_показывают_помеченную_строку(база):
    _наполнить(база)
    до = _страницы(база)
    assert до["карточка"] is not None and до["поиск"] == ["143880"]
    assert до["бренды"] is True
    assert _выполнить(apply=True, run_id="ложная-цена-тест-5") == 0
    после = _страницы(база)
    assert после["карточка"] is None and после["поиск"] == []
    assert после["бренды"] is False
    assert до["перекрёстная"] is True and после["перекрёстная"] is False
    assert _выполнить(revert="ложная-цена-тест-5") == 0
    assert _страницы(база)["карточка"] is not None


def test_вида_нет_чтение_идёт_по_таблице(база):
    c = база.cursor()
    c.execute(f"set search_path to {ГОЛАЯ}")
    assert price_store.живые_есть(c) is False
    c.execute("insert into lib_prices (feed, item_name, price) values (%s, 'Втулка выдуманная', 5)",
              (КП,))
    c.execute(price_store.живые_sql("select count(*) from lib_prices_live p where p.feed = %s",
                                    False), (КП,))
    assert c.fetchall() == [(1,)]
    c.execute(f"set search_path to {ИМЯ}")
    assert price_store.живые_есть(c) is True
