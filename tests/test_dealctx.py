"""Чат и договор сделки во вкладке «Реализация» (dealctx.py, решение владельца 23.10.2026).

Корпус придуман (CLAUDE.md, правило 18): сделки ContractsStub и свод fixture.realctx_raw.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import contracts  # noqa: E402
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


def test_у_каждого_исхода_есть_подпись_во_вкладке():
    """Исход разбора без подписи вкладка показала бы кодом — «не читается» (25.10.2026)
    и любой следующий должен прийти в _DC_OUT шаблона вместе со списком."""
    import re
    шаблон = (ROOT / "templates" / "dashboard_core.html").read_text(encoding="utf-8")
    тело = re.search(r"const _DC_OUT=\{(.*?)\};", шаблон).group(1)
    подписи = dict(re.findall(r"'([^']+)':'([^']+)'", тело))
    assert set(подписи) == set(dealctx.OUTCOMES), set(dealctx.OUTCOMES) ^ set(подписи)
    assert подписи["не читается"] == "файл договора не читается — перезалить"
    assert dealctx.UNREADABLE in dealctx.OUTCOMES


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
    assert not rows["6020"]["stuck"] and rows["6020"]["chatUnverified"] is False
    # «ждём УПД от клиента» — не кандидат «Замедлено» (решение владельца 27.10.2026, Б)
    assert not rows["6005"]["stuck"] and "сверх нормы шага" not in rows["6005"]["why"]
    assert rows["6010"]["stuck"] and "по чтению 19.10: в чате тихо 20 дн." in d["6010"]["why"]
    # строки сделок видят все с вкладкой: в них нет ни оценки модели, ни данных прогона оценки
    for x in ct["rows"]:
        for слово in ("по оценке чата", "ждём", "по чтению", "ход за нами", "никто не двигает", "откат"):
            assert слово not in x["why"], (x["deal"], слово)
    assert not rows["6012"]["stuck"], "оценка до новой переписки — не основание"
    assert rows["6000"]["chatUnverified"] is True
    h = ct["head"]["ctx"]
    # плитки основного вида — без отдельного направления (решение владельца 25.10.2026)
    assert h["open"] == len([x for x in ct["rows"] if not x["closed"] and not x["seg"]])
    assert "sep" not in h and ct["sep"]["head"]["ctx"]["open"] == len(
        [x for x in ct["rows"] if not x["closed"] and x["seg"]])
    assert h["chat"] == {"seen": 7, "cur": 5, "reply": 2, "replyLate": 1, "high": 1, "dueLate": 1}
    assert h["contract"] == {"seen": 7, "risk": 1, "flags": 2, "nopair": 2, "pair": 2, "unread": 1, "unreadable": 1}
    q = [(x["code"], x["deal"]) for x in dc["queue"]]
    assert q == [("chatReply", "6006"), ("chatRisk", "6008"), ("chatDue", "6008"), ("contractRisk", "6001"),
                 ("econGap", "6010"), ("econGap", "6003"), ("contractMissing", "6007"), ("contractMissing", "6003"),
                 ("contractUnreadable", "6015")]
    reply = dc["queue"][0]
    assert reply["what"].endswith("оценка модели, уверенность 80 %"), "пункт по чату — оценка, а не факт"
    assert reply["who"] == rows["6006"]["ossDeal"] and reply["days"] == 4
    # риск сверки договоров — руководитель ОРК по справочнику отделов (ork.ITEM, решение 26.10.2026, п. 1)
    assert dc["queue"][3]["role"] == "orkHead" and dc["queue"][3]["who"] == "Роза (рук. ОРК)"
    assert dc["queue"][3]["act"].endswith("пени, документы")
    assert dc["counts"] == {"chatReply": 1, "chatRisk": 1, "chatDue": 1, "contractRisk": 1, "contractMissing": 2,
                            "econGap": 2, "contractUnreadable": 1}
    # файл договора не читается — пункт ответственному сделки: следующий прогон это не
    # вылечит, файл надо перезалить; роль — «ответственный сделки» (регламент пункт не описывает)
    нечит = next(q for q in dc["queue"] if q["code"] == "contractUnreadable")
    assert нечит["who"] == rows["6015"]["manager"] and нечит["what"] == "файл договора не читается"
    assert нечит["role"] == "dealResp" and нечит["roleName"] == "ответственный сделки"
    assert нечит["act"].startswith("перезалить файл договора")
    assert d["6015"]["contract"]["outcome"] == "не читается" and d["6015"]["contract"]["missing"] == []
    assert d["6001"]["contract"]["flags"][0]["text"] == dealctx.contract_b2b.FLAG_TEXT["penaltyUncovered"]
    assert d["6003"]["contract"]["missing"] == ["supplier"]
    assert d["6011"]["contract"]["outcome"] == "разошлось" and d["6011"]["contract"]["flags"] == []
    assert d["6004"]["chat"]["blocker"] == "поставка · на стороне: поставщик"
    assert d["6014"]["chat"]["cur"] is False
    assert d["6002"]["chat"]["conf"] == 80, "уверенность модели — рядом с оценкой"
    assert d["6007"]["contract"]["missing"] == ["customer", "supplier"]
    assert any(q["what"] == ("договор заказчика и поставщика не найден: ни в карточках договоров сделки "
                             "и её заказов, ни в Purchase order") for q in dc["queue"])
    # «что делать первым» — самое важное из обеих очередей обоих видов вкладки
    for did, rec in d.items():
        if "todo" in rec:
            все = [x for x in contracts.life_all(ct)["queue"] + dc["queue"] if str(x["deal"]) == did]
            assert rec["todo"]["code"] == min(все, key=lambda x: (x["sev"], -(x["days"] or 0)))["code"]


def test_пункты_направления_в_его_очереди():
    """Решение владельца 25.10.2026: чат и договор сделки отдельного направления — пункты
    его очереди (seg) и его плитки (head["sep"]); счётчики и плитки основного вида — как
    без них. «Что делать первым» у сделки направления — из её же очереди."""
    raw = fixture.realctx_raw()
    base = dict(raw["chats"]["6008"])
    raw["chats"]["6101"] = dict(base)          # высокий риск, срок из чата прошёл
    raw["contracts"]["6100"] = {"at": raw["at"], "outcome": "нет договоров",
                                "read": {"customer": 0, "supplier": 0}, "flags": []}
    ctx = dealctx.clean(raw)
    ct = fixture.build_contracts(ctx_chats=ctx["chats"])
    head, dc = dealctx.build(ct["rows"], contracts.life_all(ct), ctx, NOW, today=fixture.CT_TODAY)
    сеп = [q for q in dc["queue"] if q["seg"]]
    assert {(q["code"], q["deal"]) for q in сеп} == {("chatRisk", "6101"), ("chatDue", "6101"),
                                                    ("contractMissing", "6100")}
    assert all(q["seg"] == fixture.CT_SEP_CAT for q in сеп)
    assert dc["counts"] == {"chatReply": 1, "chatRisk": 1, "chatDue": 1, "contractRisk": 1, "contractMissing": 2,
                            "econGap": 2, "contractUnreadable": 1}
    assert dc["sepCounts"] == {"chatRisk": 1, "chatDue": 1, "contractMissing": 1}
    assert head["chat"]["high"] == 1 and head["sep"]["chat"]["high"] == 1
    assert head["sep"]["open"] == 2 and head["sep"]["contract"]["seen"] == 1
    # главное по сделке направления — сорванный срок клиента её заказа (очередь lifecycle)
    assert dc["deals"]["6101"]["todo"]["code"] == "late"


def test_в_закрытой_части_нет_текста_чатов_и_условий():
    """Только подписи из кода, даты и имена людей сделки — ни цитат, ни значений условий."""
    dc = fixture.build_realization()["dealctx"]
    s = json.dumps(dc, ensure_ascii=False)
    for слово in ("ЦИТАТА", "quote", "terms", "evidence"):
        assert слово not in s


# ── финальная экономика из файла (решение владельца 23.10.2026) ──────────────────

def _e(**kw) -> dict:
    raw = {"at": NOW.isoformat(), "sale": 2_600_000.0, "cur": "RUB", "saleEur": 26_000.0, "buyEur": 20_000.0,
           "markup": 0.3, "rebid": False, "items": 3, "fx": {"EUR": 100.0, "USD": 92.0}}
    raw.update(kw)
    return raw


def test_экономика_в_ключе_только_по_форме():
    raw = {"version": 1, "chats": {}, "contracts": {}, "econ": {
        "1": _e(), "2": _e(saleEur=10_000.0, markup=-0.5),          # продажа ниже закупки
        "3": _e(markup=0.9),                                         # наценка не из своих чисел
        "4": _e(cur="руб"), "5": _e(fx={"EUR": "сто"}), "6": _e(sale=float("nan")),
        "7": _e(items=0), "8": _e(rebid="нет"), "x": _e(),
        "9": _e(ratio="сто"), "10": _e(ratio=-1.0), "11": _e(note="что-то"),
        "12": _e(ratio=1.05, note=None), "13": _e(ratio=None, note="суммы нет")}}
    ctx = dealctx.clean(raw)
    assert set(ctx["econ"]) == {"1", "12", "13"} and ctx["bad"] == 11
    assert ctx["econ"]["12"]["ratioAtRead"] == 1.05 and ctx["econ"]["13"]["ratioAtRead"] is None
    assert "ratioAtRead" not in ctx["econ"]["1"], "ключ до 25.10.2026 — без сверки на день чтения"
    assert ctx["econ"]["1"]["fx"] == {"EUR": 100.0, "USD": 92.0}
    # ключ до 23.10.2026 — без раздела econ: вкладка как была
    assert dealctx.clean({"version": 1, "chats": {}, "contracts": {}})["econ"] == {}


def test_сверка_суммы_в_валюте_подачи():
    e = dealctx.clean({"version": 1, "econ": {"1": _e()}})["econ"]["1"]
    assert dealctx.econ_ratio(2_600_000, "RUB", e) == (1.0, "")
    r, _ = dealctx.econ_ratio(20_000, "EUR", e)          # по курсу самого файла: 100 ₽ за €
    assert r == pytest.approx(2_000_000 / 2_600_000)
    r, _ = dealctx.econ_ratio(28_000, "USD", e)          # по курсам файла
    assert r == pytest.approx(28_000 * 92 / 2_600_000)
    assert dealctx.econ_ratio(1000, "CNY", e) == (None, dealctx.ECON_OTHER_CUR)
    # суммы нет — сверять не с чем (ревизия, дефект 5): не «расходится на 100 %»
    assert dealctx.econ_ratio(0, "RUB", e) == (None, dealctx.ECON_NO_SUM)
    assert dealctx.econ_ratio(None, "", e) == (None, dealctx.ECON_NO_SUM)
    assert dealctx.econ_ratio("", "CNY", e) == (None, dealctx.ECON_NO_SUM), "суммы нет — раньше валюты"
    assert dealctx.econ_ratio("много", "RUB", e) == (None, "") and dealctx.econ_ratio(float("nan"), "RUB", e) == (None, "")


def test_сверка_суммы_без_ндс():
    """Ревизия, дефект 4: «Сумма» с НДС против нетто-продажи файла — OPPORTUNITY − TAX_VALUE."""
    e = dealctx.clean({"version": 1, "econ": {"1": _e()}})["econ"]["1"]
    assert dealctx.econ_ratio(3_172_000, "RUB", e, tax=572_000) == (1.0, "")
    assert dealctx.econ_ratio("3172000", "RUB", e, tax="572000.00") == (1.0, ""), "поля Битрикса — строками"
    r, _ = dealctx.econ_ratio(3_172_000, "RUB", e, tax="")              # налога нет — «Сумма» как есть
    assert r == pytest.approx(1.22) and dealctx.econ_vat(r)
    for налог in ("много", -1, 3_172_000):                              # не число, отрицателен, не меньше суммы
        assert dealctx.econ_ratio(3_172_000, "RUB", e, tax=налог) == (None, "")
    assert [dealctx.econ_vat(x) for x in (1.2, 1.209, 1.22, 1.23, 1.19, 1.215, 1.1, 1.25, None, True)] == \
        [True, True, True, True, True, True, False, False, False, False]


def test_экономика_в_карточке_и_пункт_очереди():
    r = fixture.build_realization()
    ct, dc = r["contracts"], r["dealctx"]
    rows = {x["deal"]: x for x in ct["rows"]}
    d = dc["deals"]
    e = d["6010"]["econ"]
    assert e == {"at": "17.10.2026", "sale": "80\u00a0000\u00a0EUR", "cur": "EUR", "buy": "€60\u00a0000", "markup": 33.3,
                 "rebid": False, "items": 9, "gap": 37.5, "gapNote": "", "vat": False, "preRebid": False,
                 "over": True}
    assert rows["6010"]["saleAmt"] == 50_000.0 and rows["6010"]["saleCur"] == "EUR"
    assert rows["6010"]["saleTax"] == 0.0, "НДС в «Сумме» — из TAX_VALUE; в стабе его нет"
    assert d["6005"]["econ"]["rebid"] is True and d["6005"]["econ"]["gap"] == 0
    assert d["6006"]["econ"]["gap"] == 5 and d["6006"]["econ"]["over"] is False
    gap = [x for x in dc["queue"] if x["code"] == "econGap"]
    assert [x["deal"] for x in gap] == ["6010", "6003"]
    assert gap[0]["what"] == "Сумма сделки расходится с финальной экономикой на 37,5 %"
    assert gap[1]["what"].endswith("на 17,2 %"), "29 000 против 35 000 — 17,14 %: вверх, от порога"
    assert gap[0]["act"] == "обновить «Сумму» сделки по финальному расчёту"
    assert gap[0]["who"] == rows["6010"]["manager"], "хозяин — ответственный сделки"
    h = ct["head"]["ctx"]
    assert h["econ"] == {"seen": 5, "gap": 2, "otherCur": 0, "noSum": 0, "vat": 0, "changed": 0, "preRebid": 0}
    assert h["rules"]["econGapPct"] == 10
    # числа файла — только в закрытой части: ни в шапке, ни в строках сделок
    открыто = json.dumps({"head": ct["head"], "rows": ct["rows"]}, ensure_ascii=False)
    for слово in ("80\u00a0000\u00a0EUR", "€60\u00a0000", "наценк", "markup", "rebid"):
        assert слово not in открыто
    assert all("econ" not in x for x in ct["rows"])
    assert set(h["econ"]) == {"seen", "gap", "otherCur", "noSum", "vat", "changed", "preRebid"}


def test_экономика_валюта_сделки_другая_без_пункта():
    ctx = dealctx.clean({"version": 1, "chats": {}, "contracts": {}, "econ": {"1": _e()}})
    row = {"deal": "1", "seq": 5, "closed": False, "manager": "Ответственный ВЫДУМКА", "saleAmt": 1000.0,
           "saleCur": "CNY", "customer": "—", "title": ""}
    head, payload = dealctx.build([row], None, ctx, NOW, today=TODAY)
    assert payload["deals"]["1"]["econ"]["gapNote"] == dealctx.ECON_OTHER_CUR
    assert payload["deals"]["1"]["econ"]["gap"] is None and payload["queue"] == []
    assert head["econ"] == {"seen": 1, "gap": 0, "otherCur": 1, "noSum": 0, "vat": 0, "changed": 0, "preRebid": 0}
    # сборка не знает суммы сделки (строка без saleAmt или она не число) — не «расходится на 100 %»
    row.pop("saleAmt")
    _h, payload = dealctx.build([row], None, ctx, NOW, today=TODAY)
    assert payload["deals"]["1"]["econ"]["gap"] is None and payload["queue"] == []
    row.update(saleAmt=None)
    _h, payload = dealctx.build([row], None, ctx, NOW, today=TODAY)
    assert payload["deals"]["1"]["econ"]["gap"] is None and payload["queue"] == []
    # граница порога: 10 % — ещё не пункт
    row.update(saleAmt=2_860_000.0, saleCur="RUB")
    _h, payload = dealctx.build([row], None, ctx, NOW, today=TODAY)
    assert payload["deals"]["1"]["econ"]["gap"] == 10 and payload["queue"] == []
    row.update(saleAmt=2_870_000.0)
    _h, payload = dealctx.build([row], None, ctx, NOW, today=TODAY)
    assert [q["code"] for q in payload["queue"]] == ["econGap"]


def _одна(row_kw: dict, **e_kw):
    ctx = dealctx.clean({"version": 1, "chats": {}, "contracts": {}, "econ": {"1": _e(**e_kw)}})
    row = {"deal": "1", "seq": 5, "closed": False, "manager": "Ответственный ВЫДУМКА", "saleCur": "RUB",
           "customer": "—", "title": ""} | row_kw
    return dealctx.build([row], None, ctx, NOW, today=TODAY)


def test_экономика_суммы_нет_не_пункт():
    """Ревизия, дефект 5: «Суммы» в сделке нет — не «расходится на 100 %»."""
    head, payload = _одна({"saleAmt": 0.0})
    cell = payload["deals"]["1"]["econ"]
    assert cell["gap"] is None and cell["over"] is False and cell["gapNote"] == dealctx.ECON_NO_SUM
    assert payload["queue"] == [] and head["econ"]["noSum"] == 1 and head["econ"]["gap"] == 0


def test_экономика_расхождение_одним_знаком_от_порога():
    """10,04 % — пункт («больше 10 %»), и печатается он не «10 %»."""
    _h, payload = _одна({"saleAmt": 2_861_040.0})                      # 2 600 000 × 1,1004
    cell = payload["deals"]["1"]["econ"]
    assert cell["over"] is True and cell["gap"] == 10.1
    assert payload["queue"][0]["what"] == "Сумма сделки расходится с финальной экономикой на 10,1 %"
    _h, payload = _одна({"saleAmt": 2_858_960.0})                      # 9,96 % — не пункт, и не «10 %»
    assert payload["deals"]["1"]["econ"]["gap"] == 9.9 and payload["queue"] == []


def test_экономика_похоже_на_ндс_не_пункт():
    head, payload = _одна({"saleAmt": 3_172_000.0})                    # × 1,22 к нетто-продаже файла
    cell = payload["deals"]["1"]["econ"]
    assert cell["vat"] is True and cell["over"] is False and cell["gap"] == 22.0
    assert payload["queue"] == [] and head["econ"]["vat"] == 1 and head["econ"]["gap"] == 0
    # НДС указан в сделке — вычитается, и всё сходится
    head, payload = _одна({"saleAmt": 3_172_000.0, "saleTax": 572_000.0})
    assert payload["deals"]["1"]["econ"]["gap"] == 0 and head["econ"]["vat"] == 0
    # 40 % — не НДС: пункт
    _h, payload = _одна({"saleAmt": 3_640_000.0})
    assert [q["code"] for q in payload["queue"]] == ["econGap"]


def test_экономика_сумма_менялась_после_чтения_не_пункт():
    """Минорное ревизии: файл обновили и «Сумму» по нему — до перечтения пункта нет."""
    # на день чтения «Сумма» сходилась, теперь — нет: менялась после чтения
    head, payload = _одна({"saleAmt": 3_000_000.0}, ratio=1.0)
    cell = payload["deals"]["1"]["econ"]
    assert cell["gapNote"] == dealctx.ECON_CHANGED and cell["gap"] is None and cell["over"] is False
    assert payload["queue"] == [] and head["econ"]["changed"] == 1
    # на день чтения не было валюты для сверки, теперь сумма в рублях — менялась
    _h, payload = _одна({"saleAmt": 3_000_000.0}, ratio=None, note="валюта сделки другая")
    assert payload["deals"]["1"]["econ"]["gapNote"] == dealctx.ECON_CHANGED and payload["queue"] == []
    # та же «Сумма», что на день чтения, и она расходится — пункт
    _h, payload = _одна({"saleAmt": 3_900_000.0}, ratio=1.5)
    assert [q["code"] for q in payload["queue"]] == ["econGap"]
    assert payload["deals"]["1"]["econ"]["gapNote"] == ""


def test_экономика_сумма_внесена_после_чтения_сверяется_прямо():
    """Вторая ревизия, 5: на день чтения «Суммы» не было («суммы нет»), назавтра внесли — не
    «менялась после чтения» (молчание на 14 дней до перечтения), а сверка с файлом прямо."""
    head, payload = _одна({"saleAmt": 3_900_000.0}, ratio=None, note="суммы нет")
    cell = payload["deals"]["1"]["econ"]
    assert cell["gapNote"] == "" and cell["over"] is True and cell["gap"] == 50.0
    assert [q["code"] for q in payload["queue"]] == ["econGap"] and head["econ"]["changed"] == 0
    # внесли верную — сходится
    _h, payload = _одна({"saleAmt": 2_600_000.0}, ratio=None, note="суммы нет")
    assert payload["deals"]["1"]["econ"]["gap"] == 0 and payload["queue"] == []
    # и сегодня суммы нет — «суммы нет», без пункта
    _h, payload = _одна({"saleAmt": 0.0}, ratio=None, note="суммы нет")
    assert payload["deals"]["1"]["econ"]["gapNote"] == dealctx.ECON_NO_SUM and payload["queue"] == []


def test_экономика_ндс_указан_тогда_120_расхождение():
    """Вторая ревизия, 2а: налог в сделке указан и вычтен — нетто всё равно на 20 % выше
    продажи файла: это расхождение, а не «похоже на сумму с НДС»."""
    head, payload = _одна({"saleAmt": 3_744_000.0, "saleTax": 624_000.0})   # нетто 3 120 000 = ×1,20
    cell = payload["deals"]["1"]["econ"]
    assert cell["vat"] is False and cell["over"] is True and cell["gap"] == 20.0
    assert [q["code"] for q in payload["queue"]] == ["econGap"] and head["econ"]["vat"] == 0
    # налога нет — та же доля «≈ с НДС»
    head, payload = _одна({"saleAmt": 3_120_000.0, "saleTax": 0.0})
    assert payload["deals"]["1"]["econ"]["vat"] is True and payload["queue"] == [] and head["econ"]["vat"] == 1
    assert dealctx.econ_verdict(1.2, tax_empty=False) == dealctx.ECON_OVER
    assert dealctx.econ_verdict(1.2, tax_empty=True) == dealctx.ECON_VAT_LIKE
    assert dealctx.econ_verdict(1.05, tax_empty=True) == dealctx.ECON_OK


def test_экономика_сумма_до_переторжки_пункт():
    """Вторая ревизия, 2б: «Наше КП» 890 000 ₽, переторжка −17 % — 738 700 ₽; «Сумма» осталась
    890 000 при пустом налоге. Отношение 1,205 похоже на НДС, но «Сумма» — ровно «Наше КП»:
    её не обновили после переторжки. Пункт — при любом расхождении."""
    e = dict(sale=738_700.0, saleEur=7_387.0, buyEur=6_000.0, markup=7_387 / 6_000 - 1, rebid=True,
             kpSale=890_000.0)
    head, payload = _одна({"saleAmt": 890_000.0, "saleTax": 0.0}, **e)
    cell = payload["deals"]["1"]["econ"]
    assert cell["preRebid"] is True and cell["vat"] is False and cell["over"] is True and cell["gap"] == 20.5
    assert payload["queue"][0]["what"] == ("Сумма сделки — продажа «Нашего КП» до переторжки: расходится с "
                                           "финальной экономикой на 20,5 %")
    assert head["econ"]["preRebid"] == 1 and head["econ"]["gap"] == 1 and head["econ"]["vat"] == 0
    # скидка переторжки 3 % — меньше порога, но «Сумма» = «Наше КП»: пункт
    e3 = dict(e, sale=863_300.0, saleEur=8_633.0, markup=8_633 / 6_000 - 1)
    _h, payload = _одна({"saleAmt": 890_000.0}, **e3)
    assert payload["deals"]["1"]["econ"]["preRebid"] is True and [q["code"] for q in payload["queue"]] == ["econGap"]
    # «Сумма» обновлена по переторжке — сходится
    _h, payload = _одна({"saleAmt": 738_700.0}, **e)
    assert payload["deals"]["1"]["econ"]["gap"] == 0 and payload["queue"] == []


def test_экономика_продажа_кп_только_у_переторжки():
    raw = {"version": 1, "econ": {"1": _e(rebid=True, kpSale=3_000_000.0), "2": _e(kpSale=3_000_000.0),
                                  "3": _e(rebid=True, kpSale=-1.0), "4": _e(rebid=True, kpSale="много"),
                                  "5": _e(rebid=True, kpSale=None)}}
    ctx = dealctx.clean(raw)
    assert set(ctx["econ"]) == {"1", "5"} and ctx["bad"] == 3
    assert ctx["econ"]["1"]["kpSale"] == 3_000_000.0 and "kpSale" not in ctx["econ"]["5"]


def test_экономика_налог_не_число_сверки_нет():
    """sql-ревизия, 3: TAX_VALUE не прочитан (saleTax None) — сверки нет, а не «налог 0»."""
    head, payload = _одна({"saleAmt": 3_640_000.0, "saleTax": None})
    cell = payload["deals"]["1"]["econ"]
    assert cell["gap"] is None and cell["over"] is False and cell["gapNote"] == "" and payload["queue"] == []
    _h, payload = _одна({"saleAmt": 3_172_000.0, "saleTax": None}, ratio=None, note=None)
    cell = payload["deals"]["1"]["econ"]
    assert cell["gapNote"] == "" and cell["vat"] is False, "не «менялась после чтения» и не «≈ с НДС»"


def test_сумма_менялась_один_признак_у_сборки_и_прогона():
    ch = dealctx.econ_changed
    assert ch(1.2, "", 1.0, None) and not ch(1.00001, "", 1.0, None)
    assert ch(1.0, "", None, "суммы нет") and ch(None, "суммы нет", 1.0, None)
    assert not ch(None, "суммы нет", None, "суммы нет") and ch(None, "валюта сделки другая", None, "суммы нет")


def test_статус_сверено_как_у_шаблона():
    import econ_template
    import realization_ctx
    assert realization_ctx.ECON_VERIFIED == econ_template.СВЕРЕНО


def test_хозяин_пунктов_чатов_и_договоров_по_регламенту():
    """Решение владельца 26.10.2026, п. 1; дизайн: срок и риск из чата — «сигнал из чата, не
    эскалация», ОСС их не получает (Рег. 1.11): отгрузка и документы — менеджеру ОРК, доставка и
    оплата — КАМу; договор заказчика не найден — КАМ, поставщика — менеджер ОРК. У каждого
    пункта — шаг и роль: отбор очереди «Недели» по роли и шагу их не теряет."""
    r = fixture.build_realization()
    dc = r["dealctx"]
    rows = {x["deal"]: x for x in r["contracts"]["rows"]}
    for q in dc["queue"]:
        assert q["role"] and q["roleName"], q["code"]
        if q["code"] in ("chatDue", "chatRisk"):
            assert q["role"] != "oss" and q["copyRole"] != "oss" and q["signal"] == dealctx.SIGNAL
    due = next(q for q in dc["queue"] if q["code"] == "chatDue")          # срок оплаты из чата
    assert due["role"] == "kam" and due["who"] == rows[due["deal"]]["kam"] and due["step"] == 13
    risk = next(q for q in dc["queue"] if q["code"] == "chatRisk")
    assert risk["role"] == "orkMgr" and risk["copyRole"] == "kam" and risk["escalateTo"] == rows[risk["deal"]]["kam"]
    miss = {q["deal"]: q for q in dc["queue"] if q["code"] == "contractMissing"}
    assert miss["6007"]["role"] == "kam" and miss["6007"]["copyRole"] == "orkMgr", "обеих сторон нет"
    assert miss["6003"]["role"] == "orkMgr" and miss["6003"]["step"] == 4, "нет договора поставщика"
    # вид срока из чата — по таблице ork.ITEM
    for kind, role in (("отгрузка", "orkMgr"), ("документы", "orkMgr"), ("доставка", "kam"),
                       ("оплата", "kam"), ("встреча", "orkMgr")):
        ow = dealctx._reg("chatDue", {"kam": "К", "stepCell": {"step": 7}}, {"kam": "К"}, key=f"chatDue:{kind}")
        assert ow["role"] == role, kind
        if kind == "документы":                  # отгрузочные или УПД — по шагу сделки, не догадкой
            assert ow["step"] == 7
    # очередь «Недели» = очередь регламента + эти пункты: роль есть у каждого
    life = r["contracts"]["life"]
    week_q = list(life["queue"]) + [q for q in dc["queue"] if not q.get("seg")]
    assert all(q.get("role") for q in week_q)
