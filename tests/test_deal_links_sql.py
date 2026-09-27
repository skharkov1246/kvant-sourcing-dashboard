"""Связи карточка → сделка → заказчик на настоящей базе PostgreSQL.

Проверяется там, где ошибка и случилась бы, — в самой базе:
  · схема применяется дважды, во вторую схему той же базы, без схемы
    поставщиков (вид создаётся с пустым именем) и без ролей платформы;
  · свежая запись разбора: вставка, изменение с новым ключом прогона, неизменная
    строка остаётся за тем, кто её записал;
  · досчёт истории целиком (library/backfill_deal_links.py) на подставном
    портале: холостой не пишет ничего и соединение у него только для чтения;
    запись — только отсутствующие строки с ключом прогона; сверка Python ↔ SQL
    совпадает; часть, где портал не отдал поле связи, не пишется; откат снимает
    ровно свои строки;
  · имя заказчика — из реестра компаний, по корню цепочки слияний.

Работает при поднятой базе (LIBRARY_SQL_TEST_DSN). Своя схема; за собой тест
убирает всё. Корпус придуман (правило 18).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "library"))

import backfill_deal_links as bdl  # noqa: E402
import deal_links as dl  # noqa: E402
import indexer as ix  # noqa: E402

DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМА = "deal_links_test"
МИГРАЦИЯ = ROOT / "library" / "supabase" / "deal_links_schema.sql"
ПОСТАВЩИКИ = """
insert into sup_entity (id, kind, display_name, resolution, status) values
 ('KV-S-000021-1','legal','zakazchikodin','candidate','active'),
 ('KV-S-000022-2','legal','Заказчик Два Выдуманный','resolved','active');
insert into sup_entity (id, kind, display_name, resolution, status, merged_into) values
 ('KV-S-000023-3','legal','zakazchikstaryi','merged','active','KV-S-000022-2');
insert into sup_identifier (sup_id, kind, value, value_norm, source, status, run_id) values
 ('KV-S-000021-1','bitrix','901','901','bitrix','verified','r1'),
 ('KV-S-000023-3','bitrix','903','903','bitrix','verified','r1');
insert into sup_display_name (sup_id, source, name, run_id) values
 ('KV-S-000021-1', 'bitrix:title', 'Заказчик Один Выдуманный', 'r1');
