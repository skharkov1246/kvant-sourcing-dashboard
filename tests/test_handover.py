"""Подтверждение готовности к передаче в ОРК (handover.py, владелец 01.11.2026): шаг до приёмки,
которого нет в регламенте ОРК. Корпус придуман (CLAUDE.md, правило 18)."""
from __future__ import annotations

import datetime as dt
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import handover  # noqa: E402
import lifecycle  # noqa: E402
import ork  # noqa: E402

TODAY = dt.date(2026, 10, 30)          # пятница


def задача(tid, oid, status="2", created="2026-10-23T17:59:00+03:00", closed="", deadline="2026-10-26T17:59:00+03:00",
           resp="77"):
    return {"id": tid, "status": status, "createdDate": created, "closedDate": closed, "deadline": deadline,
            "responsibleId": resp, "ufCrmTask": [f"Tac_{oid}", "D_900"]}


def заказ(**kw):
    o = {"id": "7001", "deal": "900", "live": True, "stage": "Заказ создан", "stageSince": "2026-10-23T10:00:00+03:00",
         "stepSince": "2026-10-23T10:00:00+03:00", "t0": "2026-10-23T10:00:00+03:00",
         "t0Base": "2026-10-23T10:00:00+03:00", "created": dt.date(2026, 10, 23), "orkMgr": ""}
    o.update(kw)
    return o


def test_разбор_задач_привязка_и_последняя():
    m = handover.parse([задача("1", "7001", status="5", closed="2026-10-24T12:00:00+03:00",
                               created="2026-10-23T10:00:00+03:00"),
                        задача("2", "7001", created="2026-10-27T10:00:00+03:00"),    # повтор — последняя
                        задача("3", "7002", status="5", closed=""),                  # завершена без даты — открыта
                        {"id": "4", "status": "2", "ufCrmTask": ["D_5"]}])            # без заказа — мимо
    assert set(m) == {"7001", "7002"}
    assert m["7001"]["id"] == "2" and m["7001"]["open"] is True
    assert m["7002"]["open"] is True and m["7002"]["resp"] == "77"


def test_открытая_задача_приёмка_не_началась():
    o = заказ(hoOpen=True, hoSince=dt.date(2026, 10, 23), hoDl=dt.date(2026, 10, 26), hoWho="Исполнитель")
    v = ork.judged(o, TODAY)
    assert v["step"] == 1 and v["waitHo"] and v["over"] is None
    assert not v["counted"] and not v["termUnknown"]          # ни «сверх нормы», ни «срок неизвестен»
    assert v["wd"] == 5 and v["hoOverdue"] and v["hoBy"] == 4  # 23.10 → 30.10; срок 26.10 прошёл
    без = ork.judged(заказ(), TODAY)
    assert без["over"] is True and not без["waitHo"]          # без задачи — как раньше: дольше нормы


def test_закрытая_задача_приёмка_от_закрытия():
    o = заказ(t0="2026-10-29T15:00:00+03:00", t0By="подтверждение готовности")
    v = ork.judged(o, TODAY)
    assert v["since"] == "2026-10-29" and v["sinceBy"] == "подтверждение готовности"
    assert v["wd"] == 1 and v["over"] is False                # от закрытия — в норме
    assert lifecycle.since_text(v["since"], v["sinceBy"], o, TODAY) == "с 29.10 (подтверждена готовность)"


def test_назначение_до_подтверждения_в_счёте():
    # ОРК назначил менеджера 24.10, задачу закрыли 27.10: назначению верим от прежнего начала
    o = заказ(t0="2026-10-27T12:00:00+03:00", assignDate=dt.date(2026, 10, 24), orkMgr="Менеджер")
    assert ork.assign_at(o) == dt.date(2026, 10, 24) and ork.step_now(o) == ork.ASSIGN_FLOOR


