"""Цены нашего КП заказчику: свой поток, который не смешивается с поставщиками.

Проверяется на настоящей схеме PostgreSQL, всеми читателями разом:
  · разбор файла поля сделки «Offer from us» со входом OUR_OFFER_PRICES пишет
    строки в поток «наше КП заказчику» с номером сделки, а заявка заказчика в
    соседнем поле той же сделки цены не даёт;
  · строка нашего потока — даже с номером компании-поставщика, который она по
    устройству не несёт, — не попадает ни в сборку предложений (crossref), ни в
    коды с ценой (codes_sql: kp_codes и buy_codes), ни в вид sup_quote_price,
    ни в поиск и карточки портала (portal_search, portal_code, portal_supplier),
    ни в закупочные коды замера (codes_with_prices), где видна отдельно;
  · переразбор нашего КП (REPARSE_FOLDER) пишет только цены этого потока:
    спрос, пометки и lib_files не трогаются, повтор идемпотентен.

Работает при поднятой базе (LIBRARY_SQL_TEST_DSN). Корпус придуман (правило 18).
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "library"))

import doc_folder  # noqa: E402
import indexer as ix  # noqa: E402
import price_store  # noqa: E402
from tests.test_read_sheet import c as ячейка, xlsx, лист as разметка_листа  # noqa: E402

DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМА = "our_offer_feed_test"
НАШЕ = "ufCrm_1585568303498"            # Offer from us
СПЕЦИФИКАЦИЯ = "ufCrm_1633502831"       # заявка заказчика
ЦЕНА = 7777.77                          # в корпусе больше нигде не встречается
ФАЙЛЫ = ("schema.sql", "schema_junk.sql", "suppliers_schema.sql", "brands_schema.sql",
         "portal_schema.sql", "portal_entity_schema.sql", "deal_links_schema.sql")

КОРПУС = f"""
insert into sup_entity (id, kind, display_name, resolution, status) values
 ('KV-S-000031-1','legal','Альфа Выдуманная','resolved','active');
insert into sup_identifier (sup_id, kind, value, value_norm, source, status, run_id) values
 ('KV-S-000031-1','bitrix','1101','1101','bitrix','verified','r1');
insert into lib_prices (feed, source, part_number, item_name, price, currency, rfq_id, rfq_company) values
 ('разбор КП','КП','KL-7','Клапан выдуманный',10,'USD','R1','1101'),
 ('{price_store.FEED_НАШЕ_КП}','{price_store.ИСТОЧНИК_НАШЕ_КП}','KL-7','Клапан выдуманный',
  {ЦЕНА},'USD','41',null),
 ('{price_store.FEED_НАШЕ_КП}','{price_store.ИСТОЧНИК_НАШЕ_КП}','OUR-777','Изделие выдуманное',
  {ЦЕНА},'USD','41','1101');
