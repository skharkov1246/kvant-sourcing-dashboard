"""Условия договоров на настоящей базе PostgreSQL (library/supabase/contract_terms_schema.sql).

  · схема применяется дважды, в свою схему одноразовой базы, без ролей платформы;
  · RLS на обеих таблицах включена и принудительна;
  · запись прогона scripts/contract_terms.База: строки условий и флаги с ключом
    прогона; повтор того же прогона не задваивает; итог — запросом по ключу;
  · повтор чтения файла (sha256, модель, версия) находится в базе;
  · прочитанная строка без условий и непрочитанная с условиями не встают;
  · откат по ключу прогона снимает ровно свои строки.

Работает при поднятой базе (LIBRARY_SQL_TEST_DSN). Корпус придуман (правило 18).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМА = "contract_terms_test"
ПУСТАЯ = "contract_terms_empty"


def _применить(c, имя: str) -> None:
    from tests.test_library_schema_sql import операторы
    for оператор in операторы((ROOT / "library" / "supabase" / имя).read_text(encoding="utf-8")):
        c.execute(оператор)


@pytest.fixture
def conn():
    import psycopg2
    с = psycopg2.connect(DSN, options=f"-c search_path={СХЕМА}")
    с.autocommit = True
    c = с.cursor()
    for сх in (СХЕМА, ПУСТАЯ):
        c.execute(f"drop schema if exists {сх} cascade")
        c.execute(f"create schema {сх}")
    c.execute(f"set search_path to {СХЕМА}")
    for _ in range(2):
        _применить(c, "contract_terms_schema.sql")
    try:
        yield с
    finally:
        for сх in (СХЕМА, ПУСТАЯ):
            с.cursor().execute(f"drop schema if exists {сх} cascade")
        с.close()


def _одно(conn, sql, *p):
    with conn.cursor() as c:
        c.execute(sql, p)
        return c.fetchone()


def _строка(run_id: str, card_id: int, *, status="ok", sha="a" * 64, side="customer", order=None) -> dict:
    прочитан = status in ("ok", "reused")
    import contract_terms as ct
    return {"run_id": run_id, "method_ver": ct.METHOD_VER, "deal_id": 9900001, "card_entity": 1174,
            "card_id": card_id, "order_id": order, "side": side,
            "side_source": "order_link" if order else "deal_link", "side_template": None, "side_company": side,
            "template": "Шаблон ВЫДУМКА",
            "doc_type": None, "file_field": "ufCrm80_1763638685405", "file_id": f"fid-{card_id}",
            "sha256": sha, "pages": 3, "note": None, "status": status, "model": "claude-opus-5-5",
            "terms": {"side": side, "deliveryDays": 120, "documents": ["паспорт ВЫДУМКА"]} if прочитан else None,
            "evidence": {"deliveryDays": {"quote": "ЦИТАТА-ВЫДУМКА", "page": 2}} if прочитан else None,
            "dropped": ["penaltyCapPct"] if прочитан else None,
            "usd": 0.125 if status == "ok" else 0.0}


def test_схема_rls_запись_повтор_и_откат(conn):
    import contract_terms as ct
    for таблица in ("contract_terms", "contract_b2b_flags"):
        assert _одно(conn, "select relrowsecurity, relforcerowsecurity from pg_class where oid = %s::regclass",
                     таблица) == (True, True), таблица
    база = ct.База(DSN, схема=СХЕМА)
    try:
        assert база.нет_таблиц() == []
        строки = [_строка("ct-t1", 1), _строка("ct-t1", 2, side="supplier", order=9700001, sha="b" * 64),
                  _строка("ct-t1", 3, status="no_file", sha=None)]
        строки[2]["file_id"] = ""
        # срок прогона и предел чтения — свои статусы, без условий
        строки += [_строка("ct-t1", 4, status="time", sha=None), _строка("ct-t1", 5, status="limit", sha="d" * 64)]
        флаги = [{"deal_id": 9900001, "order_id": 9700001, "code": "lateByDesign", "days": 9},
                 {"deal_id": 9900001, "order_id": 9700001, "code": "basisGap", "days": None}]
        for _ in range(2):                         # повтор того же прогона не задваивает
            база.записать_условия(строки)
            база.записать_флаги(флаги, "ct-t1")
        assert база.итог("ct-t1") == (5, 2, 0.25)
        кэш = база.из_кэша("a" * 64, "claude-opus-5-5")
        assert кэш["terms"]["deliveryDays"] == 120 and кэш["evidence"]["deliveryDays"]["page"] == 2
        assert кэш["dropped"] == ["penaltyCapPct"], "снятые поля повтора — как у первого чтения"
        assert база.из_кэша("d" * 64, "claude-opus-5-5") is None, "непрочитанный (limit) файл — не повтор"
        assert _одно(conn, "select side_company from contract_terms where run_id = 'ct-t1' and card_id = 2") \
            == ("supplier",)
        assert база.из_кэша("a" * 64, "claude-sonnet-5-5") is None, "условия другой модели — не повтор"
        assert база.из_кэша("c" * 64, "claude-opus-5-5") is None

        # второй прогон: свои строки, откат первого их не трогает
        база.записать_условия([_строка("ct-t2", 1, status="reused")])
        with conn.cursor() as c:
            c.execute("delete from contract_b2b_flags where run_id = 'ct-t1'")
            c.execute("delete from contract_terms where run_id = 'ct-t1'")
        assert база.итог("ct-t1") == (0, 0, 0.0)
        assert база.итог("ct-t2")[0] == 1
    finally:
        база.закрыть()


def test_статус_и_условия_согласованы(conn):
    import psycopg2
    import contract_terms as ct
    база = ct.База(DSN, схема=СХЕМА)
    try:
        плохие = [_строка("ct-t3", 1), _строка("ct-t3", 2, status="download")]
        плохие[0]["terms"] = None                          # «прочитан», а условий нет
        плохие[1]["terms"] = {"side": "customer"}           # «не скачался», а условия есть
        for r in плохие:
            with pytest.raises(psycopg2.errors.CheckViolation):
                база.записать_условия([r])
        # условий нет, как у непрочитанного: сработать может только проверка статуса
        неизвестный = _строка("ct-t3", 3, status="no_file")
        неизвестный["status"] = "maybe"
        with pytest.raises(psycopg2.errors.CheckViolation):
            база.записать_условия([неизвестный])
        чужая_сторона = _строка("ct-t3", 4, status="no_file")
        чужая_сторона["side_company"] = "trader"
        with pytest.raises(psycopg2.errors.CheckViolation):
            база.записать_условия([чужая_сторона])
        assert база.итог("ct-t3")[0] == 0
    finally:
        база.закрыть()


def test_без_схемы_прогон_называет_недостающие_таблицы(conn):
    import contract_terms as ct
    база = ct.База(DSN, схема=ПУСТАЯ)
    try:
        assert база.нет_таблиц() == ["contract_terms", "contract_b2b_flags"]
    finally:
        база.закрыть()