def test_сегменты_s1():
    tr = [("C1", "2026-10-23T10:00:00+03:00"), ("C2", "2026-10-29T10:00:00+03:00")]
    names = {"C1": "Заказ создан", "C2": "Заказ направлен"}
    # открыта — S1 не начат
    s = ork.segments(tr, names, t0="2026-10-23T10:00:00+03:00", facts={"hoOpen": True})
    assert "S1" not in s["segs"]
    # закрыта 28.10 — S1 от закрытия до выхода на шаг 4
    s = ork.segments(tr, names, t0="2026-10-28T10:00:00+03:00",
                     facts={"t0Base": "2026-10-23T10:00:00+03:00", "t0By": "подтверждение готовности"})
    assert s["segs"]["S1"]["wd"] == 1 and s["segs"]["S1"]["startBy"] == "подтверждение готовности"
    # ОРК взял заказ (назначение 24.10) до закрытия 29.10 — S1 0 р.д., а не «порядок нарушен»
    s = ork.segments(tr, names, t0="2026-10-29T18:00:00+03:00",
                     facts={"t0Base": "2026-10-23T10:00:00+03:00", "assignAt": dt.date(2026, 10, 24)})
    assert s["segs"]["S1"]["wd"] == 0 and not s["segs"]["S1"]["neg"] and not s["assignIgnored"]


def test_пункт_очереди_у_исполнителя_задачи():
    o = заказ(hoOpen=True, hoSince=dt.date(2026, 10, 23), hoDl=dt.date(2026, 10, 26), hoWho="Исполнитель",
              _pid={"hoResp": "77"}, dl=dt.date(2026, 12, 30), prodStart=dt.date(2026, 11, 1),
              prodEnd=dt.date(2026, 11, 20), delBudget=dt.date(2026, 12, 20))
    exc = lifecycle.order_exceptions(o, TODAY)
    codes = [c for c, _ in exc]
    assert "hoOver" in codes and "s1Over" not in codes
    assert dict(exc)["hoOver"] == 4
    ow = ork.item_owner("hoOver", 1, {"hoResp": "Исполнитель", "orkHead": "Руководитель"})
    assert ow["role"] == "hoResp" and ow["who"] == "Исполнитель" and ow["copy"] == "Руководитель"
    # срок задачи не прошёл — пункта нет
    o2 = dict(o, hoDl=dt.date(2026, 11, 2))
    assert "hoOver" not in [c for c, _ in lifecycle.order_exceptions(o2, TODAY)]


def test_чтение_отказ_портала_как_без_задач():
    class Отказ:
        def call_envelope(self, *a, **k):
            raise RuntimeError("tasks.task.list: ACCESS_DENIED")
    m, st = handover.read(Отказ())
    assert m == {} and st == "ACCESS_DENIED"

    class Ок:
        def call_envelope(self, method, params):
            assert params["filter"]["%TITLE"] == handover.TITLE
            return {"result": {"tasks": [задача("1", "7001")]}}
    m, st = handover.read(Ок())
    assert st == "ок" and m["7001"]["open"]


def test_вкладка_на_корпусе_с_задачами():
    """Корпус с задачами: открытая просроченная — заказ ждёт подтверждения (stepWaitHo), пункт
    hoOver у исполнителя задачи, ведомость шага 1 считает его отдельно; закрытая — приёмка от
    закрытия; задачи прочитаны («ок»)."""
    from tests import fixture
    ct = fixture.build_contracts(stub=fixture.ContractsStub(ho=True))
    week = ct["ork"]["week"]
    assert week["handover"]["read"] == "ок"
    rows = [o for r in ct["rows"] for o in r.get("orders") or []]
    wait = [o for o in rows if o.get("stepWaitHo")]
    assert wait and all(o["stepOver"] is None and o.get("hoWho") for o in wait)
    q = [x for x in ct["life"]["queue"] if x["code"] == "hoOver"]
    assert all(x["role"] == "hoResp" and x["roleName"] == handover.ROLE and x["inWd"] for x in q)
    assert {x["order"] for x in q} <= {o["id"] for o in wait}
    led1 = next(s for s in week["ledger"] if s["no"] == 1)
    assert led1["ordersWaitHo"] == week["handover"]["n"] == len([o for o in wait if o["live"]])
    assert week["handover"]["overdue"] == len({x["order"] for x in q})
    без = fixture.build_contracts()
    assert без["ork"]["week"]["handover"]["n"] == 0 and без["ork"]["week"]["handover"]["read"] == "ок"