"""


def операторы(путь: Path) -> list[str]:
    from tests.test_library_schema_sql import операторы as резать
    return резать(путь.read_text(encoding="utf-8"))


def применить(c, *файлы: str) -> None:
    for имя in файлы:
        for оператор in операторы(ROOT / "library" / "supabase" / имя):
            c.execute(оператор)


@pytest.fixture
def conn():
    import psycopg2
    с = psycopg2.connect(DSN, options=f"-c search_path={СХЕМА}")
    с.autocommit = True
    c = с.cursor()
    c.execute(f"drop schema if exists {СХЕМА} cascade")
    c.execute(f"create schema {СХЕМА}")
    c.execute(f"set search_path to {СХЕМА}")
    применить(c, "schema.sql", "schema_junk.sql", "suppliers_schema.sql")
    for _ in range(2):                          # повторяемость (правило 21)
        применить(c, "deal_links_schema.sql")
    c.execute(ПОСТАВЩИКИ)
    с.autocommit = False
    try:
        yield с
    finally:
        с.rollback()
        с.autocommit = True
        с.cursor().execute(f"drop schema if exists {СХЕМА} cascade")
        с.close()


def строки(conn, sql, *p):
    with conn.cursor() as c:
        c.execute(sql, p)
        return c.fetchall()


def test_свежая_запись_разбора_меняет_только_изменённое(conn):
    import psycopg2.extras
    ev = psycopg2.extras.execute_values
    with conn.cursor() as c:
        assert dl.записать_свежие(c, [("11", "41", "Запрос", "2026-09-01T10:00:00+03:00"),
                                      ("12", None, None, None),
                                      ("11", "41", "Запрос", "2026-09-01T10:00:00+03:00")],
                                  [("41", "Сделка", "901", "NEW", None)], "ix-1", ev) == (2, 1)
        # Повтор без изменений не трогает ничего; изменение — со своим ключом.
        assert dl.записать_свежие(c, [("11", "41", "Запрос", None)], [], "ix-2", ev) == (0, 0)
        assert dl.записать_свежие(c, [("12", "41", None, None)],
                                  [("41", "Сделка", "901", "WON", None)], "ix-3", ev) == (1, 1)
    conn.commit()
    assert строки(conn, "select card_id, deal_id, run_id, created_at is not null"
                        " from lib_rfq_cards order by 1") == [
        ("11", "41", "ix-1", True), ("12", "41", "ix-3", False)]
    assert строки(conn, "select stage, run_id from lib_deals") == [("WON", "ix-3")]


def test_схема_держит_номера(conn):
    import psycopg2
    for sql in ("insert into lib_rfq_cards (card_id, run_id) values ('0', 'x')",
                "insert into lib_rfq_cards (card_id, deal_id, run_id) values ('5', 'abc', 'x')",
                "insert into lib_deals (deal_id, company_id, run_id) values ('5', '0', 'x')"):
        with pytest.raises(psycopg2.errors.CheckViolation):
            with conn.cursor() as c:
                c.execute(sql)
        conn.rollback()


def test_имя_заказчика_из_реестра_по_корню_слияния(conn):
    with conn.cursor() as c:
        c.execute("insert into lib_deals (deal_id, title, company_id, run_id) values"
                  " ('41','С1','901','t'), ('42','С2','903','t'), ('43','С3','999','t'),"
                  " ('44','С4',null,'t')")
        c.execute("insert into lib_rfq_cards (card_id, deal_id, run_id) values"
                  " ('11','41','t'), ('12','43','t'), ('13',null,'t')")
    assert строки(conn, "select deal_id, company_sup_id, company_title from lib_deal_customer"
                        " order by 1") == [
        ("41", "KV-S-000021-1", "Заказчик Один Выдуманный"),
        ("42", "KV-S-000022-2", "Заказчик Два Выдуманный"),       # слит — к корню
        ("43", None, None), ("44", None, None)]                    # нет в реестре — пусто
    assert строки(conn, "select card_id, deal_title, company_title from lib_rfq_chain"
                        " order by 1") == [
        ("11", "С1", "Заказчик Один Выдуманный"), ("12", "С3", None), ("13", None, None)]


# ── досчёт истории на подставном портале ─────────────────────────────────────

КАРТОЧЕК, СДЕЛОК = 130, 60


def _портал(без_поля_связи: set[int] = frozenset()):
    """crm.item.list (СП-166) и crm.deal.list по ключу; журнал вызовов."""
    вызовы = []
    карточки = [{"id": i, "parentId2": (1000 + i % 40) if i % 3 else None,
                 "title": f"Запрос {i}", "createdTime": "2026-09-01T10:00:00+03:00"}
                for i in range(1, КАРТОЧЕК + 1)]
    for x in карточки:
        if x["id"] in без_поля_связи:
            del x["parentId2"]
    сделки = [{"ID": str(1000 + i), "TITLE": f"Сделка {i}",
               "COMPANY_ID": ("901" if i % 2 else "0"), "STAGE_ID": "NEW",
               "DATE_CREATE": "2026-08-01T10:00:00+03:00"} for i in range(СДЕЛОК)]

    def bx(method, params):
        вызовы.append(method)
        f = params.get("filter") or {}
        if method == "crm.item.list":
            if params.get("order") == {"id": "DESC"}:
                return {"result": {"items": [{"id": КАРТОЧЕК}]}}
            после, до = int(f.get(">id", 0)), f.get("<=id")
            items = [x for x in карточки if x["id"] > после and (до is None or x["id"] <= до)]
            return {"result": {"items": items[:50]}}
        if method == "crm.deal.list":
            if params.get("order") == {"ID": "DESC"}:
                return {"result": [{"ID": str(1000 + СДЕЛОК - 1)}]}
            после, до = int(f.get(">ID", 0)), f.get("<=ID")
            items = [x for x in сделки if int(x["ID"]) > после and (до is None or int(x["ID"]) <= до)]
            return {"result": items[:50]}
        raise AssertionError(method)
    return bx, вызовы


def _досчёт(conn, monkeypatch, *, писать: bool, shards="4", bx=None, **окружение):
    import psycopg2.extras
    bx, вызовы = bx or _портал()
    monkeypatch.setattr(ix, "bx", bx)
    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "https://portal.example.test/rest/1/x")
    monkeypatch.setenv("SHARDS", shards)
    monkeypatch.setenv("RUN_ID", окружение.pop("run_id", "lk-1"))
    for к in ("PLAN", "LIMIT", "ENTITIES"):
        monkeypatch.delenv(к, raising=False)
    for к, v in окружение.items():
        monkeypatch.setenv(к, v)
    conn.commit()
    conn.set_session(readonly=not писать)
    try:
        код = bdl.выполнить(conn, писать, psycopg2.extras.execute_values)
    finally:
        conn.rollback()
        conn.set_session(readonly=False)
    return код, вызовы


def test_холостой_не_пишет_и_сверка_сходится(conn, monkeypatch, capsys):
    код, вызовы = _досчёт(conn, monkeypatch, писать=False)
    out = capsys.readouterr().out
    assert код == 0
    assert строки(conn, "select count(*) from lib_rfq_cards") == [(0,)]
    связанных = sum(1 for i in range(1, КАРТОЧЕК + 1) if i % 3)
    assert f"cards: строк {КАРТОЧЕК}" in out and f"cards: со сделкой {связанных}" in out
    assert "гейт не пройден" not in out and "вставлено 0" in out
    # Запросов: наибольший номер + страницы четырёх частей, по сущности.
    assert вызовы.count("crm.item.list") <= 1 + (-(-КАРТОЧЕК // 50)) + 4
    assert "портал: запросов" in out


def test_запись_только_новых_с_ключом_и_откат(conn, monkeypatch, capsys):
    with conn.cursor() as c:
        # Разбор уже записал карточку 3 — с другой сделкой: досчёт её не трогает,
        # а расхождение печатает.
        c.execute("insert into lib_rfq_cards (card_id, deal_id, run_id) values ('3', '1777', 'ix-0')")
    conn.commit()
    код, _ = _досчёт(conn, monkeypatch, писать=True)
    out = capsys.readouterr().out
    assert код == 0, out
    assert строки(conn, "select count(*), count(deal_id) from lib_rfq_cards where run_id = 'lk-1'") == [
        (КАРТОЧЕК - 1, sum(1 for i in range(1, КАРТОЧЕК + 1) if i % 3 and i != 3))]
    assert строки(conn, "select deal_id, run_id from lib_rfq_cards where card_id = '3'") == [
        ("1777", "ix-0")]
    assert "cards: расходится с записанным 1" in out
    assert строки(conn, "select count(*), count(company_id) from lib_deals where run_id = 'lk-1'") == [
        (СДЕЛОК, СДЕЛОК // 2)]
    # Все карточки со сделкой выходят на сделку в lib_deals.
    assert "карточек, чья сделка не в lib_deals 1" in out        # 1777 — выдуманная разбором
    # Повтор ничего не добавляет.
    код, _ = _досчёт(conn, monkeypatch, писать=True, run_id="lk-2")
    assert код == 0 and строки(conn, "select count(*) from lib_rfq_cards where run_id = 'lk-2'") == [(0,)]
    # Откат снимает ровно свои строки.
    assert bdl.откатить(conn, "lk-1") == 0
    assert строки(conn, "select card_id, run_id from lib_rfq_cards") == [("3", "ix-0")]
    assert строки(conn, "select count(*) from lib_deals") == [(0,)]


def test_часть_без_поля_связи_не_пишется(conn, monkeypatch, capsys):
    # Во всей первой четверти (номера 1…33) портал не отдал parentId2.
    портал = _портал(без_поля_связи=set(range(1, 34)))
    код, _ = _досчёт(conn, monkeypatch, писать=True, bx=портал, ENTITIES="cards")
    out = capsys.readouterr().out
    assert код == 1
    assert "портал не отдал поле parentId2" in out
    assert строки(conn, "select min(card_id::int), count(*) from lib_rfq_cards") == [
        (34, КАРТОЧЕК - 33)]


def test_план_не_ходит_в_портал(conn, monkeypatch, capsys):
    with conn.cursor() as c:
        c.execute("insert into lib_files (file_id, deal_id, origin, status) values"
                  " ('f1', '120', 'поле запроса', 'разобран'), ('f2', '5', 'поле запроса', 'разобран')")
    conn.commit()
    код, вызовы = _досчёт(conn, monkeypatch, писать=False, PLAN="1")
    out = capsys.readouterr().out
    assert код == 0 and вызовы == []
    assert "план cards: номеров с файлами в базе 2, наибольший 120 · запросов не больше ~8" in out


def test_ограничение_берёт_свежие_номера(conn, monkeypatch, capsys):
    код, _ = _досчёт(conn, monkeypatch, писать=True, LIMIT="40", ENTITIES="cards")
    assert код == 0
    assert строки(conn, "select min(card_id::int), max(card_id::int), count(*) from lib_rfq_cards") == [
        (КАРТОЧЕК - 39, КАРТОЧЕК, 40)]


def test_миграция_без_поставщиков_и_без_ролей():
    """Файл применяется в голую схему: вид создаётся с пустым именем заказчика,
    роли платформы называются только через проверку наличия (правило 20)."""
    import psycopg2
    с = psycopg2.connect(DSN)
    с.autocommit = True
    c = с.cursor()
    сх = "deal_links_bare"
    try:
        c.execute(f"drop schema if exists {сх} cascade")
        c.execute(f"create schema {сх}")
        c.execute(f"set search_path to {сх}")
        for _ in range(2):
            применить(c, "deal_links_schema.sql")
        c.execute("insert into lib_deals (deal_id, company_id, run_id) values ('7', '901', 't')")
        c.execute("select company_title from lib_deal_customer")
        assert c.fetchall() == [(None,)]
    finally:
        c.execute(f"drop schema if exists {сх} cascade")
        с.close()
    текст = re.sub(r"(?m)--.*$", "", МИГРАЦИЯ.read_text(encoding="utf-8"))
    assert not re.search(r"(?:from|to)\s+(?:anon|authenticated|service_role)\b", текст)
    assert "lock_timeout" in текст
