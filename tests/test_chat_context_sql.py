"""Чат сделки: курсор и оценки на настоящей базе PostgreSQL.

  · схема применяется дважды, в свою схему одноразовой базы, без ролей платформы
    и с ними (тогда — RLS, FORCE и права только сервисному ключу);
  · проверка оценки на Python и deal_chat_verdict_ok в базе согласны на каждом
    случае корпуса — два способа одной проверки;
  · оценка и курсор пишутся одной транзакцией: упала вставка — курсор стоит;
    чтение без оценки двигает очередь, но не курсор;
  · оценки только дописываются: правка и удаление запрещены, откат — пометкой,
    и помеченная оценка уходит из вида последней;
  · неудача вызова: расход и очередь с серией неудач — одной транзакцией, курсор
    оценки стоит; записанная оценка серию обнуляет;
  · расход только дописывается, сумма за сутки — с полуночи по Москве; исход и
    класс брака — только из списков Python;
  · откат по гейту качества: оценки прогона помечены, курсоры — как до прогона;
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


def _трата(deal="5001", run="chat-ctx-t1", когда=T0, usd=0.0108, outcome="записано", defect=None,
           api_status=None, estimated=False) -> dict:
    return {"at": когда, "run_id": run, "part": 1, "deal_id": deal, "model": "claude-opus-5-5",
            "outcome": outcome, "defect": defect, "api_status": api_status, "tokens_in": 1200,
            "tokens_out": 300, "usd": usd, "estimated": estimated}


def _неудача(deal="5001", chat="7001", когда=T0, run="chat-ctx-t1") -> dict:
    return {"deal_id": deal, "chat_id": chat, "last_human_at": когда - dt.timedelta(hours=2),
            "checked_at": когда, "run_id": run}


def test_оценка_и_курсор_одной_транзакцией(conn):
    store = cc.База(_соединение(), только_чтение=False)
    try:
        assert store.состояние() == {}
        store.записать_оценку(_строка(), _курсор(), _трата())
        s = store.состояние()["5001"]
        assert s["last_msg_id"] == 12 and s["chat_id"] == "7001" and s["fail_streak"] == 0
        assert _одно(conn, "select confidence::text, usd::text, run_id from deal_chat_ctx") == (
            "0.7", "0.010800", "chat-ctx-t1"), "уверенность — из оценки, а не отдельным значением"
        assert _одно(conn, "select count(*), sum(usd)::text from deal_chat_spend") == (1, "0.010800")

        # оценка не прошла проверку базы (текст лишним ключом) — курсор стоит
        плохая = _годная([15])
        плохая["note"] = "клиент обещал оплатить"
        import psycopg2
        with pytest.raises(psycopg2.errors.CheckViolation):
            store.записать_оценку(_строка(lo=13, hi=15, v=плохая, run="chat-ctx-t2"), _курсор(15),
                                  _трата(run="chat-ctx-t2"))
        assert store.состояние()["5001"]["last_msg_id"] == 12
        assert _одно(conn, "select count(*) from deal_chat_ctx") == (1,)
        assert _одно(conn, "select count(*) from deal_chat_spend") == (1,), "расход — в той же транзакции"

        # доказательство вне оценённого отрезка — тоже отказ базы
        with pytest.raises(psycopg2.errors.CheckViolation):
            store.записать_оценку(_строка(lo=13, hi=15, v=_годная([12]), run="chat-ctx-t2"), _курсор(15),
                                  _трата(run="chat-ctx-t2"))
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

        # удачное чтение — отметка read_at; неудачное (нет доступа) сдвигает очередь, но не её
        # (вкладка «Реализация» верит оценке только по свежему удачному чтению, 23.10.2026)
        assert store.состояние()["5001"]["read_at"] == позже
        ещё = позже + dt.timedelta(days=1)
        store.отметить_чтение("5001", "7001", None, ещё, "chat-ctx-t3", False)
        s = store.состояние()["5001"]
        assert s["checked_at"] == ещё and s["read_at"] == позже
        store.отметить_чтение("5003", "7003", None, ещё, "chat-ctx-t3", False)
        assert store.состояние()["5003"]["read_at"] is None

        # курсор не уходит назад
        store.записать_оценку(_строка(lo=5, hi=8, v=_годная([8]), run="chat-ctx-t4"), _курсор(8),
                              _трата(run="chat-ctx-t4"))
        assert store.состояние()["5001"]["last_msg_id"] == 12
    finally:
        store.conn.close()


def test_оценки_только_дописываются_и_откат_пометкой(conn):
    store = cc.База(_соединение(), только_чтение=False)
    try:
        store.записать_оценку(_строка(run="chat-ctx-t1"), _курсор(), _трата())
        store.записать_оценку(_строка(lo=13, hi=14, v=_годная([14], state="стоит"), run="chat-ctx-t2",
                                      когда=T0 + dt.timedelta(hours=1)), _курсор(14), _трата(run="chat-ctx-t2"))
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


def test_неудача_двигает_очередь_и_серию_но_не_курсор(conn):
    store = cc.База(_соединение(), только_чтение=False)
    try:
        store.записать_оценку(_строка(), _курсор(), _трата())
        позже = T0 + dt.timedelta(days=1)
        for i in range(1, 3):
            store.записать_неудачу(_трата(outcome="брак", defect="evidence", run="chat-ctx-t2"),
                                   _неудача(когда=позже, run="chat-ctx-t2"))
            s = store.состояние()["5001"]
            assert (s["fail_streak"], s["last_msg_id"], s["checked_at"]) == (i, 12, позже)
        assert _одно(conn, "select run_id from deal_chat_state") == ("chat-ctx-t1",), \
            "ключ прогона у курсора — того, кто сдвинул курсор оценки"
        # новая сделка, сразу неудача — строка с курсором 0 и серией 1
        store.записать_неудачу(_трата(deal="5002", outcome="отказ", run="chat-ctx-t2"),
                               _неудача(deal="5002", chat="7002", run="chat-ctx-t2"))
        assert (store.состояние()["5002"]["last_msg_id"], store.состояние()["5002"]["fail_streak"]) == (0, 1)
        # сбой связи — только расход, очередь не трогается
        store.записать_неудачу(_трата(deal="5003", outcome="сбой_api", usd=0.2, estimated=True,
                                      run="chat-ctx-t2"), None)
        assert "5003" not in store.состояние()
        # записанная оценка обнуляет серию
        store.записать_оценку(_строка(lo=13, hi=14, v=_годная([14]), run="chat-ctx-t3"), _курсор(14),
                              _трата(run="chat-ctx-t3"))
        assert store.состояние()["5001"]["fail_streak"] == 0
        assert _одно(conn, "select count(*), count(*) filter (where estimated) from deal_chat_spend") == (6, 1)
    finally:
        store.conn.close()


def test_расход_только_дописывается_и_сутки_по_москве(conn):
    store = cc.База(_соединение(), только_чтение=False)
    полночь = dt.datetime(2026, 10, 19, 0, 0, tzinfo=cc._MSK)        # 18.10 21:00 UTC
    try:
        for когда, usd in ((полночь - dt.timedelta(seconds=1), 3.0), (полночь, 1.25),
                           (полночь + dt.timedelta(hours=20), 0.5)):
            store.записать_неудачу(_трата(когда=когда, usd=usd, outcome="брак", defect="не JSON"), None)
        assert store.потрачено_с(полночь) == pytest.approx(1.75), "вчерашнее по Москве не в счёт"
        assert store.потрачено_с(cc.начало_суток(полночь + dt.timedelta(hours=1))) == pytest.approx(1.75)
    finally:
        store.conn.close()
    import psycopg2
    with conn.cursor() as c:
        for sql in ("update deal_chat_spend set usd = 0", "delete from deal_chat_spend",
                    "truncate deal_chat_spend"):
            with pytest.raises(psycopg2.errors.RaiseException):
                c.execute(sql)
    assert _одно(conn, "select count(*) from deal_chat_spend") == (3,)


def test_исход_и_брак_расхода_python_и_база_согласны(conn):
    import psycopg2
    годные = [("записано", None), ("отказ", None), ("сбой_api", None), ("сбой_записи", None)]
    годные += [("брак", x) for x in cc.КЛАССЫ_БРАКА]
    плохие = [("брак", None), ("записано", "evidence"), ("сбой_api", "клиент молчит"), ("брак", "клиент молчит"),
              ("ответ модели", None), ("брак", "отказ модели")]
    with conn.cursor() as c:
        for i, (исход, брак) in enumerate(годные + плохие):
            ожидается = (исход, брак) in годные
            assert ожидается == (исход in cc.ИСХОДЫ and (исход == "брак") == (брак is not None)
                                 and (брак is None or брак in cc.КЛАССЫ_БРАКА))
            sql = ("insert into deal_chat_spend (run_id, part, deal_id, model, outcome, defect) "
                   "values ('chat-ctx-t1', 1, %s, 'claude-opus-5-5', %s, %s)")
            if ожидается:
                c.execute(sql, (6000 + i, исход, брак))
            else:
                with pytest.raises(psycopg2.errors.CheckViolation):
                    c.execute(sql, (6000 + i, исход, брак))
        for плохая in ("insert into deal_chat_spend (run_id, part, deal_id, model, outcome) "
                       "values ('chat-ctx-t1', 0, 1, 'claude-opus-5-5', 'отказ')",
                       "insert into deal_chat_spend (run_id, part, deal_id, model, outcome) "
                       "values ('chat-ctx-t1', 1, 1, 'Модель с текстом', 'отказ')",
                       "insert into deal_chat_spend (run_id, part, deal_id, model, outcome, usd) "
                       "values ('chat-ctx-t1', 1, 1, 'claude-opus-5-5', 'отказ', -1)"):
            with pytest.raises(psycopg2.errors.CheckViolation):
                c.execute(плохая)


def test_откат_гейта_помечает_оценки_и_возвращает_курсоры(conn):
    store = cc.База(_соединение(), только_чтение=False)
    try:
        store.записать_оценку(_строка(run="chat-ctx-t1"), _курсор(), _трата())
        до = store.состояние()
        позже = T0 + dt.timedelta(days=1)
        # прогон t2: новая оценка старой сделки и первая — новой
        store.записать_оценку(_строка(lo=13, hi=14, v=_годная([14], state="стоит"), run="chat-ctx-t2",
                                      когда=позже), _курсор(14, когда=позже), _трата(run="chat-ctx-t2"))
        store.записать_оценку(_строка(deal="5002", lo=20, hi=21, v=_годная([21]), run="chat-ctx-t2",
                                      когда=позже), _курсор(21, chat="7002", когда=позже),
                              _трата(deal="5002", run="chat-ctx-t2"))
        assert store.аннулировать("chat-ctx-t2", {"5001": до["5001"], "5002": None}) == 2
        после = store.состояние()
        assert после == до, "курсоры — ровно как до прогона, созданные прогоном строки сняты"
        assert _одно(conn, "select state, msg_to_id from deal_chat_ctx_latest where deal_id = 5001") == ("идёт", 12)
        assert _одно(conn, "select count(*) from deal_chat_ctx_latest where deal_id = 5002") == (0,)
        assert _одно(conn, "select count(*), count(voided_at) from deal_chat_ctx") == (3, 2)
        assert _одно(conn, "select count(*) from deal_chat_spend where run_id = 'chat-ctx-t2'") == (2,), \
            "расход откатом не снимается"
        assert store.аннулировать("chat-ctx-t2", {}) == 0, "повторный откат ничего не меняет"
    finally:
        store.conn.close()


def test_прогон_целиком_на_базе_ломающиеся_чаты_и_откат(conn):
    """Тот же сценарий, что в tests/test_chat_context.py, но запись — в настоящую базу."""
    from tests.test_chat_context import СЕГОДНЯ, Выборочная, _много_чатов
    store = cc.База(_соединение(), только_чтение=False)
    try:
        итоги = []
        for run in ("chat-ctx-r1", "chat-ctx-r2"):
            портал = _много_чатов(2)
            счёт = cc.Counter()
            канд, service = cc.собрать(портал, счёт)
            итоги.append(cc.обработать(портал, store, канд, service, режим="live", лимит=100, бюджет=5.0,
                                       сегодня=СЕГОДНЯ, run_id=run, клиент_модели=Выборочная(),
                                       итог=cc.Итог("live", счёт=счёт), частей=10))
        первый, второй = итоги
        assert первый.код == 1 and первый.счёт["аннулировано"] == 3
        assert второй.код == 0 and второй.счёт["записано"] == 6 and второй.счёт["брак_повторный"] == 2
        assert _одно(conn, "select count(*), count(voided_at) from deal_chat_ctx") == (9, 3)
        assert _одно(conn, "select count(*) from deal_chat_ctx_latest") == (6,)
        assert _одно(conn, "select count(*), max(fail_streak) from deal_chat_state where fail_streak > 0") == (2, 2)
        assert _одно(conn, "select count(*), count(distinct part) > 1 from deal_chat_spend") == (5 + 8, True)
        assert _одно(conn, "select count(*) from deal_chat_state where last_msg_id > 0") == (6,)
    finally:
        store.conn.close()


def test_холостой_без_схемы_и_только_чтение(conn):
    with conn.cursor() as c:
        c.execute("drop table deal_chat_state cascade")
        c.execute("drop table deal_chat_spend cascade")
    store = cc.База(_соединение(только_чтение=True), только_чтение=True)
    try:
        assert store.потрачено_с(T0) == 0.0 and store.схема is False
        assert store.состояние() == {}
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
        assert c.fetchall() == [("deal_chat_ctx", True, True), ("deal_chat_facts", True, True),
                                ("deal_chat_hist", True, True), ("deal_chat_holds", True, True),
                                ("deal_chat_spend", True, True), ("deal_chat_state", True, True)]
        for таблица in ("deal_chat_ctx", "deal_chat_state", "deal_chat_spend", "deal_chat_ctx_latest",
                        "deal_chat_facts", "deal_chat_hist", "deal_chat_holds", "deal_chat_money_latest",
                        "deal_chat_contract_latest", "deal_chat_sourcing_recent", "deal_chat_important_latest"):
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
        c.execute("select has_table_privilege('service_role', %s, 'insert'), "
                  "has_table_privilege('service_role', %s, 'update'), "
                  "has_table_privilege('service_role', %s, 'delete'), "
                  "has_sequence_privilege('service_role', %s, 'usage'), "
                  "has_sequence_privilege('anon', %s, 'usage')",
                  (f"{СХЕМА}.deal_chat_spend",) * 3 + (f"{СХЕМА}.deal_chat_spend_id_seq",) * 2)
        assert c.fetchone() == (True, False, False, True, False), "расход: только чтение и вставка"
        c.execute("select has_column_privilege('service_role', %s, 'voided_at', 'update'), "
                  "has_column_privilege('service_role', %s, 'verdict', 'update')",
                  (f"{СХЕМА}.deal_chat_ctx", f"{СХЕМА}.deal_chat_ctx"))
        assert c.fetchone() == (True, False)
        # v3: факты и история — чтение, вставка и пометка отката; брони — чтение и вставка
        for таблица in ("deal_chat_facts", "deal_chat_hist"):
            c.execute("select has_table_privilege('service_role', %s, 'insert'), "
                      "has_table_privilege('service_role', %s, 'delete'), "
                      "has_column_privilege('service_role', %s, 'voided_at', 'update'), "
                      "has_column_privilege('service_role', %s, 'run_id', 'update')", (f"{СХЕМА}.{таблица}",) * 4)
            assert c.fetchone() == (True, False, True, False), таблица
        c.execute("select has_table_privilege('service_role', %s, 'insert'), "
                  "has_table_privilege('service_role', %s, 'update')", (f"{СХЕМА}.deal_chat_holds",) * 2)
        assert c.fetchone() == (True, False)
        бронь = f"{СХЕМА}.deal_chat_take_budget(text, integer, bigint, text, numeric)"
        c.execute("select has_function_privilege('service_role', %s, 'execute'), "
                  "has_function_privilege('anon', %s, 'execute'), "
                  "has_function_privilege('authenticated', %s, 'execute')", (бронь,) * 3)
        assert c.fetchone() == (True, False, False), "бронь бюджета — только сервисному ключу"
        c.execute("select obj_description(%s::regclass, 'pg_class')", (f"{СХЕМА}.deal_chat_facts",))
        assert c.fetchone()[0] == "deal_chat_schema:v3"
    except psycopg2.Error:
        raise
    finally:
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        for р in созданные:
            c.execute(f"drop role if exists {р}")
        с.close()


# ── v3 (25.10.2026): факты, история, брони ──────────────────────────────────────────

def test_файл_поверх_схемы_v2_и_дважды():
    """Схема v2 (таблицы до раздела V3) с прежними строками — затем файл целиком дважды:
    колонки дописываются, прежний расход становится суточным, оценки chat-ctx-1 на месте."""
    с = _соединение()
    с.autocommit = True
    c = с.cursor()
    try:
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        c.execute(f"create schema {СХЕМА}")
        c.execute(f"set search_path to {СХЕМА}")
        v2 = ФАЙЛ.read_text(encoding="utf-8").split("-- ══ V3")[0]
        for оператор in операторы(v2):
            c.execute(оператор)
        c.execute("insert into deal_chat_spend (run_id, part, deal_id, model, outcome, usd) "
                  "values ('chat-ctx-old', 1, 5001, 'claude-opus-5-5', 'записано', 0.5)")
        c.execute("insert into deal_chat_ctx (deal_id, model, prompt_sha256, method_ver, msg_from_id, msg_to_id, "
                  "verdict, run_id) values (5001, 'claude-opus-5-5', %s, 'chat-ctx-1', 10, 12, %s, 'chat-ctx-old')",
                  ("a" * 64, json.dumps(_годная([12]), ensure_ascii=False)))
        c.execute("insert into deal_chat_state (deal_id, chat_id, last_msg_id, run_id, method_ver) "
                  "values (5001, 7001, 12, 'chat-ctx-old', 'chat-ctx-1')")
        for _ in range(2):
            _применить(c)
        c.execute("select kind, hold_id from deal_chat_spend")
        assert c.fetchall() == [("сутки", None)]
        c.execute("select hist_state, hist_fail_streak from deal_chat_state")
        assert c.fetchall() == [(None, 0)]
        c.execute("select count(*) from deal_chat_important_latest")
        assert c.fetchone() == (0,), "оценка chat-ctx-1 — не вызов с «главным»"
        c.execute("select count(*) from deal_chat_ctx_latest")
        assert c.fetchone() == (1,)
    finally:
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        с.close()


def _корпус_фактов() -> list[tuple]:
    """(вид, данные, сообщение, доказательства, ключи) — годные и испорченные по одному полю."""
    деньги = {"amount": 1250000.0, "currency": "RUB", "money_kind": "итог", "side": "продажа", "vat": "без_ндс",
              "date": None}
    договор = {"status": "подписан", "side": "заказчик", "where": "эдо", "spec_no": "3/2026"}
    сорсинг = {"signal": "кп_получено", "author_role": "сорсер"}
    главное = {"kind": "поставщик", "severity": "высокая"}
    out = [("деньги", {**деньги, k: x}, 10, [10], []) for k, список in (
        ("currency", cc.ВАЛЮТЫ), ("money_kind", cc.ВИДЫ_ДЕНЕГ), ("side", cc.СТОРОНЫ_ДЕНЕГ), ("vat", cc.НДС))
        for x in список]
    out += [("деньги", {**деньги, "date": "2026-10-24"}, 10, [10], []), ("деньги", {**деньги, "amount": 0.01}, 10, [10], [])]
    out += [("договор", {**договор, k: x}, 12, [10, 12], []) for k, список in (
        ("status", cc.СТАТУСЫ_ДОГОВОРА), ("side", cc.СТОРОНЫ_ДОГОВОРА), ("where", cc.ГДЕ_ДОГОВОР)) for x in список]
    out += [("договор", {**договор, "spec_no": x}, 12, [12], []) for x in (None, "№3", "12/2026-A", "СП.4")]
    out += [("сорсинг", {**сорсинг, k: x}, 11, [11], []) for k, список in (
        ("signal", cc.СИГНАЛЫ), ("author_role", cc.РОЛИ_АВТОРА)) for x in список]
    out += [("главное", {**главное, k: x}, 11, [11], []) for k, список in (
        ("kind", cc.ВИДЫ_ГЛАВНОГО), ("severity", cc.ВАЖНОСТИ)) for x in список]
    out += [("главное", главное, None, [], [k]) for k in cc.КЛЮЧИ_СВОДКИ]
    out += [("главное", главное, 12, [10, 12], ["stage", "o1"])]
    плохие = [
        ("деньги", {**деньги, "currency": "RUR"}, 10, [10], []), ("деньги", {**деньги, "amount": -1}, 10, [10], []),
        ("деньги", {**деньги, "amount": 0}, 10, [10], []), ("деньги", {**деньги, "amount": "1250000"}, 10, [10], []),
        ("деньги", {**деньги, "amount": 1e13}, 10, [10], []), ("деньги", {**деньги, "date": "2026-02-30"}, 10, [10], []),
        ("деньги", {**деньги, "date": "24.10.2026"}, 10, [10], []), ("деньги", {**деньги, "note": "текст"}, 10, [10], []),
        ("деньги", {k: x for k, x in деньги.items() if k != "vat"}, 10, [10], []),
        ("деньги", деньги, 10, [10, 11], []), ("деньги", деньги, 10, [11], []), ("деньги", деньги, None, [], []),
        ("деньги", деньги, 10, [10], ["stage"]),
        ("договор", {**договор, "spec_no": "спец 3"}, 12, [12], []),
        ("договор", {**договор, "spec_no": "без-цифр"}, 12, [12], []),
        ("договор", {**договор, "spec_no": "79161234567"}, 12, [12], []),
        ("договор", {**договор, "spec_no": "x" * 21 + "1"}, 12, [12], []),
        ("договор", договор, 10, [10, 12], []), ("договор", договор, 12, [10, 12, 13, 14, 15, 16], []),
        ("договор", {**договор, "status": "почти"}, 12, [12], []),
        ("сорсинг", {**сорсинг, "author_role": "клиент"}, 11, [11], []), ("сорсинг", {"signal": "отказ"}, 11, [11], []),
        ("главное", {**главное, "severity": "низкая"}, 11, [11], []), ("главное", главное, None, [], []),
        ("главное", главное, None, [], ["o9"]), ("главное", главное, 13, [11], []),
        ("главное", главное, None, [], ["stage", "stage"]), ("главное", {**главное, "text": "x"}, 11, [11], []),
        ("главное", главное, 11, [11, 11], []), ("главное", главное, None, [0], []),
        ("иное", главное, 11, [11], []), ("деньги", [], 10, [10], []),
        # ревью 25.10.2026: одни прошлые выводы модели — не доказательство
        ("главное", главное, None, [], ["prev", "facts", "imp"]), ("главное", главное, None, [], ["imp"]),
    ]
    out += [("главное", главное, None, [], ["imp", "stage"]), ("главное", главное, 11, [11], ["prev"])]
    return out + плохие


def test_факт_ок_python_и_база_согласны(conn):
    расхождения, годных, плохих = [], 0, 0
    for вид, данные, msg, ids, keys in _корпус_фактов():
        py = cc.факт_ок(вид, copy.deepcopy(данные), msg, ids, keys)
        (sql,) = _одно(conn, "select deal_chat_fact_ok(%s, %s::jsonb, %s, %s::bigint[], %s::text[])",
                       вид, json.dumps(данные, ensure_ascii=False), msg, ids, keys)
        if py != sql:
            расхождения.append((вид, данные, msg, ids, keys, py, sql))
        годных += py
        плохих += not py
    assert not расхождения, расхождения
    assert годных >= 60 and плохих >= 30


ТЕКСТЫ_ГЛАВНОГО = [
    "Срок клиента 24.10 сорван, поставщик молчит 5 дней", "Нужна оплата аванса до 2026-10-30",
    "Трубы из Европы задерживаются на таможне", "Клиент недоволен переносом отгрузки на 30.10.2026",
    "Сумма в чате выше файла на 17 %", "Договор у клиента на подписи третью неделю", "x" * 160,
    "Клиент согласовал 1 250 000 за поставку", "Сумма в рублях выросла", "Ответ в 2 млн", "Цена 50k за позицию",
    "Письмо на test@example.invalid", "Смотри [ссылка]", "Сайт пример-компании.ru не открывается",
    "Счёт на 12 500 EUR", "Оплата в евро", "Цена 1250 руб.", "Звонок 8 912 345 67 89", "x" * 161, "  ", "",
    "Цена ₽ выросла", "Оплата в USD задержана", "Доллары не пришли", "В юанях дешевле", "Партия 1.250 шт",
    "Платёж 3 тыс. не прошёл", "срок 10.2026", "РУБЛИ не пришли", "Поставка ЕВРО-фуры",
    "https://example.invalid", "Читай www.example.invalid", "Заказ 2026 года сорван", "заказ 10-15 дней",
    # ревью 25.10.2026
    "смотри bit.ly/abc", "файл 1drv.ms/y", "портал portal.example.pro/z", "сайт пример.biz", "Сайт EXAMPLE.CO",
    "Клиент согласовал полтора миллиона", "Аванс два млн не пришёл", "Сделка на три миллиарда",
    "Млн — не сумма?", "т.е. клиент ждёт", "Поставщик ответил, ждём счёт до 30.10",
    # ревью 28.10.2026: телефоны и суммы без валюты
    "Позвонить клиенту +7 (912) 345-67-89", "Позвонить 8-912-345-67-89", "Позвонить 345-67-89",
    "Оплата 500 т.р. не пришла", "Оплата 500 тр", "Долг 500к", "Долг двести тысяч", "Заказ 10-15 дней",
    "Срок 2026-10-30 и 2026-10-31 сорван", "Нужно 5 труб к пятнице", "Шаг 3-4 за 2 дня",
]
ЦИТАТЫ = [
    "Итог по КП 1 250 000 руб. без НДС", "а" * 200, "Договор у [имя] на подписи", "перезвоню [телефон]",
    "а" * 201, "", "пишите на почту x@y", "смотри http://x", "www.x.ru", "x [USER=5]y[/USER]",
    "звоните 8 912 345 67 89", "+7 (912) 345-67-89", "дата 2026-10-24 и 2026-10-25", "сумма 1 250 000 000 руб",
    "номер 123456789", "Иван [имя] — сотрудник#2 (КАМ)",
    # ревью 25.10.2026
    "смотри bit.ly/abc", "файл 1drv.ms/y", "портал portal.example.pro/z", "сайт пример.biz", "т.р./шт — 1 250",
    "цена 12.50 EUR",
]
НОМЕРА_СПЕЦИФИКАЦИЙ = [None, "3", "3/2026", "СП-4.1", "Ё12", "a" * 19 + "1", "a" * 20 + "1", "спец 3", "без",
                       "1234567890", "№3", "12_A"]


def test_тексты_python_и_база_согласны(conn):
    расхождения = []
    for s in ТЕКСТЫ_ГЛАВНОГО:
        (sql,) = _одно(conn, "select coalesce(deal_chat_statement_ok(%s), false)", s)
        if cc.текст_главного_ок(s) != sql:
            расхождения.append(("главное", s, sql))
    for q in ЦИТАТЫ:
        (sql,) = _одно(conn, "select coalesce(deal_chat_quote_ok(%s), false)", q)
        if cc.цитата_ок(q) != sql:
            расхождения.append(("цитата", q, sql))
    for n in НОМЕРА_СПЕЦИФИКАЦИЙ:
        (sql,) = _одно(conn, "select coalesce(deal_chat_spec_ok(%s), false)", n)
        if cc.номер_спецификации_ок(n) != sql:
            расхождения.append(("спецификация", n, sql))
    assert not расхождения, расхождения
    assert sum(map(cc.текст_главного_ок, ТЕКСТЫ_ГЛАВНОГО)) >= 6 and sum(map(cc.цитата_ок, ЦИТАТЫ)) >= 4


def _факт(вид="деньги", msg=11, **kw) -> dict:
    данные = {"деньги": {"amount": 1250000.0, "currency": "RUB", "money_kind": "итог", "side": "продажа",
                         "vat": "без_ндс", "date": None},
              "договор": {"status": "подписан", "side": "заказчик", "where": "эдо", "spec_no": "№3"},
              "сорсинг": {"signal": "ответил", "author_role": "сорсер"},
              "главное": {"kind": "поставщик", "severity": "высокая"}}[вид]
    f = {"kind": вид, "pass": "сутки", "msg_id": msg, "msg_at": T0, "evidence_msg_ids": [msg], "evidence_keys": [],
         "evidence_vals": None, "data": данные, "quote": "Итог 1 250 000 руб. — согласовано" if вид == "деньги" else None,
         "statement": None, "confidence": None}
    if вид == "главное":
        f.update(statement="Поставщик сорвал отгрузку 24.10, нужен новый срок", confidence=0.7,
                 evidence_keys=["o1"], evidence_vals={"o1": "n=1; phase=производство"})
    f.update(kw)
    return f


def test_факты_источник_отрезок_дописывание_повторы(conn):
    import psycopg2
    store = cc.База(_соединение(), только_чтение=False)
    try:
        assert store.записать_оценку(_строка(lo=10, hi=12), _курсор(), _трата(),
                                     [_факт(), _факт("договор", 12), _факт("сорсинг", 10), _факт("главное", 12)]) == 4
        (ctx_id,) = _одно(conn, "select id from deal_chat_ctx")
        assert _одно(conn, "select count(*), count(distinct ctx_id), min(pass) from deal_chat_facts") == (4, 1, "сутки")
        assert _одно(conn, "select data->>'currency', quote from deal_chat_facts where kind = 'деньги'") == (
            "RUB", "Итог 1 250 000 руб. — согласовано"), "кириллица в jsonb и цитате не портится"
        # доказательство 11 — вне отрезка своей оценки 13–14: триггер не пускает, курсор стоит
        with pytest.raises(psycopg2.errors.RaiseException):
            store.записать_оценку(_строка(lo=13, hi=14, v=_годная([14]), run="chat-ctx-t3"), _курсор(14),
                                  _трата(run="chat-ctx-t3"), [_факт(msg=11)])
        assert store.состояние()["5001"]["last_msg_id"] == 12
        # тот же факт другим прогоном не пишется (история и сутки его не задвоят), новый — пишется
        assert store.записать_оценку(_строка(lo=11, hi=14, v=_годная([14]), run="chat-ctx-t4"), _курсор(14),
                                     _трата(run="chat-ctx-t4"), [_факт(msg=11), _факт(msg=13)]) == 1
        assert _одно(conn, "select count(*) from deal_chat_ctx") == (2,)
    finally:
        store.conn.close()
    with conn.cursor() as c:
        def плохо(sql, *p, ошибка=psycopg2.errors.CheckViolation):
            with pytest.raises(ошибка):
                c.execute(sql, p)
        вставка = ("insert into deal_chat_facts (deal_id, run_id, method_ver, pass, ctx_id, hist_id, kind, msg_id, "
                   "msg_at, evidence_msg_ids, data, quote, statement, confidence) values (5001, 'chat-ctx-t1', "
                   "'chat-ctx-2', %s, %s, %s, %s, 11, now(), '{11}', %s::jsonb, %s, %s, %s)")
        данные = json.dumps(_факт()["data"], ensure_ascii=False)
        плохо(вставка, "сутки", ctx_id, 1, "деньги", данные, None, None, None)        # два источника
        плохо(вставка, "сутки", None, None, "деньги", данные, None, None, None)        # ни одного
        плохо(вставка, "история", ctx_id, None, "деньги", данные, None, None, None)    # проход не тот
        плохо(вставка, "сутки", ctx_id, None, "деньги", данные, "звоните 8 912 345 67 89", None, None)
        плохо(вставка, "сутки", ctx_id, None, "деньги", данные, "x" * 201, None, None)
        плохо(вставка, "сутки", ctx_id, None, "деньги", данные, None, "строка у денег", None)
        плохо(вставка, "сутки", ctx_id, None, "главное", '{"kind": "поставщик", "severity": "высокая"}', None,
              "Клиент согласовал 1 250 000", 0.5)
        плохо(вставка, "сутки", ctx_id, None, "главное", '{"kind": "поставщик", "severity": "высокая"}', None,
              "Поставщик молчит", None)
        # строка оценки другого прогона — не своя
        плохо(вставка.replace("'chat-ctx-t1'", "'chat-ctx-t9'"), "сутки", ctx_id, None, "деньги", данные, None,
              None, None, ошибка=psycopg2.errors.RaiseException)
        # только дописывается: правка и удаление запрещены, пометка — один раз
        for sql in ("update deal_chat_facts set data = data || '{\"vat\": \"с_ндс\"}'",
                    "update deal_chat_facts set quote = null", "delete from deal_chat_facts",
                    "truncate deal_chat_facts"):
            плохо(sql, ошибка=psycopg2.errors.RaiseException)
        c.execute("update deal_chat_facts set voided_at = now() where kind = 'деньги' and msg_id = 11")
        плохо("update deal_chat_facts set voided_at = null where kind = 'деньги' and msg_id = 11",
              ошибка=psycopg2.errors.RaiseException)
    # помеченный факт не мешает записать его снова
    store = cc.База(_соединение(), только_чтение=False)
    try:
        assert store.записать_оценку(_строка(lo=11, hi=15, v=_годная([15]), run="chat-ctx-t5"), _курсор(15),
                                     _трата(run="chat-ctx-t5"), [_факт(msg=11)]) == 1
    finally:
        store.conn.close()


def _бронь(conn, вид, потолок, run="chat-ctx-t1"):
    return _одно(conn, "select deal_chat_take_budget(%s, 1, 5001, %s, %s)", run, вид, потолок)[0]


def test_бронь_бюджета_пределы_статей_и_неоплаченная_бронь(conn):
    import psycopg2
    with conn.cursor() as c:
        # вчерашний (по Москве) расход суток не в счёт, расход истории — в счёт всегда
        c.execute("insert into deal_chat_spend (at, run_id, part, deal_id, model, outcome, usd, kind) values "
                  "(now() - interval '2 days', 'chat-ctx-old', 1, 1, 'claude-opus-5-5', 'записано', 4.9, 'сутки'), "
                  "(now() - interval '400 days', 'chat-ctx-old', 1, 1, 'claude-opus-5-5', 'записано', 15, 'история')")
    первая = _бронь(conn, "сутки", 2.0)
    assert первая is not None and _бронь(conn, "сутки", 2.0) is not None
    assert _бронь(conn, "сутки", 1.5) is None, "2 + 2 + 1,5 > 5: брони неоплаченные — считаются по потолку"
    # первая оплачена дешевле потолка — остаток вернулся
    with conn.cursor() as c:
        c.execute("insert into deal_chat_spend (run_id, part, deal_id, model, outcome, usd, kind, hold_id) "
                  "values ('chat-ctx-t1', 1, 5001, 'claude-opus-5-5', 'записано', 0.3, 'сутки', %s)", (первая,))
    assert _бронь(conn, "сутки", 1.5) is not None, "0,3 + 2 + 1,5 ≤ 5"
    assert _бронь(conn, "сутки", 1.5) is None
    # статья «история»: 15 уже ушло за всё время — ещё 5, и не больше; суточная не задета
    assert _бронь(conn, "история", 4.0) is not None and _бронь(conn, "история", 1.5) is None
    assert _бронь(conn, "история", 1.0) is not None
    with conn.cursor() as c:
        for плохо in (("сутки", 0), ("месяц", 1.0)):
            with pytest.raises(psycopg2.errors.RaiseException):
                c.execute("select deal_chat_take_budget('chat-ctx-t1', 1, 5001, %s, %s)", плохо)
        with pytest.raises(psycopg2.errors.RaiseException):
            c.execute("update deal_chat_holds set ceiling_usd = 0.01")
        with pytest.raises(psycopg2.errors.UniqueViolation):     # бронь оплачивается один раз
            c.execute("insert into deal_chat_spend (run_id, part, deal_id, model, outcome, kind, hold_id) "
                      "values ('chat-ctx-t1', 1, 5001, 'claude-opus-5-5', 'записано', 'сутки', %s)", (первая,))
        with pytest.raises(psycopg2.errors.CheckViolation):
            c.execute("insert into deal_chat_spend (run_id, part, deal_id, model, outcome, kind) "
                      "values ('chat-ctx-t1', 1, 5001, 'claude-opus-5-5', 'записано', 'месяц')")


def test_бронь_двух_соединений_разом_предел_не_превышает(conn):
    import threading
    итоги: list = []

    def брать():
        с = _соединение()
        с.autocommit = True
        try:
            for _ in range(10):
                with с.cursor() as c:
                    c.execute("select deal_chat_take_budget('chat-ctx-t1', 1, 5001, 'сутки', 0.6)")
                    итоги.append(c.fetchone()[0])
        finally:
            с.close()
    потоки = [threading.Thread(target=брать) for _ in range(2)]
    for п in потоки:
        п.start()
    for п in потоки:
        п.join()
    взято = [x for x in итоги if x is not None]
    assert len(итоги) == 20 and len(взято) == 8, "0,6 × 8 = 4,8 ≤ 5 < 0,6 × 9"
    assert float(_одно(conn, "select sum(ceiling_usd) from deal_chat_holds")[0]) <= 5.0


def test_главное_последнего_вызова_и_пустой_вызов_гасит(conn):
    store = cc.База(_соединение(), только_чтение=False)
    try:
        главное = _факт("главное", 12)
        store.записать_оценку(_строка(lo=10, hi=12, когда=T0), _курсор(), _трата(),
                              [главное, _факт("главное", 11, statement="Клиент недоволен сроком, нужен звонок",
                                              data={"kind": "клиент_недоволен", "severity": "средняя"})])
        assert _одно(conn, "select count(*), count(kind) from deal_chat_important_latest") == (2, 2)
        прошлые = store.факты(["5001"])["5001"]
        assert прошлые["imp"]["pass"] == "сутки" and len(прошлые["imp"]["items"]) == 2
        assert прошлые["imp"]["conf"] == pytest.approx(0.7) and "money" not in прошлые
        # следующий вызов без «главного» — прежние пункты погашены: строка вызова без пунктов
        store.записать_оценку(_строка(lo=13, hi=14, v=_годная([14]), run="chat-ctx-t2", когда=T0 + dt.timedelta(hours=1)),
                              _курсор(14), _трата(run="chat-ctx-t2"), [])
        assert _одно(conn, "select count(*), count(kind) from deal_chat_important_latest") == (1, 0)
        assert store.факты(["5001"])["5001"]["imp"]["items"] == []
        # он помечен откатом — снова видны пункты прежнего
        store.аннулировать("chat-ctx-t2", {"5001": None})
        assert _одно(conn, "select count(kind) from deal_chat_important_latest") == (2,)
    finally:
        store.conn.close()
    with conn.cursor() as c:                         # оценка chat-ctx-1 — не вызов с «главным»
        c.execute("insert into deal_chat_ctx (deal_id, assessed_at, model, prompt_sha256, method_ver, msg_from_id, "
                  "msg_to_id, verdict, run_id) values (5001, %s, 'claude-opus-5-5', %s, 'chat-ctx-1', 20, 21, %s, "
                  "'chat-ctx-t6')", (T0 + dt.timedelta(days=1), "b" * 64, json.dumps(_годная([21]), ensure_ascii=False)))
    assert _одно(conn, "select count(kind) from deal_chat_important_latest") == (2,)


def _строка_истории(deal="5001", lo=1, hi=9, run="chat-ctx-h1", когда=T0) -> dict:
    return {"deal_id": deal, "model": "claude-opus-5-5", "prompt_sha256": "c" * 64, "method_ver": cc.METHOD_VER,
            "msg_from_id": lo, "msg_to_id": hi, "msgs": hi - lo + 1, "pages": 1, "reached": "конец_чата",
            "digest_sha256": "d" * 64, "tokens_in": 5000, "tokens_out": 900, "usd": 0.05, "run_id": run,
            "assessed_at": когда}


def test_история_строка_курсор_и_откат_на_базе(conn):
    import psycopg2
    store = cc.База(_соединение(), только_чтение=False)
    try:
        store.записать_оценку(_строка(lo=10, hi=12), _курсор(), _трата(), [_факт("деньги", 11)])
        до = store.состояние()["5001"]
        assert до["hist_state"] is None and до["hist_fail_streak"] == 0
        бронь = store.взять_бюджет("chat-ctx-h1", 1, "5001", "история", 0.5)
        трата = dict(_трата(run="chat-ctx-h1"), kind="история", hold_id=бронь)
        # тот же факт, что знала суточная оценка, — не задваивается; новые — пишутся с hist_id
        n = store.записать_историю(_строка_истории(hi=11), [dict(_факт("деньги", 11), **{"pass": "история"}),
                                                            dict(_факт("сорсинг", 5), **{"pass": "история"})],
                                   {"hist_low_id": 1, "hist_state": "конец_чата", "hist_at": T0}, трата)
        assert n == 1
        s = store.состояние()["5001"]
        assert (s["hist_state"], s["hist_low_id"], s["hist_run_id"], s["last_msg_id"]) == ("конец_чата", 1,
                                                                                          "chat-ctx-h1", 12)
        assert _одно(conn, "select kind, hold_id is not null from deal_chat_spend where run_id = 'chat-ctx-h1'") == (
            "история", True)
        assert store.потрачено_с(None, "история") == pytest.approx(0.0108)
        # суточная оценка chat-ctx-2 той же сделки важнее истории (ревью 25.10.2026): история читает
        # старые сообщения и свежее «главное» не гасит
        assert _одно(conn, "select pass from deal_chat_important_latest where deal_id = 5001") == ("сутки",)
        # неудача истории: своя серия, суточная очередь стоит
        store.записать_неудачу(dict(_трата(run="chat-ctx-h1"), kind="история", outcome="брак", defect="money"),
                               _неудача(run="chat-ctx-h1"), True)
        s2 = store.состояние()["5001"]
        assert s2["hist_fail_streak"] == 1 and s2["fail_streak"] == 0 and s2["checked_at"] == до["checked_at"]
        # откат: строка истории и её факты помечены, курсор истории — как до прогона
        assert store.аннулировать("chat-ctx-h1", {}, {"5001": до}) == 1
        s3 = store.состояние()["5001"]
        assert s3["hist_state"] is None and s3["hist_low_id"] is None and s3["hist_run_id"] is None
        assert _одно(conn, "select count(*) filter (where voided_at is null) from deal_chat_facts "
                           "where hist_id is not null") == (0,)
        assert _одно(conn, "select count(*) from deal_chat_hist where voided_at is not null") == (1,)
        # курсора сделки нет — строка истории не пишется вовсе
        with pytest.raises(RuntimeError):
            store.записать_историю(_строка_истории(deal="5999", run="chat-ctx-h2"), [],
                                   {"hist_low_id": 1, "hist_state": "предел", "hist_at": T0},
                                   dict(_трата(deal="5999", run="chat-ctx-h2"), kind="история"))
        assert _одно(conn, "select count(*) from deal_chat_hist where deal_id = 5999") == (0,)
        with conn.cursor() as c:
            with pytest.raises(psycopg2.errors.CheckViolation):
                c.execute("update deal_chat_state set hist_state = 'почти' where deal_id = 5001")
    finally:
        store.conn.close()


def test_прогон_истории_целиком_на_базе():
    """Тот же сценарий, что tests/test_chat_context.py (история с годным ответом), — запись в базу."""
    from tests.test_chat_context import СЕГОДНЯ, Модель, _курсоры, _портал_истории, ответ_с_фактами  # noqa: F401
    import psycopg2
    с = _соединение()
    с.autocommit = True
    c = с.cursor()
    try:
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        c.execute(f"create schema {СХЕМА}")
        c.execute(f"set search_path to {СХЕМА}")
        for _ in range(2):
            _применить(c)
        сделки = [("8812601", "770601", 120, "2026-10-01"), ("8812602", "770602", 30, "2026-10-02")]
        for d, чат, n, _c in сделки:
            c.execute("insert into deal_chat_state (deal_id, chat_id, last_msg_id, run_id, method_ver) "
                      "values (%s, %s, %s, 'chat-ctx-old', 'chat-ctx-1')", (int(d), int(чат), n))
        store = cc.База(_соединение(), только_чтение=False)
        try:
            портал = _портал_истории(сделки)
            счёт = cc.Counter()
            канд, service = cc.собрать(портал, счёт)
            итог = cc.обработать(портал, store, канд, service, режим="live", лимит=60, бюджет=20.0, сегодня=СЕГОДНЯ,
                                 run_id="chat-ctx-h1", клиент_модели=Модель(), итог=cc.Итог("live", счёт=счёт),
                                 проход="history", имена=портал.users())
        finally:
            store.conn.close()
        assert итог.код == 0 and итог.счёт["записано"] == 2
        c.execute("select count(*), sum(msgs), min(reached) from deal_chat_hist")
        assert c.fetchone() == (2, 150, "конец_чата")
        c.execute("select count(*), count(distinct hold_id), min(kind), max(kind) from deal_chat_spend")
        assert c.fetchone() == (2, 2, "история", "история")
        c.execute("select count(*) from deal_chat_state where hist_state = 'конец_чата' and last_msg_id in (120, 30)")
        assert c.fetchone() == (2,)
    except psycopg2.Error:
        raise
    finally:
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        с.close()



# ── ревью chat-ctx-2 (25.10.2026) ────────────────────────────────────────────────────────

def test_дата_суммы_python_и_база_согласны(conn):
    """Дата суммы — не позже дня сообщения по Москве: обещанная оплата — не пришедшая."""
    ночь = dt.datetime(2026, 10, 18, 22, 30, tzinfo=dt.timezone.utc)          # по Москве уже 19.10
    случаи = [("деньги", {"date": None}, ночь), ("деньги", {"date": "2026-10-19"}, ночь),
              ("деньги", {"date": "2026-10-20"}, ночь), ("деньги", {"date": "2026-10-18"}, ночь),
              ("деньги", {"date": "2026-10-18"}, None), ("договор", {"date": "2099-01-01"}, ночь),
              ("деньги", {}, None)]
    for вид, d, at in случаи:
        (sql,) = _одно(conn, "select deal_chat_money_date_ok(%s, %s::jsonb, %s)", вид, json.dumps(d), at)
        assert cc.дата_денег_ок(вид, d, at) == sql, (вид, d, at, sql)
    assert sum(cc.дата_денег_ок(*x) for x in случаи) == 5


def test_факт_с_датой_позже_сообщения_база_не_пускает(conn):
    import psycopg2
    store = cc.База(_соединение(), только_чтение=False)
    try:
        будущее = dict(_факт(), data={**_факт()["data"], "date": "2026-12-15"})
        with pytest.raises(psycopg2.errors.CheckViolation):
            store.записать_оценку(_строка(lo=10, hi=12), _курсор(), _трата(), [будущее])
        assert "5001" not in store.состояние(), "транзакция целиком откатилась"
    finally:
        store.conn.close()


def test_бронь_считает_расход_по_метке_брони_у_полуночи(conn):
    """Вызов, забронированный после полуночи по Москве, а записанный с меткой раннера до неё, —
    в сутках брони (ревью 25.10.2026): суточный предел не обходится одним вызовом у полуночи."""
    бронь = _бронь(conn, "сутки", 0.5)
    with conn.cursor() as c:
        c.execute("insert into deal_chat_spend (at, run_id, part, deal_id, model, outcome, usd, kind, hold_id) "
                  "values (now() - interval '3 days', 'chat-ctx-t1', 1, 5001, 'claude-opus-5-5', 'записано', 4.8, "
                  "'сутки', %s)", (бронь,))
    assert _бронь(conn, "сутки", 0.5) is None, "4,8 — в сутках брони, ещё 0,5 не помещается"
    store = cc.База(_соединение(), только_чтение=True)
    try:
        assert store.потрачено_с(cc.начало_суток(), "сутки") == pytest.approx(4.8)
        assert store.брони_с(cc.начало_суток(), "сутки") == 0
    finally:
        store.conn.close()
    assert _бронь(conn, "сутки", 0.1) is not None
    store = cc.База(_соединение(), только_чтение=True)
    try:
        assert store.брони_с(cc.начало_суток(), "сутки") == pytest.approx(0.1), "неоплаченная бронь — занята"
    finally:
        store.conn.close()


def test_главное_вида_предложено_сводка_и_дата_доказательства(conn):
    store = cc.База(_соединение(), только_чтение=False)
    try:
        строка = dict(_строка(lo=10, hi=12), imp_proposed=2, with_digest=False)
        store.записать_оценку(строка, _курсор(), _трата(), [_факт("главное", 12)])
        r = _одно(conn, "select pass, proposed, with_digest, count(kind) over (), ev_at = msg_at "
                        "from deal_chat_important_latest where deal_id = 5001")
        assert r == ("сутки", 2, False, 1, True), "модель вернула два пункта, записан один"
        # вызов без «главного»: пунктов нет, но proposed хранит, сколько их было до проверок
        store.записать_оценку(dict(_строка(lo=13, hi=14, v=_годная([14]), run="chat-ctx-t2",
                                           когда=T0 + dt.timedelta(hours=1)), imp_proposed=1, with_digest=True),
                              _курсор(14), _трата(run="chat-ctx-t2"), [])
        assert _одно(conn, "select proposed, with_digest, kind, ev_at is not null from deal_chat_important_latest "
                           "where deal_id = 5001") == (1, True, None, True)
        # история без суточной оценки — видна; при суточной — нет
        c = store.conn.cursor()
        c.execute("insert into deal_chat_state (deal_id, chat_id, last_msg_id, run_id, method_ver) "
                  "values (5002, 7002, 40, 'chat-ctx-old', 'chat-ctx-1')")
        store.conn.commit()
        бронь = store.взять_бюджет("chat-ctx-h1", 1, "5002", "история", 0.5)
        store.записать_историю(dict(_строка_истории(deal="5002"), imp_proposed=0, with_digest=False), [],
                               {"hist_low_id": 1, "hist_state": "конец_чата", "hist_at": T0},
                               dict(_трата(deal="5002", run="chat-ctx-h1"), kind="история", hold_id=бронь))
        assert _одно(conn, "select pass, proposed from deal_chat_important_latest where deal_id = 5002") == (
            "история", 0)
        with conn.cursor() as cc_:
            import psycopg2
            with pytest.raises(psycopg2.errors.CheckViolation):
                cc_.execute("insert into deal_chat_ctx (deal_id, model, prompt_sha256, method_ver, msg_from_id, "
                            "msg_to_id, verdict, run_id, imp_proposed) values (5003, 'claude-opus-5-5', %s, "
                            "'chat-ctx-2', 1, 1, %s, 'chat-ctx-t9', 51)",
                            ("e" * 64, json.dumps(_годная([1]), ensure_ascii=False)))
    finally:
        store.conn.close()


def test_хранилище_истории_и_отката_фактов_на_базе(conn):
    """отметить_историю, аннулировать_факты, нижние_границы, выход_p90 — на настоящей базе."""
    store = cc.База(_соединение(), только_чтение=False)
    try:
        store.записать_оценку(_строка(lo=10, hi=12), _курсор(), _трата(), [_факт(), _факт("сорсинг", 10)])
        assert store.нижние_границы(["5001", "5999"]) == {"5001": 10}
        store.отметить_историю("5001", "chat-ctx-h1", T0, None)
        s = store.состояние()["5001"]
        assert s["hist_fail_streak"] == 1 and s["hist_state"] is None and s["last_msg_id"] == 12
        store.отметить_историю("5001", "chat-ctx-h1", T0, {"hist_low_id": 3, "hist_state": "конец_чата"})
        s = store.состояние()["5001"]
        assert (s["hist_state"], s["hist_low_id"], s["hist_fail_streak"], s["hist_run_id"]) == (
            "конец_чата", 3, 0, "chat-ctx-h1")
        assert store.аннулировать_факты("chat-ctx-t1") == 2
        assert _одно(conn, "select count(*) filter (where voided_at is null) from deal_chat_ctx") == (1,), \
            "оценка хода не помечена"
        assert store.состояние()["5001"]["last_msg_id"] == 12, "курсор стоит"
        assert store.выход_p90("сутки") is None, "замера ещё нет: меньше пяти записанных вызовов с бронью"
        with conn.cursor() as c:
            for i in range(6):
                c.execute("select deal_chat_take_budget('chat-ctx-t1', 1, 5001, 'сутки', 0.1)")
                (h,) = c.fetchone()
                c.execute("insert into deal_chat_spend (run_id, part, deal_id, model, outcome, tokens_out, usd, kind, "
                          "hold_id) values ('chat-ctx-t1', 1, 5001, 'claude-opus-5-5', 'записано', %s, 0.01, 'сутки', %s)",
                          (1000 * (i + 1), h))
        assert store.выход_p90("сутки") == 6000
    finally:
        store.conn.close()


def test_прогон_на_базе_факт_отвергнут_базой_оценка_без_фактов():
    """База отвергла факт (триггер отрезка) — та же оценка пишется без фактов, курсор идёт."""
    from tests.test_chat_context import СЕГОДНЯ, Модель, Портал, сообщение  # noqa: F401
    с = _соединение()
    с.autocommit = True
    c = с.cursor()
    try:
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        c.execute(f"create schema {СХЕМА}")
        c.execute(f"set search_path to {СХЕМА}")
        _применить(c)
        портал = Портал({"770901": [сообщение(21, "4410", "Поставщик прислал КП")]},
                        real=[{"ID": "8812901", "CATEGORY_ID": "0", "STAGE_ID": "C0:WORK", "STAGE_SEMANTIC_ID": "P",
                               cc.chat_activity.CHAT_FIELD: "770901"}], pre=[])
        настоящая = cc.сверить_факты

        def мимо_python(*a, **kw):                   # факт, который Python пропустил бы, а база — нет
            out = настоящая(*a, **kw)
            return out + [{"kind": "сорсинг", "pass": "сутки", "msg_id": 999, "msg_at": T0, "evidence_msg_ids": [999],
                           "evidence_keys": [], "evidence_vals": None,
                           "data": {"signal": "ответил", "author_role": "сорсер"}, "quote": None, "statement": None,
                           "confidence": None}]
        cc.сверить_факты = мимо_python
        store = cc.База(_соединение(), только_чтение=False)
        try:
            счёт = cc.Counter()
            канд, service = cc.собрать(портал, счёт)
            итог = cc.обработать(портал, store, канд, service, режим="live", лимит=10, бюджет=5.0, сегодня=СЕГОДНЯ,
                                 run_id="chat-ctx-r1", клиент_модели=Модель(), итог=cc.Итог("live", счёт=счёт),
                                 имена=портал.users())
        finally:
            cc.сверить_факты = настоящая
            store.conn.close()
        assert итог.код == 0 and итог.счёт["запись_без_фактов"] == 1 and итог.счёт["факт_снят:база:P0001"] == 1
        c.execute("select count(*), max(imp_proposed), bool_and(with_digest = false) from deal_chat_ctx")
        assert c.fetchone() == (1, 0, True)
        c.execute("select last_msg_id from deal_chat_state where deal_id = 8812901")
        assert c.fetchone() == (21,)
        c.execute("select count(*) from deal_chat_facts")
        assert c.fetchone() == (0,)
    finally:
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        с.close()
