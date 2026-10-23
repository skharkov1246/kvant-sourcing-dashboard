"""Чат и договор сделки во вкладке «Реализация» (dealctx.py, решение владельца 23.10.2026).

Корпус придуман (CLAUDE.md, правило 18): сделки ContractsStub и свод fixture.realctx_raw.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import dealctx  # noqa: E402
from tests import fixture  # noqa: E402

NOW = dt.datetime.fromisoformat(fixture.CT_NOW)
TODAY = fixture.CT_TODAY


def _a(**kw) -> dict:
    """Оценка в разобранном виде (как после dealctx.clean)."""
    raw = {"checked": (NOW - dt.timedelta(hours=6)).isoformat(), "human": (NOW - dt.timedelta(days=1)).isoformat(),
           "at": (NOW - dt.timedelta(hours=6)).isoformat(), "state": "идёт", "direction": "вперёд",
           "risk": "низкий", "kinds": [], "ball": "сопровождение", "reply": False, "due": None,
           "dueKind": None, "blocker": None}
    raw.update(kw)
    return dealctx.clean({"version": 1, "chats": {"1": raw}, "contracts": {}})["chats"]["1"]


ACTIVE = {"status": "active", "last": "18.10.2026", "days": 1}
QUIET = {"status": "quiet", "last": "", "days": None}
UNREAD = {"status": "unread", "last": "", "days": None}


# ── «подвисла» ────────────────────────────────────────────────────────────────

def test_без_оценки_итог_как_до_23_10():
    assert dealctx.decide(QUIET, None, TODAY) == (True, "в чате людей не было", "в чате людей не было", "quiet")
    assert dealctx.decide({"status": "quiet", "last": "01.10.2026", "days": 18}, None, TODAY)[0] is True
    stuck, note, priv, src = dealctx.decide(ACTIVE, None, TODAY)
    assert (stuck, src) == (False, "active") and note.startswith("в чате работа") and priv == note
    н = "чат не проверен — простой не подтверждён"
    assert dealctx.decide(UNREAD, None, TODAY) == (None, н, н, "unverified")
    assert dealctx.decide({"status": "skipped", "last": "", "days": None}, None, TODAY)[0] is None


def test_пишут_но_ход_за_нами_стоит_или_откат_подвисла():
    for kw, why in (({"state": "ждёт_нас"}, "ход за нами"), ({"state": "стоит"}, "никто не двигает"),
                    ({"direction": "назад"}, "откат")):
        stuck, note, priv, src = dealctx.decide(ACTIVE, _a(**kw), TODAY)
        assert (stuck, src) == (True, "model"), kw
        assert why in priv and "по оценке чата 19.10" in priv
        assert note == dealctx.PUBLIC_STUCK_MODEL, "для всех — откуда решение, без самой оценки"


def test_ждём_вторую_сторону_до_срока_не_подвисла():
    stuck, note, priv, src = dealctx.decide(ACTIVE, _a(state="ждёт_поставщика", due="2026-10-25", dueKind="отгрузка"), TODAY)
    assert (stuck, src) == (False, "active") and priv.endswith("ждём поставщика до 25.10")
    assert "ждём" not in note


def test_устаревшая_оценка_свежую_переписку_не_судит():
    # человек писал после оценки — по данным прогона оценки
    после = _a(state="стоит", at=(NOW - dt.timedelta(days=3)).isoformat(), human=(NOW - dt.timedelta(days=1)).isoformat())
    assert dealctx.current(после, TODAY) is None
    assert dealctx.decide(ACTIVE, после, TODAY)[3] == "active"
    # … и по чтению этой сборки
    оценка = _a(state="стоит", at=(NOW - dt.timedelta(days=3)).isoformat(), human=(NOW - dt.timedelta(days=4)).isoformat())
    assert dealctx.current(оценка, TODAY) is not None
    assert dealctx.current(оценка, TODAY, "18.10.2026") is None
    # сборка видит только день: сообщение в день оценки — уже не судим
    сегодня = _a(state="стоит", at=(NOW - dt.timedelta(hours=5)).isoformat(),
                 human=(NOW - dt.timedelta(days=2)).isoformat())
    assert dealctx.current(сегодня, TODAY, "17.10.2026") is not None
    assert dealctx.current(сегодня, TODAY, "19.10.2026") is None
    # прогон оценки давно не читал чат
    давно = _a(state="стоит", checked=(NOW - dt.timedelta(days=dealctx.FRESH_DAYS + 1)).isoformat(),
               at=(NOW - dt.timedelta(days=9)).isoformat(), human=(NOW - dt.timedelta(days=10)).isoformat())
    assert dealctx.current(давно, TODAY) is None
    assert dealctx.decide(UNREAD, давно, TODAY)[3] == "unverified"


def test_сборка_чат_не_прочла_решает_свежее_чтение_прогона():
    d = dealctx.decide(UNREAD, _a(), TODAY)
    assert (d[0], d[3]) == (False, "ctxActive") and d[1] == "в чате работа — не подвисла"
    тихо = _a(human=(NOW - dt.timedelta(days=dealctx.chat_activity.QUIET_DAYS + 1)).isoformat())
    stuck, note, priv, src = dealctx.decide(UNREAD, тихо, TODAY)
    assert (stuck, src) == (True, "ctxQuiet") and "тихо 8 дн." in priv and note == dealctx.PUBLIC_STUCK
    без_людей = dealctx.clean({"version": 1, "chats": {"1": {"checked": NOW.isoformat(), "human": None, "at": None}},
                               "contracts": {}})["chats"]["1"]
    assert dealctx.decide(UNREAD, без_людей, TODAY)[:1] == (True,)
    assert dealctx.decide(UNREAD, _a(state="ждёт_нас"), TODAY)[3] == "model"


def test_граница_тишины_как_у_chat_activity():
    ровно = _a(human=(NOW - dt.timedelta(days=dealctx.chat_activity.QUIET_DAYS)).isoformat())
    assert dealctx.decide(UNREAD, ровно, TODAY)[3] == "ctxActive"   # active: days <= QUIET_DAYS


# ── рабочие часы ──────────────────────────────────────────────────────────────

def test_рабочие_часы_без_выходных():
    пн = dt.datetime(2026, 10, 19, 12, tzinfo=dealctx.MSK)
    assert dealctx.work_hours(dt.datetime(2026, 10, 17, 0, tzinfo=dealctx.MSK), пн) == 12   # с субботы
    assert dealctx.work_hours(dt.datetime(2026, 10, 16, 12, tzinfo=dealctx.MSK), пн) == 24  # с пятницы
    assert dealctx.work_hours(пн, пн - dt.timedelta(hours=1)) == 0
    assert dealctx.work_hours(пн - dt.timedelta(days=400), пн) <= dealctx.DUE_MAX_DAYS * 24


# ── форма ключа ───────────────────────────────────────────────────────────────

def test_ключ_не_по_форме_отбрасывается_и_считается():
    raw = fixture.realctx_raw()
    raw["chats"]["6100"] = dict(raw["chats"]["6002"], state="думает")         # не из списка
    raw["chats"]["6101"] = dict(raw["chats"]["6002"], kinds=["срок", "погода"])
    raw["chats"]["abc"] = raw["chats"]["6002"]                                  # не номер сделки
    raw["chats"]["6102"] = dict(raw["chats"]["6002"], due="25.10.2026")       # дата не ISO
    raw["contracts"]["6103"] = dict(raw["contracts"]["6001"], outcome="почти")
    raw["contracts"]["6104"] = dict(raw["contracts"]["6001"], flags=[{"code": "rm -rf"}])
    raw["chats"]["6105"] = dict(raw["chats"]["6002"], conf=1.7)                # уверенность вне 0…1
    ctx = dealctx.clean(raw)
    assert ctx["bad"] == 7
    assert "6100" not in ctx["chats"] and "6002" in ctx["chats"] and "6103" not in ctx["contracts"]
    assert dealctx.clean({"version": 2}) is None and dealctx.clean([]) is None


def test_флаги_только_у_пары():
    c = dealctx.clean({"version": 1, "chats": {}, "contracts": {"1": {
        "at": NOW.isoformat(), "outcome": "недочитана", "read": {"customer": 1},
        "flags": [{"code": "cisg", "order": None, "days": None}]}}})["contracts"]["1"]
    assert c["flags"] == [] and c["read"] == {"customer": 1, "supplier": 0}


def test_списки_как_в_прогонах():
    import chat_context as cc
    import contract_terms as ct
    assert dealctx.STATES == cc.СОСТОЯНИЯ and dealctx.DIRECTIONS == cc.НАПРАВЛЕНИЯ
    assert dealctx.RISKS == cc.РИСКИ and dealctx.RISK_KINDS == cc.ВИДЫ_РИСКА
    assert dealctx.BLOCKERS == cc.ПРЕПЯТСТВИЯ and dealctx.ROLES == cc.РОЛИ and dealctx.DUE_KINDS == cc.ВИДЫ_СРОКА
    assert dealctx.OUTCOMES == ct.ИСХОДЫ + ("разошлось",)
    assert set(dealctx.FLAG_SHORT) == set(dealctx.contract_b2b.FLAG_TEXT)
    assert set(dealctx.ROLE_LBL) == set(cc.РОЛИ) and set(dealctx.DUE_ACC) == set(cc.ВИДЫ_СРОКА)


def test_нет_файла_нет_свода(tmp_path, monkeypatch):
    monkeypatch.delenv("REALCTX_PATH", raising=False)
    assert dealctx.load() is None
    p = tmp_path / "ctx.json"
    p.write_text("{", encoding="utf-8")
    assert dealctx.load(str(p)) is None
    p.write_text(json.dumps(fixture.realctx_raw()), encoding="utf-8")
    monkeypatch.setenv("REALCTX_PATH", str(p))
    assert dealctx.load()["chats"]["6002"]["state"] == "ждёт_нас"
    assert dealctx.build([], None, None, NOW) == (None, None)


# ── вкладка целиком ───────────────────────────────────────────────────────────

def test_без_свода_вкладка_прежняя():
    до = fixture.build_contracts()
    после = fixture.build_contracts(ctx_chats=None)
    assert [(r["deal"], r["stuck"], r["slow"], r.get("chatUnverified"), r["why"]) for r in до["rows"]] == \
        [(r["deal"], r["stuck"], r["slow"], r.get("chatUnverified"), r["why"]) for r in после["rows"]]
    assert "ctx" not in до["head"]
    assert sum(до["head"]["chatBy"].values()) == sum(v for k, v in до["head"]["chat"].items()
                                                      if k in ("active", "quiet", "unread", "skipped"))


def test_вкладка_со_сводом():
    r = fixture.build_realization()
    ct, dc = r["contracts"], r["dealctx"]
    rows = {x["deal"]: x for x in ct["rows"]}
    by = ct["head"]["chatBy"]
    # каждый кандидат решён ровно одним способом
    assert sum(by.values()) == sum(v for k, v in ct["head"]["chat"].items() if k in ("active", "quiet", "unread", "skipped"))
    assert by["model"] == 2 and by["ctxQuiet"] == 1 and by["ctxActive"] == 1
    d = dc["deals"]
    assert rows["6002"]["stuck"] and "ход за нами" in d["6002"]["why"]
    assert rows["6008"]["stuck"] and "никто не двигает" in d["6008"]["why"]
    assert not rows["6004"]["stuck"] and d["6004"]["why"].endswith("ждём поставщика до 25.10")
    assert not rows["6005"]["stuck"] and rows["6005"]["chatUnverified"] is False
    assert rows["6010"]["stuck"] and "по чтению 19.10: в чате тихо 20 дн." in d["6010"]["why"]
    # строки сделок видят все с вкладкой: в них нет ни оценки модели, ни данных прогона оценки
    for x in ct["rows"]:
        for слово in ("по оценке чата", "ждём", "по чтению", "ход за нами", "никто не двигает", "откат"):
            assert слово not in x["why"], (x["deal"], слово)
    assert not rows["6012"]["stuck"], "оценка до новой переписки — не основание"
    assert rows["6000"]["chatUnverified"] is True
    h = ct["head"]["ctx"]
    assert h["open"] == len([x for x in ct["rows"] if not x["closed"]])
    assert h["chat"] == {"seen": 7, "cur": 5, "reply": 2, "replyLate": 1, "high": 1, "dueLate": 1}
    assert h["contract"] == {"seen": 6, "risk": 1, "flags": 2, "nopair": 2, "pair": 2, "unread": 1}
    q = [(x["code"], x["deal"]) for x in dc["queue"]]
    assert q == [("chatReply", "6006"), ("chatRisk", "6008"), ("chatDue", "6008"), ("contractRisk", "6001"),
                 ("contractMissing", "6007"), ("contractMissing", "6003")]
    reply = dc["queue"][0]
    assert reply["who"] == rows["6006"]["ossDeal"] and reply["days"] == 4
    assert dc["queue"][3]["who"] == dealctx.CONTRACT_HEAD and dc["queue"][3]["act"].endswith("пени, документы")
    assert dc["counts"] == {"chatReply": 1, "chatRisk": 1, "chatDue": 1, "contractRisk": 1, "contractMissing": 2}
    assert d["6001"]["contract"]["flags"][0]["text"] == dealctx.contract_b2b.FLAG_TEXT["penaltyUncovered"]
    assert d["6003"]["contract"]["missing"] == ["supplier"]
    assert d["6011"]["contract"]["outcome"] == "разошлось" and d["6011"]["contract"]["flags"] == []
    assert d["6004"]["chat"]["blocker"] == "поставка · на стороне: поставщик"
    assert d["6014"]["chat"]["cur"] is False
    assert d["6002"]["chat"]["conf"] == 80, "уверенность модели — рядом с оценкой"
    assert d["6007"]["contract"]["missing"] == ["customer", "supplier"]
    assert dc["queue"][4]["what"] == "договор заказчика и поставщика не найден в карточках сделки"
    # «что делать первым» — самое важное из обеих очередей
    for did, rec in d.items():
        if "todo" in rec:
            все = [x for x in ct["life"]["queue"] + dc["queue"] if str(x["deal"]) == did]
            assert rec["todo"]["code"] == min(все, key=lambda x: (x["sev"], -(x["days"] or 0)))["code"]


def test_в_закрытой_части_нет_текста_чатов_и_условий():
    """Только подписи из кода, даты и имена людей сделки — ни цитат, ни значений условий."""
    dc = fixture.build_realization()["dealctx"]
    s = json.dumps(dc, ensure_ascii=False)
    for слово in ("ЦИТАТА", "quote", "terms", "evidence"):
        assert слово not in s
