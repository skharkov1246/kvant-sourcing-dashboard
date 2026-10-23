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


# обязательные даты размещения заполнены — иначе каждый заказ корпуса давал бы «noPlanDates»
ДАТЫ = {"prodStart": dt.date(2026, 5, 1), "prodEnd": dt.date(2026, 7, 1), "delBudget": dt.date(2026, 9, 1)}


def заказ(**kw) -> dict:
    o = {"id": "1", "deal": "100", "seq": 900, "title": "900/1. Насос", "sup": "Поставщик А", "oss": "Ольга (ОСС)",
         "live": True, "success": False, **ДАТЫ}
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
        заказ(id="4", deal="102", seq=902, sup="Поставщик Б", shipPlan=д("2026-08-20"), prodEnd=д("2026-08-01"),
              shipFact=д("2026-08-20")),
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
    # не отгружен: бюджетный конец производства (31.10) + обычное опоздание этого поставщика (+5)
    # позже плановой отгрузки (01.11) — берём позднее; + 30 дн. пути
    o = заказ(dl=д("2026-12-01"), shipPlan=д("2026-11-01"), prodEnd=д("2026-10-31"), sup="Поставщик А")
    fc = lc.forecast(o, СЕГОДНЯ, med, {"Поставщик А": 5})
    assert fc == д("2026-12-05")
    assert ("forecast", 4) in lc.order_exceptions(o, СЕГОДНЯ, fc)
    # без бюджетной даты — плановая отгрузка как есть: её и так переписывают под реальность
    assert lc.forecast(заказ(shipPlan=д("2026-11-01"), sup="Поставщик А"), СЕГОДНЯ, med, {"Поставщик А": 5}) == д("2026-12-01")
    # пришёл на склад: остаётся последняя миля
    assert lc.forecast(заказ(inbFact=д("2026-10-15")), СЕГОДНЯ, med, {}) == д("2026-10-25")
    # прогноз в прошлом не бывает; доставленный и закрытый — без прогноза
    assert lc.forecast(заказ(shipFact=д("2026-08-01")), СЕГОДНЯ, med, {}) == СЕГОДНЯ
    assert lc.forecast(заказ(delFact=д("2026-10-01")), СЕГОДНЯ, med, {}) is None
    assert lc.forecast(заказ(), СЕГОДНЯ, {"transit": None}, {}) is None


def test_разбор_ревью_старые_доставки_прогноз_склад_и_хозяин():
    """Дефекты, найденные ревью до пуша (19.10.2026)."""
    # закрытый заказ, доставленный давно без файла платёжки: не долг клиента, а закрытие пройдено
    старый = заказ(live=False, success=True, delFact=д("2024-11-20"), utd=True)
    assert lc.order_exceptions(старый, СЕГОДНЯ) == [] and lc.phase(старый, СЕГОДНЯ) == "done"
    # живой заказ, доставленный больше года назад: тоже вне очереди закрытия
    assert lc.order_exceptions(заказ(delFact=д("2025-06-01"), dl=д("2025-07-01")), СЕГОДНЯ) == []
    # плановая отгрузка давно прошла, не отгружен: прогноз = сегодня + путь, а не «сегодня»
    o = заказ(dl=д("2026-11-08"), shipPlan=д("2026-08-20"))
    fc = lc.forecast(o, СЕГОДНЯ, {"transit": 25, "lastMile": 10}, {})
    assert fc == д("2026-11-23") and ("forecast", 15) in lc.order_exceptions(o, СЕГОДНЯ, fc)
    # пришёл на склад: не «поставщик не отгрузил»
    assert lc.order_exceptions(заказ(dl=д("2026-10-25"), inbFact=д("2026-10-10"), shipPlan=д("2026-10-01")), СЕГОДНЯ) == []
    заказы = [заказ(id="1", deal="100", oss="", dl=д("2026-10-01")),          # хозяин — ОСС сделки
              заказ(id="2", deal="", dl=д("2026-09-01")),                      # без сделки — вне вселенной
              заказ(id="3", deal="101", dl=д("2026-09-01"))]                   # сделка выиграна и закрыта
    сделки = [{"deal": "100", "seq": 900, "oss": "Ольга (ОСС), Глеб (ОСС)", "ossDeal": "Дина (ОСС) (уволен)",
               "manager": "Пётр (ответственный)", "kam": "Анна (КАМ)", "closed": False},
              {"deal": "101", "seq": 901, "closed": True, "lost": False}]
    life = lc.build(заказы, сделки, СЕГОДНЯ)
    assert life["counts"] == {"late": 1}
    q = life["queue"][0]
    assert q["who"] == "Дина (ОСС) (уволен)" and q["whoGone"] is True and q["kam"] == "Анна (КАМ)"


def test_надёжность_поставщика_к_бюджетной_дате_а_не_к_переписанному_плану():
    """Плановую отгрузку переписывают под факт — с ней поставщик всегда «в срок»."""
    заказы = [заказ(id=str(i), sup="Поставщик В", shipPlan=д("2026-09-10"), shipFact=д("2026-09-10"),
                    prodEnd=д("2026-08-20")) for i in range(3)]
    lead, sup = lc.speeds(заказы, СЕГОДНЯ)
    assert sup["Поставщик В"] == [21, 21, 21] and lead["shipSlip"] == [21, 21, 21]