"""


def операторы(путь: Path) -> list[str]:
    from tests.test_library_schema_sql import операторы as резать
    return резать(путь.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def база():
    import psycopg2
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    c = conn.cursor()
    созданные = []
    try:
        for роль in ("anon", "authenticated", "service_role"):
            c.execute("select 1 from pg_roles where rolname = %s", (роль,))
            if not c.fetchone():
                c.execute(f"create role {роль} nologin")
                созданные.append(роль)
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        c.execute(f"create schema {СХЕМА}")
        c.execute(f"set search_path to {СХЕМА}")
        for имя in ФАЙЛЫ:
            for оператор in операторы(ROOT / "library" / "supabase" / имя):
                c.execute(оператор)
        c.execute(КОРПУС)
        ix.ensure_segments(c)            # справочник сегментов — как у разбора
        yield conn
    finally:
        c.execute("reset search_path")
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        for роль in созданные:
            c.execute(f"drop role if exists {роль}")
        conn.close()


def ответ(conn, sql, p=()):
    c = conn.cursor()
    c.execute(f"set search_path to {СХЕМА}")
    c.execute(sql, p or None)
    return c.fetchall()


def test_сборка_предложений_видит_только_поставщика(база):
    import crossref
    c = база.cursor()
    c.execute(f"set search_path to {СХЕМА}")
    c.execute(crossref.ПРЕДЛОЖЕНИЯ_SQL, (crossref.FEED,))
    i = [d[0] for d in c.description].index("price")
    assert sorted(float(r[i]) for r in c.fetchall()) == [10.0]


def test_коды_с_ценой_без_нашего_потока(база):
    import codes_sql
    коды = ответ(база, "with parts as (select 1 as n, 0 as k), " + codes_sql.PRICE_SETS
                 + " select 'кп', code from kp_codes union all select 'закуп', code from buy_codes")
    assert ("кп", "kl7") in коды and ("закуп", "kl7") in коды
    assert not [к for к in коды if к[1] == "our777"], коды


def test_вид_предложений_поставщиков(база):
    assert ответ(база, "select count(*) from sup_quote_price where price = %s", (ЦЕНА,)) == [(0,)]
    assert ответ(база, "select count(*) from sup_quote_price") == [(1,)]


def test_поиск_и_карточки_портала(база):
    виды = {r[0] for r in ответ(база, "select kind from portal_search(%s, 10)", ("OUR-777",))}
    assert "код" not in виды
    карточка = ответ(база, "select portal_code('KL-7')::text")[0][0] or ""
    assert "7777" not in карточка
    поставщик = ответ(база, "select portal_supplier('KV-S-000031-1')::text")[0][0] or ""
    assert "7777" not in поставщик and "OUR-777" not in поставщик


def test_замер_кодов_видит_наш_поток_отдельно(база):
    spec = importlib.util.spec_from_file_location("cwp_sql", ROOT / "scripts" / "codes_with_prices.py")
    cwp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cwp)
    c = база.cursor()
    c.execute(f"set search_path to {СХЕМА}")
    c.execute(cwp.ПОДГОТОВКА, (cwp.FEED_КП, list(cwp.НЕ_ЗАКУПОЧНЫЕ), list(cwp.НЕ_ЗАКУПОЧНЫЕ)))
    c.execute("select ключ from коды_цен_все order by 1")
    assert [r[0] for r in c.fetchall()] == ["kl7"]
    c.execute("select поток, строк from коды_не_закупочные")
    assert c.fetchall() == [(price_store.FEED_НАШЕ_КП, 2)]
    c.execute("discard temp")


# ── разбор и переразбор файла нашего КП ─────────────────────────────────────

def кп_xlsx() -> bytes:
    """Книга собирается из XML через zipfile, как в tests/test_read_sheet.py:
    openpyxl в гейте не ставится (gate.yml), и разбор обязан идти без него."""
    ряды = []
    for r, строка in enumerate(
            (["Коммерческое предложение выдуманное"],
             ["№", "Наименование", "Артикул", "Кол-во", "Ед. изм.", "Цена, USD", "Сумма, USD"],
             [1, "Клапан выдуманный", "KL-7", 2, "шт", ЦЕНА, 2 * ЦЕНА],
             [2, "Седло выдуманное", "ZC-2002", 3, "шт", 100, 300]), 1):
        ряды.append([ячейка(f"{chr(ord('A') + k)}{r}", v) for k, v in enumerate(строка)])
    return xlsx([("КП", разметка_листа(ряды), None)])


def ссылка(fid: str, поле: str) -> dict:
    return {"deal": "41", "field": поле, "field_title": None, "origin": "поле сделки",
            "fo": {"id": fid, "urlMachine": "https://portal.example.test/f"}, "our_company": None,
            "owner_created": "2026-09-01T10:00:00+03:00"}


@pytest.fixture
def разбор(monkeypatch):
    # Каскад чтения: книгу читает read_sheet, а не openpyxl, которого в гейте нет.
    monkeypatch.setattr(ix, "КАСКАД", True)
    monkeypatch.setattr(ix, "SOURCE", "deals")
    monkeypatch.setattr(ix, "НАШЕ_КП", False)
    monkeypatch.setattr(ix, "download", lambda fo, rec=None: кп_xlsx())
    monkeypatch.setattr(ix, "наши_имена", lambda: ())


def test_разбор_пишет_наше_кп_своим_потоком(разбор, monkeypatch):
    rec, items = ix.handle(ссылка("f-our", НАШЕ))
    assert rec["status"] == "разобран" and ix.строки_цен(rec, items) == []    # вход выключен
    monkeypatch.setattr(ix, "НАШЕ_КП", True)
    rec, items = ix.handle(ссылка("f-our", НАШЕ))
    строки = ix.строки_цен(rec, items)
    поля = [dict(zip(price_store.КОЛОНКИ, r)) for r in строки]
    assert sorted(p["price"] for p in поля) == [100, ЦЕНА]
    assert {(p["feed"], p["rfq_id"], p["rfq_company"]) for p in поля} == {
        (price_store.FEED_НАШЕ_КП, "41", None)}
    # Заявка заказчика в соседнем поле той же сделки цены не даёт и со входом.
    rec, items = ix.handle(ссылка("f-spec", СПЕЦИФИКАЦИЯ))
    assert rec["status"] == "разобран" and ix.строки_цен(rec, items) == []


def test_переразбор_нашего_кп_пишет_только_цены(база, разбор, monkeypatch, capsys):
    import psycopg2
    import reparse
    c = база.cursor()
    c.execute(f"set search_path to {СХЕМА}")
    c.execute("insert into lib_files (file_id, deal_id, origin, field, status, kind, rows_found,"
              " parser_version, processed_at) values"
              " ('f-our', '41', 'поле сделки', %s, 'разобран', 'xlsx/docx', 2, 99, '2026-09-01'),"
              " ('f-spec', '41', 'поле сделки', %s, 'разобран', 'xlsx/docx', 2, 99, '2026-09-01')",
              (НАШЕ, СПЕЦИФИКАЦИЯ))
    c.execute("insert into lib_demand (deal_id, item_name, part_number, source_file) values"
              " ('41', 'Клапан выдуманный', 'KL-7', 'f-our'), ('41', 'Седло выдуманное', 'ZC-2002', 'f-our')")
    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "https://portal.example.test/rest/1/x")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://example.test/db")
    monkeypatch.setattr(reparse, "APPLY", True)
    monkeypatch.setattr(reparse, "ПАПКА", doc_folder.НАШЕ_ПРЕДЛОЖЕНИЕ)
    monkeypatch.setattr(reparse, "НАШЕ_КП_РЕЖИМ", True)
    monkeypatch.setattr(reparse, "ПОТОК_ЦЕН", price_store.FEED_НАШЕ_КП)
    monkeypatch.setattr(ix, "connect", lambda *a, **k: psycopg2.connect(
        DSN, options=f"-c search_path={СХЕМА}"))
    обход = [ссылка("f-our", НАШЕ), ссылка("f-spec", СПЕЦИФИКАЦИЯ)]
    monkeypatch.setattr(ix, "collect_refs", lambda *a, **k: обход)

    def состояние():
        return (ответ(база, "select feed, rfq_id, count(*) from lib_prices"
                            " where source_url is not null group by 1, 2"),
                ответ(база, "select count(*) from lib_demand"),
                ответ(база, "select count(*) from lib_row_junk"),
                ответ(база, "select file_id, processed_at, parser_version from lib_files order by 1"))

    файлы_до = состояние()[3]
    for _ in range(2):                                   # повтор идемпотентен
        assert reparse.main() == 0
        цены, спрос, пометки, файлы = состояние()
        assert цены == [(price_store.FEED_НАШЕ_КП, "41", 2)]
        assert (спрос, пометки, файлы) == ([(2,)], [(0,)], файлы_до)
    out = capsys.readouterr().out
    assert "кандидатов на переразбор: 1" in out and "записано файлов: 1 (только цены нашего КП)" in out
    # Поставщиков по-прежнему один, а в «разбор КП» не легло ничего нового.
    assert ответ(база, "select count(*) from lib_prices where feed = 'разбор КП'") == [(1,)]
