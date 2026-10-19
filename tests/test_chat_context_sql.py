"""Чат сделки: курсор и оценки на настоящей базе PostgreSQL.

  · схема применяется дважды, в свою схему одноразовой базы, без ролей платформы
    и с ними (тогда — RLS, FORCE и права только сервисному ключу);
  · проверка оценки на Python и deal_chat_verdict_ok в базе согласны на каждом
    случае корпуса — два способа одной проверки;
  · оценка и курсор пишутся одной транзакцией: упала вставка — курсор стоит;
    чтение без оценки двигает очередь, но не курсор;
  · оценки только дописываются: правка и удаление запрещены, откат — пометкой,
    и помеченная оценка уходит из вида последней;
  · текст в оценку не пролезает ни лишним ключом, ни значением вне списка.

Работает при поднятой базе (LIBRARY_SQL_TEST_DSN). Корпус придуман (правило 18).
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import chat_context as cc  # noqa: E402
from tests.test_library_schema_sql import операторы  # noqa: E402

DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМА = "chat_ctx_test"
ФАЙЛ = ROOT / "library" / "supabase" / "deal_chat_schema.sql"
РОЛИ = ("anon", "authenticated", "service_role")
T0 = dt.datetime(2026, 10, 19, 9, 0, tzinfo=dt.timezone.utc)


def _применить(c) -> None:
    for оператор in операторы(ФАЙЛ.read_text(encoding="utf-8")):
        c.execute(оператор)


def _соединение(только_чтение: bool = False):
    import psycopg2
    с = psycopg2.connect(DSN, options=f"-c search_path={СХЕМА}")
    if только_чтение:
        с.set_session(readonly=True)
    return с


@pytest.fixture
def conn():
    с = _соединение()
    с.autocommit = True
    c = с.cursor()
    c.execute(f"drop schema if exists {СХЕМА} cascade")
    c.execute(f"create schema {СХЕМА}")
    c.execute(f"set search_path to {СХЕМА}")
    for _ in range(2):
        _применить(c)
    try:
        yield с
    finally:
        с.cursor().execute(f"drop schema if exists {СХЕМА} cascade")
        с.close()


def _одно(conn, sql, *p):
    with conn.cursor() as c:
        c.execute(sql, p)
        return c.fetchone()


def _годная(ids, **поправки) -> dict:
    v = {"state": "идёт", "direction": "вперёд", "risk": "низкий", "risk_kinds": [], "blocker": None,
         "ball_role": "кам", "reply_expected": True, "due": None, "evidence_msg_ids": list(ids),
         "confidence": 0.7}
    v.update(поправки)
    return v


def _корпус() -> list[dict]:
    """Каждое значение каждого списка в годной оценке, плюс порча по одному полю."""
    out = []
    for ключ, список in (("state", cc.СОСТОЯНИЯ), ("direction", cc.НАПРАВЛЕНИЯ), ("risk", cc.РИСКИ),
                         ("ball_role", cc.РОЛИ)):
        out += [_годная([10], **{ключ: x}) for x in список]
    out += [_годная([10], risk_kinds=[x]) for x in cc.ВИДЫ_РИСКА]
    out += [_годная([10], blocker={"kind": x, "on_role": "поставщик"}) for x in cc.ПРЕПЯТСТВИЯ]
    out += [_годная([10], blocker={"kind": "ответ", "on_role": x}) for x in cc.РОЛИ]
    out += [_годная([10], due={"date": "2026-10-24", "kind": x}) for x in cc.ВИДЫ_СРОКА]
    out += [_годная([10, 11, 12, 13, 14]), _годная([10], confidence=0), _годная([10], confidence=1),
            _годная([10], risk_kinds=["срок", "цена", "оплата"]), _годная([10], due={"date": "2028-02-29", "kind": "оплата"})]
    плохие = [
        {"state": "идёт по плану"}, {"direction": ""}, {"risk": None}, {"ball_role": "директор"},
        {"reply_expected": 1}, {"reply_expected": "true"},
        {"risk_kinds": ["срок", "цена", "оплата", "техника"]}, {"risk_kinds": ["срок", "срок"]},
        {"risk_kinds": "срок"}, {"risk_kinds": [1]},
        {"blocker": {"kind": "ответ"}}, {"blocker": {"kind": "ответ", "on_role": "кам", "why": "текст"}},
        {"blocker": "клиент молчит"}, {"blocker": {"kind": "молчание", "on_role": "кам"}},
        {"due": {"date": "2026-02-30", "kind": "оплата"}}, {"due": {"date": "2026-10-24T10:00", "kind": "оплата"}},
        {"due": {"date": "24.10.2026", "kind": "оплата"}}, {"due": {"date": "2026-10-24"}},
        {"due": "завтра"},
        {"evidence_msg_ids": []}, {"evidence_msg_ids": [10, 11, 12, 13, 14, 15]},
        {"evidence_msg_ids": [10, 10]}, {"evidence_msg_ids": [10.0]}, {"evidence_msg_ids": [True]},
        {"evidence_msg_ids": ["10"]}, {"evidence_msg_ids": [9]}, {"evidence_msg_ids": [16]},
        {"evidence_msg_ids": [0]}, {"evidence_msg_ids": [-10]},
        {"confidence": 1.01}, {"confidence": -0.1}, {"confidence": "0.5"}, {"confidence": None},
        {"confidence": False},
    ]
    out += [_годная([10], **п) for п in плохие]
    лишний = _годная([10])
    лишний["comment"] = "клиент обещал оплатить в пятницу"
    нет_ключа = _годная([10])
    del нет_ключа["due"]
    out += [лишний, нет_ключа, [], "текст"]
    return out


def test_python_и_база_согласны_на_каждом_случае(conn):
    расхождения, годных, плохих = [], 0, 0
    for v in _корпус():
        py = cc.проверить_оценку(copy.deepcopy(v), 10, 15) == ""
        (sql,) = _одно(conn, "select deal_chat_verdict_ok(%s::jsonb, 10, 15)", json.dumps(v, ensure_ascii=False))
        if py != sql:
            расхождения.append((v, py, sql))
        годных += py
        плохих += not py
    assert not расхождения, расхождения
    assert годных >= 50 and плохих >= 35, "корпус не пустой с обеих сторон"


def _строка(deal="5001", lo=10, hi=12, v=None, run="chat-ctx-t1", когда=T0) -> dict:
    return {"deal_id": deal, "model": "claude-opus-5-5", "prompt_sha256": "a" * 64,
            "method_ver": cc.METHOD_VER, "msg_from_id": lo, "msg_to_id": hi,
            "verdict": v or _годная([hi]), "tokens_in": 1200, "tokens_out": 300, "usd": 0.0108,
            "run_id": run, "assessed_at": когда}


def _курсор(hi=12, chat="7001", когда=T0) -> dict:
    return {"chat_id": chat, "last_msg_id": hi, "last_human_at": когда - dt.timedelta(hours=1),
            "checked_at": когда}


def test_оценка_и_курсор_одной_транзакцией(conn):
    store = cc.База(_соединение(), только_чтение=False)
    try:
        assert store.состояние() == {}
        store.записать_оценку(_строка(), _курсор())
        s = store.состояние()["5001"]
        assert s["last_msg_id"] == 12 and s["chat_id"] == "7001"
        assert _одно(conn, "select confidence::text, usd::text, run_id from deal_chat_ctx") == (
            "0.7", "0.010800", "chat-ctx-t1"), "уверенность — из оценки, а не отдельным значением"

        # оценка не прошла проверку базы (текст лишним ключом) — курсор стоит
        плохая = _годная([15])
        плохая["note"] = "клиент обещал оплатить"
        import psycopg2
        with pytest.raises(psycopg2.errors.CheckViolation):
            store.записать_оценку(_строка(lo=13, hi=15, v=плохая, run="chat-ctx-t2"), _курсор(15))
        assert store.состояние()["5001"]["last_msg_id"] == 12
        assert _одно(conn, "select count(*) from deal_chat_ctx") == (1,)

        # доказательство вне оценённого отрезка — тоже отказ базы
        with pytest.raises(psycopg2.errors.CheckViolation):
            store.записать_оценку(_строка(lo=13, hi=15, v=_годная([12]), run="chat-ctx-t2"), _курсор(15))
        assert store.состояние()["5001"]["last_msg_id"] == 12

        # чтение без оценки: очередь двигается, курсор оценки — нет
        позже = T0 + dt.timedelta(days=1)
        store.отметить_чтение("5001", "7001", позже - dt.timedelta(minutes=5), позже, "chat-ctx-t3")
        s = store.состояние()["5001"]
        assert s["last_msg_id"] == 12 and s["checked_at"] == позже
        assert _одно(conn, "select run_id from deal_chat_state") == ("chat-ctx-t1",), \
            "ключ прогона у курсора — того, кто его сдвинул"
        store.отметить_чтение("5002", "7002", None, позже, "chat-ctx-t3")
        assert store.состояние()["5002"]["last_msg_id"] == 0

        # курсор не уходит назад
        store.записать_оценку(_строка(lo=5, hi=8, v=_годная([8]), run="chat-ctx-t4"), _курсор(8))
        assert store.состояние()["5001"]["last_msg_id"] == 12
    finally:
        store.conn.close()


def test_оценки_только_дописываются_и_откат_пометкой(conn):
    store = cc.База(_соединение(), только_чтение=False)
    try:
        store.записать_оценку(_строка(run="chat-ctx-t1"), _курсор())
        store.записать_оценку(_строка(lo=13, hi=14, v=_годная([14], state="стоит"), run="chat-ctx-t2",
                                      когда=T0 + dt.timedelta(hours=1)), _курсор(14))
        assert _одно(conn, "select state, due_date from deal_chat_ctx_latest where deal_id = 5001") == ("стоит", None)
        assert store.прошлые(["5001"])["5001"]["verdict"]["state"] == "стоит"
    finally:
        store.conn.close()

    import psycopg2
    with conn.cursor() as c:
        for sql in ("update deal_chat_ctx set verdict = verdict || '{\"state\": \"идёт\"}'",
                    "update deal_chat_ctx set usd = 0",
                    "delete from deal_chat_ctx",
                    "truncate deal_chat_ctx"):
            with pytest.raises(psycopg2.errors.RaiseException):
                c.execute(sql)
        c.execute("update deal_chat_ctx set voided_at = now() where run_id = 'chat-ctx-t2'")
        with pytest.raises(psycopg2.errors.RaiseException):
            c.execute("update deal_chat_ctx set voided_at = null where run_id = 'chat-ctx-t2'")
    assert _одно(conn, "select state, msg_to_id from deal_chat_ctx_latest where deal_id = 5001") == ("идёт", 12)
    assert _одно(conn, "select count(*) from deal_chat_ctx") == (2,), "помечено, а не удалено"


def test_холостой_без_схемы_и_только_чтение(conn):
    with conn.cursor() as c:
        c.execute("drop table deal_chat_state cascade")
    store = cc.База(_соединение(только_чтение=True), только_чтение=True)
    try:
        assert store.состояние() == {} and store.схема is False
        assert store.прошлые(["5001"]) == {}
    finally:
        store.conn.close()


def test_доступ_только_сервисному_ключу():
    import psycopg2
    с = _соединение()
    с.autocommit = True
    c = с.cursor()
    c.execute("select rolname from pg_roles where rolname = any(%s)", (list(РОЛИ),))
    были = {r[0] for r in c.fetchall()}
    созданные = [р for р in РОЛИ if р not in были]
    try:
        for р in созданные:
            c.execute(f"create role {р} nologin")
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        c.execute(f"create schema {СХЕМА}")
        c.execute(f"set search_path to {СХЕМА}")
        for _ in range(2):
            _применить(c)
        c.execute("""select relname, relrowsecurity, relforcerowsecurity from pg_class
                      where relnamespace = %s::regnamespace and relname like 'deal_chat_%%' and relkind = 'r'
                      order by relname""", (СХЕМА,))
        assert c.fetchall() == [("deal_chat_ctx", True, True), ("deal_chat_state", True, True)]
        for таблица in ("deal_chat_ctx", "deal_chat_state", "deal_chat_ctx_latest"):
            имя = f"{СХЕМА}.{таблица}"
            for р in ("anon", "authenticated"):
                c.execute("select has_table_privilege(%s, %s, 'select')", (р, имя))
                assert not c.fetchone()[0], f"{р} читает {таблица}"
            c.execute("select count(*) from information_schema.role_table_grants "
                      "where table_schema = %s and table_name = %s and grantee = 'PUBLIC'", (СХЕМА, таблица))
            assert c.fetchone()[0] == 0, f"PUBLIC имеет права на {таблица}"
            c.execute("select has_table_privilege('service_role', %s, 'select')", (имя,))
            assert c.fetchone()[0]
        c.execute("select has_table_privilege('service_role', %s, 'delete')", (f"{СХЕМА}.deal_chat_ctx",))
        assert not c.fetchone()[0], "оценки сервисный ключ не удаляет"
        c.execute("select has_column_privilege('service_role', %s, 'voided_at', 'update'), "
                  "has_column_privilege('service_role', %s, 'verdict', 'update')",
                  (f"{СХЕМА}.deal_chat_ctx", f"{СХЕМА}.deal_chat_ctx"))
        assert c.fetchone() == (True, False)
    except psycopg2.Error:
        raise
    finally:
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        for р in созданные:
            c.execute(f"drop role if exists {р}")
        с.close()