def test_решения_владельца_хозяин_поля_сделка_в_срок_нормы():
    """Решения владельца 19.10.2026: хозяин — ответственный заказа (п. 1); сделка в срок —
    все заказы к сроку (п. 3); норма звена — 75-й процентиль (п. 4); обязательные даты
    размещения и плановый срок оплаты при доставке (п. 9)."""
    # п. 9: нет дат размещения — пункт с перечнем; доставлен без планового срока оплаты — пункт
    o = заказ(prodStart=None, delBudget=None, dl=д("2026-12-01"))
    assert ("noPlanDates", None) in lc.order_exceptions(o, СЕГОДНЯ)
    assert ("noPayPlan", 9) in lc.order_exceptions(заказ(delFact=д("2026-10-10"), utd=True, clientPaid=True), СЕГОДНЯ)
    заказы = [
        o,
        # сделка 101: два заказа, оба к сроку — сделка в срок
        заказ(id="2", deal="101", live=False, success=True, dl=д("2026-09-30"), delFact=д("2026-09-20"),
              created=д("2026-06-01"), shipFact=д("2026-08-01"), inbFact=д("2026-08-10"), utd=True, clientPaid=True,
              payPlan=д("2026-10-20")),
        заказ(id="3", deal="101", live=False, success=True, dl=д("2026-09-30"), delFact=д("2026-09-25"),
              created=д("2026-06-01"), shipFact=д("2026-08-01"), inbFact=д("2026-08-10"), utd=True, clientPaid=True,
              payPlan=д("2026-10-20")),
        # сделка 102: один заказ опоздал — сделка не в срок, хотя другой к сроку
        заказ(id="4", deal="102", live=False, success=True, dl=д("2026-09-01"), delFact=д("2026-09-11"),
              created=д("2026-06-01"), shipFact=д("2026-08-01"), inbFact=д("2026-08-10"), utd=True, clientPaid=True,
              payPlan=д("2026-10-20")),
        заказ(id="5", deal="102", live=False, success=True, dl=д("2026-09-30"), delFact=д("2026-09-20"),
              created=д("2026-06-01"), shipFact=д("2026-08-01"), inbFact=д("2026-08-10"), utd=True, clientPaid=True,
              payPlan=д("2026-10-20")),
        # живой заказ в производстве 200 дней при норме 61 — сверх нормы; хозяин — ответственный заказа
        заказ(id="6", deal="103", resp="Роман (ответственный заказа)", created=д("2026-04-02"), supPaidPct=50,
              dl=д("2027-03-01")),
    ]
    сделки = [{"deal": d, "seq": 900 + i, "closed": False} for i, d in enumerate(("100", "101", "102", "103"))]
    life = lc.build(заказы, сделки, СЕГОДНЯ)
    assert life["otd"]["deals90"] == {"n": 2, "ontime": 1, "pct": 50}
    assert life["otd"]["last90"]["n"] == 4 and life["otd"]["last90"]["ontime"] == 3
    assert life["norms"]["toShip"] == 61
    ф = {p["key"]: p for p in life["phases"]}
    assert ф["make"]["over"] == 1
    assert {p["who"] for p in life["people"]} >= {"Роман (ответственный заказа)"}
    q = next(x for x in life["queue"] if x["code"] == "noPlanDates")
    assert q["what"].endswith("начало производства, доставка клиенту") and q["who"] == "Ольга (ОСС)"
    # закрытая выигранная сделка: ни сроков, ни оплаты в очереди
    life2 = lc.build([заказ(dl=д("2026-10-01"), prodStart=None)], [{"deal": "100", "seq": 900, "closed": True}], СЕГОДНЯ)
    assert life2["counts"] == {}


def test_ведёт_уволенный_и_ответственный_заказа():
    """Ревизия 23.10.2026: пункт «ведёт уволенный» — и по ответственному живого заказа
    (respGone строки сделки), в пункте названо, кто ушёл; закрытая сделка — без пункта."""
    сделки = [{"deal": "100", "seq": 900, "closed": False, "respGone": True, "ossDeal": "Дина (ОСС)"},
              {"deal": "101", "seq": 901, "closed": False, "managerGone": True, "ossGone": True,
               "manager": "Пётр (уволен)"},
              {"deal": "102", "seq": 902, "closed": True, "respGone": True},
              {"deal": "103", "seq": 903, "closed": False}]
    заказы = [заказ(id=str(i), deal=d, dl=д("2026-12-01")) for i, d in enumerate(("100", "101", "102", "103"))]
    life = lc.build(заказы, сделки, СЕГОДНЯ)
    g = {q["deal"]: q for q in life["queue"] if q["code"] == "gone"}
    assert set(g) == {"100", "101"} and life["counts"]["gone"] == 2
    assert g["100"]["what"] == "ведёт уволенный: ответственный заказа" and g["100"]["who"] == "Дина (ОСС)"
    assert g["101"]["what"] == "ведёт уволенный: ответственный сделки, ОСС" and g["101"]["whoGone"] is True
    assert life["labels"]["gone"] == "ведёт уволенный", "подпись фильтра — без перечня"


def test_доставлено_по_стадии_без_даты_не_просрочка():
    """Заказ закрыт успехом (семантика S), срок клиента прошёл, даты доставки нет: не
    «срок сорван», а «внести фактическую дату доставки»; в плитки фаз и в «в срок» не идёт."""
    o = заказ(live=False, success=True, closedRecent=True, dl=д("2026-09-01"))
    assert lc.order_exceptions(o, СЕГОДНЯ) == [("noFact", None)]
    assert lc.phase(o, СЕГОДНЯ) == "done"
    life = lc.build([o], [{"deal": "100", "seq": 900, "closed": False}], СЕГОДНЯ)
    assert "late" not in life["counts"] and life["counts"]["noFact"] == 1
    assert all(p["late"] == 0 and p["orders"] == 0 for p in life["phases"])
    assert life["otd"]["last90"]["n"] == 0 and life["otd"]["deals90"]["n"] == 0
    # живой заказ без даты доставки при прошедшем сроке — просрочка, как и была
    assert ("late", 48) in lc.order_exceptions(заказ(dl=д("2026-09-01")), СЕГОДНЯ)
