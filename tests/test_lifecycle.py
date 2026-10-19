"""Жизненный цикл заказа в реализации: фазы по фактам, очередь «что делать»,
доставка в срок, надёжность поставщиков.

Корпус придуман (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lifecycle as lc  # noqa: E402

СЕГОДНЯ = dt.date(2026, 10, 19)


def д(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def заказ(**kw) -> dict:
    o = {"id": "1", "deal": "100", "seq": 900, "title": "900/1. Насос", "sup": "Поставщик А", "oss": "Ольга (ОСС)",
         "live": True, "success": False}
    o.update(kw)
    return o


def test_фаза_по_фактам_а_не_по_стадии():
    assert lc.phase(заказ(), СЕГОДНЯ) == "place"
    assert lc.phase(заказ(supPaidPct=30), СЕГОДНЯ) == "make"
    assert lc.phase(заказ(prodConf=True), СЕГОДНЯ) == "make"
    assert lc.phase(заказ(shipFact=д("2026-10-01")), СЕГОДНЯ) == "move"
    assert lc.phase(заказ(delFact=д("2026-10-10"), utd=True), СЕГОДНЯ) == "close", "без платежа клиента — закрытие"
    assert lc.phase(заказ(delFact=д("2026-10-10"), utd=True, clientPaid=True), СЕГОДНЯ) == "warranty"
    assert lc.phase(заказ(delFact=д("2025-09-01"), utd=True, clientPaid=True), СЕГОДНЯ) == "done"


def test_исключения_заказа():
    late = lc.order_exceptions(заказ(dl=д("2026-10-01")), СЕГОДНЯ)
    assert ("late", 18) in late
    risk = lc.order_exceptions(заказ(dl=д("2026-10-25")), СЕГОДНЯ)
    assert ("risk", 6) in risk
    assert lc.order_exceptions(заказ(dl=д("2026-10-25"), shipFact=д("2026-10-15")), СЕГОДНЯ) == []
    slip = lc.order_exceptions(заказ(dl=д("2026-12-25"), shipPlan=д("2026-10-09"), delPlan=д("2027-01-10")), СЕГОДНЯ)
    assert ("shipSlip", 10) in slip and ("planAfterDl", 16) in slip
    assert ("noDl", None) in lc.order_exceptions(заказ(), СЕГОДНЯ)
    # доставлено, но заказ не закрыт: не просрочка, а закрытие
    дост = lc.order_exceptions(заказ(dl=д("2026-09-01"), delFact=д("2026-09-10"), payPlan=д("2026-10-10")), СЕГОДНЯ)
    assert ("noUtd", 39) in дост and ("unpaid", 9) in дост and not any(c == "late" for c, _ in дост)
    assert lc.order_exceptions(заказ(live=False, success=True, closedRecent=True), СЕГОДНЯ) == [("noFact", None)]


def test_очередь_сводка_и_доставка_в_срок():
    заказы = [
        заказ(id="1", dl=д("2026-10-01"), supPaidPct=100),
        заказ(id="2", deal="101", seq=901, live=False, success=True, dl=д("2026-09-30"), delFact=д("2026-09-20"),
              created=д("2026-07-01"), shipPlan=д("2026-08-01"), shipFact=д("2026-08-05"), inbFact=д("2026-09-01"),
              utd=True, clientPaid=True),
        заказ(id="3", deal="101", seq=901, live=False, success=True, dl=д("2026-09-01"), delFact=д("2026-09-11"),
              created=д("2026-06-01"), shipPlan=д("2026-07-01"), shipFact=д("2026-07-01"), utd=True, clientPaid=True),
        заказ(id="4", deal="102", seq=902, sup="Поставщик Б", shipPlan=д("2026-08-01"), shipFact=д("2026-08-20")),
        заказ(id="5", deal="103", seq=903),
    ]
    сделки = [
        {"deal": "100", "seq": 900, "customer": "Заказчик 1", "closed": False, "oss": "Ольга (ОСС)"},
        {"deal": "101", "seq": 901, "customer": "Заказчик 2", "closed": False, "oss": "Ольга (ОСС)"},
        {"deal": "102", "seq": 902, "customer": "Заказчик 3", "closed": False, "oss": "—", "manager": "Иван (КАМ)"},
        {"deal": "103", "seq": 903, "customer": "Заказчик 4", "closed": False, "lost": True},
        {"deal": "104", "seq": 904, "customer": "Заказчик 5", "closed": False, "firstLag": 12, "inCat0": True,
         "managerGone": True, "manager": "Пётр (уволен)"},
    ]
    life = lc.build(заказы, сделки, СЕГОДНЯ)
    кк = life["counts"]
    assert кк["late"] == 1 and кк["dealOpen"] == 1 and кк["noOrder"] == 1 and кк["gone"] == 1
    assert "noDl" not in кк or кк["noDl"] == 1, "проигранная сделка 103 не даёт исключений"
    assert life["queue"][0]["code"] == "late" and life["queue"][0]["who"] == "Ольга (ОСС)"
    ф = {p["key"]: p for p in life["phases"]}
    assert ф["make"]["orders"] == 1 and ф["make"]["late"] == 1 and ф["move"]["orders"] == 1
    assert ф["warranty"]["orders"] == 2 and ф["warranty"]["n"] == 1
    сен = next(m for m in life["otd"]["months"] if m["m"] == "09.2026")
    assert (сен["n"], сен["ontime"], сен["pct"], сен["medLate"]) == (2, 1, 50, 10.0)
    assert life["otd"]["last90"] == {"n": 2, "ontime": 1, "pct": 50}
    assert life["lead"]["total"]["n"] == 2 and life["lead"]["transit"]["med"] == 27
    assert life["warranty"]["assumed"] and life["warranty"]["active"] == 2
    # поставщик в рейтинге — от трёх отгрузок с планом и фактом; здесь никого
    assert life["suppliers"] == []
    люди = {p["who"]: p for p in life["people"]}
    assert люди["Ольга (ОСС)"]["n90"] == 2 and люди["Ольга (ОСС)"]["pct90"] == 50
    assert lc._add_months(д("2026-01-31"), 1) == д("2026-02-28")


def test_прогноз_доставки_ранний_сигнал():
    med = {"transit": 20, "lastMile": 10, "toShip": 40}
    # не отгружен: плановая отгрузка + обычное опоздание этого поставщика (+5) + 30 дн. пути
    o = заказ(dl=д("2026-12-01"), shipPlan=д("2026-11-01"), sup="Поставщик А")
    fc = lc.forecast(o, СЕГОДНЯ, med, {"Поставщик А": 5})
    assert fc == д("2026-12-06")
    assert ("forecast", 5) in lc.order_exceptions(o, СЕГОДНЯ, fc)
    # пришёл на склад: остаётся последняя миля
    assert lc.forecast(заказ(inbFact=д("2026-10-15")), СЕГОДНЯ, med, {}) == д("2026-10-25")
    # прогноз в прошлом не бывает; доставленный и закрытый — без прогноза
    assert lc.forecast(заказ(shipFact=д("2026-08-01")), СЕГОДНЯ, med, {}) == СЕГОДНЯ
    assert lc.forecast(заказ(delFact=д("2026-10-01")), СЕГОДНЯ, med, {}) is None
    assert lc.forecast(заказ(), СЕГОДНЯ, {"transit": None}, {}) is None
