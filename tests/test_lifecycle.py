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
    # бюджетный конец производства (01.07) прошёл, заказ не готов — prodDev (вместо прежнего
    # shipSlip: плановую отгрузку переписывают под факт)
    slip = lc.order_exceptions(заказ(dl=д("2026-12-25"), shipPlan=д("2026-10-09"), delPlan=д("2027-01-10")), СЕГОДНЯ)
    assert ("prodDev", 110) in slip and ("planAfterDl", 16) in slip
    assert not any(c == "shipSlip" for c, _ in slip)
    assert ("noDl", None) in lc.order_exceptions(заказ(), СЕГОДНЯ)
    # доставлено, но заказ не закрыт: не просрочка, а закрытие
    дост = lc.order_exceptions(заказ(dl=д("2026-09-01"), delFact=д("2026-09-10"), payPlan=д("2026-10-10")), СЕГОДНЯ)
    # УПД — в рабочих днях от доставки: 10.09 → 19.10 — 27 р.д.
    assert ("noUtd", 27) in дост and ("unpaid", 9) in дост and not any(c == "late" for c, _ in дост)
    assert lc.order_exceptions(заказ(live=False, success=True, closedRecent=True), СЕГОДНЯ) == [("noFact", None)]


def test_очередь_сводка_и_доставка_в_срок():
    заказы = [
        заказ(id="1", dl=д("2026-10-01"), supPaidPct=100, orkMgr="Илья (ОРК)"),
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
        {"deal": "104", "seq": 904, "customer": "Заказчик 5", "closed": False, "inCat0": True,
         "cat0At": "2026-10-01T10:00:00+03:00", "managerGone": True, "manager": "Пётр (уволен)"},
    ]
    life = lc.build(заказы, сделки, СЕГОДНЯ)
    кк = life["counts"]
    assert кк["late"] == 1 and кк["dealOpen"] == 1 and кк["noOrder"] == 1 and кк["gone"] == 1
    assert "noDl" not in кк or кк["noDl"] == 1, "проигранная сделка 103 не даёт исключений"
    # хозяин — исполнитель шага (решение 26.10.2026, п. 1): срок сорван — менеджер ОРК
    q0 = life["queue"][0]
    assert q0["code"] == "late" and q0["who"] == "Илья (ОРК)" and q0["role"] == "orkMgr"
    assert life["queue"][0]["escalateTo"] == "не назначен" and q0["copyRole"] == "kam", "КАМа в сделке нет"
    no = next(q for q in life["queue"] if q["code"] == "noOrder")
    assert no["days"] == 12 and no["role"] == "orkHead" and no["who"] == "руководитель ОРК", "12 р.д. без заказа"
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
    # «хозяева процесса» поимённо в данные вкладки не идут (решение 26.10.2026, п. 3)
    assert "people" not in life
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
    assert life["counts"] == {"late": 1, "prodDev": 1}
    q = next(x for x in life["queue"] if x["code"] == "late")
    # хозяин срока — менеджер ОРК (исполнитель шага), его нет — «не назначен»; КАМ — в копии
    assert q["who"] == "не назначен" and q["whoGone"] is False and q["kam"] == "Анна (КАМ)"
    assert q["escalateTo"] == "Анна (КАМ)" and q["step"] == 1, "шаг — текущий шаг заказа (по фактам — 1)"


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
    # «сверх нормы» — по нормам регламента (решение 26.10.2026, п. 2): производство не
    # нормируется, 200 дней от создания уже не «сверх нормы»; p75 — только справочно
    assert ф["make"]["over"] == 0
    # ответственный заказа не из отдела ОРК — не менеджер ОРК: исполнитель «не назначен»
    assert "people" not in life
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
    # переназначает руководитель ОРК (шаг 1); его нет в справочнике — подпись роли
    assert g["100"]["what"] == "ведёт уволенный: ответственный заказа" and g["100"]["who"] == "руководитель ОРК"
    assert g["101"]["what"] == "ведёт уволенный: ответственный сделки, ОСС" and g["101"]["role"] == "orkHead"
    q = next(x for x in lc.build(заказы, сделки, СЕГОДНЯ, ork_head="Роза (рук. ОРК)")["queue"] if x["code"] == "gone")
    assert q["who"] == "Роза (рук. ОРК)"
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


def test_даты_доставки_нет_берётся_дата_упд():
    """Решение владельца 25.10.2026: «доставлено, даты доставки нет» — прежде чем ставить
    пункт, искать УПД и брать дату доставки из него. Доставка в срок — только по факту."""
    закрыт = заказ(live=False, success=True, closedRecent=True, dl=д("2026-09-01"),
                   utdDate=д("2026-09-05"), utd=True)
    assert lc.order_exceptions(закрыт, СЕГОДНЯ) == [], "дата УПД есть — пункта «нет даты доставки» нет"
    assert lc.delivered(закрыт) == д("2026-09-05")
    assert lc.phase(закрыт, СЕГОДНЯ) == "warranty", "гарантия — от даты УПД"
    # живой заказ: срок прошёл, факта нет, УПД подписан — доставлен, это закрытие, не просрочка
    живой = заказ(dl=д("2026-09-01"), utdDate=д("2026-09-20"), utd=True, payPlan=д("2026-10-30"))
    исключения = lc.order_exceptions(живой, СЕГОДНЯ)
    assert not any(c in ("late", "risk", "forecast", "noDl") for c, _ in исключения)
    assert lc.phase(живой, СЕГОДНЯ) == "close", "без платёжки клиента — закрытие"
    assert lc.forecast(живой, СЕГОДНЯ, {"transit": 10, "lastMile": 5}, {}) is None
    # факт есть — он главнее даты УПД
    assert lc.delivered(заказ(delFact=д("2026-09-10"), utdDate=д("2026-09-20"))) == д("2026-09-10")
    life = lc.build([закрыт, {**живой, "id": "2"}], [{"deal": "100", "seq": 900, "closed": False}], СЕГОДНЯ)
    assert life["byUtd"] == 2 and "noFact" not in life["counts"] and "late" not in life["counts"]
    # доставка в срок и хозяева за 90 дней — только по факту: подпись УПД бывает позже приёмки
    assert life["otd"]["last90"]["n"] == 0 and life["otd"]["deals90"]["n"] == 0


def test_пункт_очереди_помечен_по_упд():
    живой = заказ(dl=д("2026-09-01"), utdDate=д("2026-09-20"), utd=False)
    life = lc.build([живой], [{"deal": "100", "seq": 900, "closed": False}], СЕГОДНЯ)
    assert life["queue"] and all(q["byUtd"] for q in life["queue"])
    сфактом = заказ(dl=д("2026-09-01"), delFact=д("2026-09-10"))
    life = lc.build([сфактом], [{"deal": "100", "seq": 900, "closed": False}], СЕГОДНЯ)
    assert life["queue"] and not any(q["byUtd"] for q in life["queue"])


# ── регламент ОРК (решения владельца 26.10.2026): нормы в р.д., хозяин — исполнитель шага ──
def test_нет_упд_с_11_рабочего_дня_по_сырой_дате_доставки():
    """10 р.д. от доставки — ещё в норме, 11-й — пункт (Рег. 14.3.1). Дни — рабочие."""
    в_норме = заказ(dl=д("2026-12-01"), delFact=д("2026-10-05"), payPlan=д("2026-12-01"))
    assert not any(c == "noUtd" for c, _ in lc.order_exceptions(в_норме, СЕГОДНЯ)), "10 р.д. — норма"
    сверх = заказ(dl=д("2026-12-01"), delFact=д("2026-10-02"), payPlan=д("2026-12-01"))
    assert ("noUtd", 11) in lc.order_exceptions(сверх, СЕГОДНЯ)
    # 11 р.д. — это всегда больше 14 календарных: новый счёт не шире прежнего (14 кал.)
    for k in range(0, 60):
        o = заказ(dl=д("2026-12-01"), delFact=СЕГОДНЯ - dt.timedelta(days=k), payPlan=д("2026-12-01"))
        if any(c == "noUtd" for c, _ in lc.order_exceptions(o, СЕГОДНЯ)):
            assert k > 14, k
    # даты доставки нет, есть подставленная дата УПД: УПД подписан — пункта нет и быть не может
    по_упд = заказ(dl=д("2026-12-01"), utdDate=д("2026-09-01"), utd=True, payPlan=д("2026-12-01"))
    assert not any(c == "noUtd" for c, _ in lc.order_exceptions(по_упд, СЕГОДНЯ))


def test_год_без_календаря_пункта_нет_и_счёт(monkeypatch):
    """Года нет в календаре — срок менеджера не посчитать: пункта нет, счёт — в noCalendar,
    без подмены на Пн–Пт (ноль за неизвестное не ставим)."""
    import period
    monkeypatch.setattr(period, "workdays_ru", lambda a, b: None)
    o = заказ(dl=д("2026-12-01"), delFact=д("2026-09-01"), payPlan=д("2026-12-01"))
    nocal = __import__("collections").Counter()
    assert not any(c == "noUtd" for c, _ in lc.order_exceptions(o, СЕГОДНЯ, nocal=nocal))
    assert nocal["noUtd"] == 1
    life = lc.build([o], [{"deal": "100", "seq": 900, "closed": False}], СЕГОДНЯ)
    assert life["noCalendar"] == {"noUtd": 1} and "noUtd" not in life["counts"]


def test_нет_заказа_больше_1_рабочего_дня():
    сделки = [{"deal": "200", "seq": 1, "closed": False, "inCat0": True, "cat0At": "2026-10-16T10:00:00+03:00"},
              {"deal": "201", "seq": 2, "closed": False, "inCat0": True, "cat0At": "2026-10-15T10:00:00+03:00"},
              {"deal": "202", "seq": 3, "closed": False, "inCat0": True, "cat0At": ""}]
    life = lc.build([], сделки, СЕГОДНЯ)
    q = {x["deal"]: x for x in life["queue"]}
    assert set(q) == {"201"}, "пятница → понедельник — 1 р.д., норма; четверг → 2 р.д. — пункт; без даты — нет"
    assert q["201"]["days"] == 2 and q["201"]["inWd"] is True and q["201"]["copyRole"] == "kam"


def test_производство_не_готово_по_всем_переходам():
    """prodDev: бюджетный конец прошёл, заказ ни разу не выходил на «Заказ готов» (шаг 8) —
    по всем переходам (reachMax), не только по текущей стадии: вернувшийся назад заказ
    производство уже прошёл."""
    o = заказ(dl=д("2027-01-01"), prodEnd=д("2026-10-01"), stage="Производство заказа")
    assert ("prodDev", 18) in lc.order_exceptions(o, СЕГОДНЯ)
    assert not any(c == "prodDev" for c, _ in lc.order_exceptions({**o, "reachMax": 8}, СЕГОДНЯ))
    assert not any(c == "prodDev" for c, _ in lc.order_exceptions({**o, "stage": "Заказ готов"}, СЕГОДНЯ))
    assert not any(c == "prodDev" for c, _ in lc.order_exceptions({**o, "shipFact": д("2026-10-10")}, СЕГОДНЯ))


def test_сверх_нормы_шага_по_стадии_и_гигиена():
    """s2Over/s3Over/s6Over/s1Over — р.д. от входа в шаг больше нормы; карточка с фактами
    впереди стадии и брошенная — не пункт, а гигиена."""
    s2 = заказ(dl=д("2027-01-01"), stage="Заказ направлен", stepSince="2026-10-14T10:00:00+03:00")
    assert ("s2Over", 3) in lc.order_exceptions(s2, СЕГОДНЯ)
    assert not any(c == "s2Over" for c, _ in lc.order_exceptions({**s2, "stepSince": "2026-10-15T10:00:00+03:00"},
                                                                  СЕГОДНЯ)), "2 р.д. — в норме"
    s3 = заказ(dl=д("2027-01-01"), stage="Согласование спецификации и оплаты",
               stepSince="2026-10-01T10:00:00+03:00", stageSince="2026-10-16T10:00:00+03:00")
    assert ("s3Over", 12) in lc.order_exceptions(s3, СЕГОДНЯ), "шаг 5 — от входа в шаг, не в стадию"
    позади = {**s2, "supPaidPct": 50}
    assert not any(c == "s2Over" for c, _ in lc.order_exceptions(позади, СЕГОДНЯ)), "стадия позади фактов"
    брошен = заказ(dl=д("2027-01-01"), stage="Заказ создан", t0="2026-01-10T10:00:00+03:00")
    assert not any(c == "s1Over" for c, _ in lc.order_exceptions(брошен, СЕГОДНЯ)), "брошенная карточка"
    s1 = {**брошен, "t0": "2026-10-14T10:00:00+03:00"}
    life = lc.build([s1], [{"deal": "100", "seq": 900, "closed": False, "kam": "Анна (КАМ)"}], СЕГОДНЯ,
                    ork_head="Роза (рук. ОРК)")
    q = next(x for x in life["queue"] if x["code"] == "s1Over")
    assert (q["days"], q["role"], q["who"], q["escalateTo"], q["inWd"]) == (3, "orkHead", "Роза (рук. ОРК)",
                                                                           "Анна (КАМ)", True)


def test_хозяин_по_таблице_пунктов():
    """unpaid — КАМ, ОСС в копии (п. 4); noPayPlan — ОСС; noFact — логистика; noUtd —
    менеджер ОРК заказа, КАМ в копии; без менеджера ОРК заказа — поле сделки."""
    o = заказ(dl=д("2026-09-01"), delFact=д("2026-09-10"), oss="Ольга (ОСС)")
    d = {"deal": "100", "seq": 900, "closed": False, "kam": "Анна (КАМ)", "kamId": "10", "orkResp": "Пётр (ОРК)"}
    q = {x["code"]: x for x in lc.build([o], [d], СЕГОДНЯ)["queue"]}
    assert (q["unpaid"]["role"], q["unpaid"]["who"], q["unpaid"]["escalateTo"]) == ("kam", "Анна (КАМ)", "Ольга (ОСС)")
    assert (q["noPayPlan"]["role"], q["noPayPlan"]["who"]) == ("oss", "Ольга (ОСС)")
    assert (q["noUtd"]["role"], q["noUtd"]["who"], q["noUtd"]["step"]) == ("orkMgr", "Пётр (ОРК)", 12)
    q2 = {x["code"]: x for x in lc.build([{**o, "orkMgr": "Илья (ОРК)"}], [d], СЕГОДНЯ)["queue"]}
    assert q2["noUtd"]["who"] == "Илья (ОРК)", "ответственный заказа из ОРК — первым"
    закрыт = заказ(live=False, success=True, closedRecent=True)
    q3 = next(x for x in lc.build([закрыт], [d], СЕГОДНЯ)["queue"] if x["code"] == "noFact")
    assert (q3["role"], q3["who"]) == ("log", "логистика")


def test_оплата_получена_сделка_не_закрыта_и_оплата_без_договора():
    """Решение 26.10.2026, п. 4: «Оплата получена» — успех, но незакрытая сделка — пункт ОСС
    dealClose; оплачено поставщику, а сделка ни разу не была на шаге «договор подписан» —
    payNoContract руководителю ОРК, КАМ в копии."""
    сделки = [{"deal": "300", "seq": 1, "closed": True, "paidStage": True, "stageDays": 40.4,
               "ossDeal": "Ольга (ОСС)"},
              {"deal": "301", "seq": 2, "closed": False, "dealStepMax": 2, "kam": "Анна (КАМ)"},
              {"deal": "302", "seq": 3, "closed": False, "dealStepMax": 4}]
    заказы = [заказ(id="1", deal="301", dl=д("2027-01-01"), supPaidPct=30),
              заказ(id="2", deal="302", dl=д("2027-01-01"), supPaidPct=30)]
    life = lc.build(заказы, сделки, СЕГОДНЯ)
    q = {(x["code"], x["deal"]): x for x in life["queue"]}
    dc = q[("dealClose", "300")]
    assert (dc["role"], dc["who"], dc["days"]) == ("oss", "Ольга (ОСС)", 40)
    pn = q[("payNoContract", "301")]
    assert (pn["role"], pn["who"], pn["escalateTo"]) == ("orkHead", "руководитель ОРК", "Анна (КАМ)")
    assert ("payNoContract", "302") not in q, "договор по стадии подписан"
    assert life["counts"]["dealClose"] == 1


def test_оплата_без_договора_договор_заказчика_в_разборе():
    """Дизайн, шаг 5: payNoContract — нет ни стадии «Договор подписан», ни договора заказчика в
    разборе договоров (dealctx): стадия сделки отстаёт, а договор уже в портале — пункта нет."""
    сделки = [{"deal": "301", "seq": 2, "closed": False, "dealStepMax": 2, "kam": "Анна (КАМ)"}]
    заказы = [заказ(id="1", deal="301", dl=д("2027-01-01"), supPaidPct=30)]
    assert any(x["code"] == "payNoContract" for x in lc.build(заказы, сделки, СЕГОДНЯ)["queue"])
    life = lc.build(заказы, сделки, СЕГОДНЯ, cust_contract={"301"})
    assert not any(x["code"] == "payNoContract" for x in life["queue"]), "договор заказчика найден"
    # разбор договоров не загружен (None) — «не найден» не проверялось: пункт так и пишет
    q = next(x for x in lc.build(заказы, сделки, СЕГОДНЯ)["queue"] if x["code"] == "payNoContract")
    assert "не проверено" in q["what"]
    q = next(x for x in lc.build(заказы, сделки, СЕГОДНЯ, cust_contract=set())["queue"]
             if x["code"] == "payNoContract")
    assert "не найден" in q["what"] and "не проверено" not in q["what"]


def test_приёмка_только_у_сделок_реализации():
    """Рег. 3.3: 1 р.д. приёмки — от входа сделки в «Реализацию»; заказ сделки вне воронки
    приёмкой не мерится (как и noOrder)."""
    o = заказ(dl=д("2027-01-01"), stage="Заказ создан", t0="2026-10-14T10:00:00+03:00")
    в = lc.build([o], [{"deal": "100", "seq": 900, "closed": False, "inCat0": True}], СЕГОДНЯ)["queue"]
    вне = lc.build([o], [{"deal": "100", "seq": 900, "closed": False, "inCat0": False}], СЕГОДНЯ)["queue"]
    assert any(x["code"] == "s1Over" for x in в) and not any(x["code"] == "s1Over" for x in вне)


def test_долг_клиента_урегулирует_кам():
    """Решение 26.10.2026, п. 4: действие пункта неоплаты — «урегулировать долг», хозяин КАМ."""
    assert lc.EXC["unpaid"][2].startswith("урегулировать долг")


def test_ждём_упд_пункт_есть_а_сверх_нормы_нет():
    """Решение владельца 27.10.2026, Б: доставлено 11+ р.д. назад без УПД — пункт noUtd у
    менеджера ОРК, КАМ в копии, дни рабочие; в «сверх нормы» фазы (ph.over) не идёт: это
    ожидание клиента, а не срок работы менеджера. Шаг 4 сверх нормы — идёт."""
    ждём = заказ(dl=д("2026-12-01"), delFact=д("2026-09-21"), payPlan=д("2026-12-01"),
                 stage="Ожидание оплаты", orkMgr="Илья (ОРК)")
    шаг4 = заказ(id="2", dl=д("2026-12-01"), stage="Заказ направлен", stageSince=д("2026-10-01"),
                 orkMgr="Илья (ОРК)")
    life = lc.build([ждём, шаг4], [{"deal": "100", "seq": 900, "closed": False, "kam": "Анна (КАМ)"}], СЕГОДНЯ)
    q = {x["code"]: x for x in life["queue"]}
    assert (q["noUtd"]["step"], q["noUtd"]["role"], q["noUtd"]["escalateTo"], q["noUtd"]["inWd"]) == (
        12, "orkMgr", "Анна (КАМ)", True)
    assert q["noUtd"]["days"] == 20 and "s2Over" in q
    ph = {p["key"]: p for p in life["phases"]}
    assert ph["close"]["over"] == 0 and ph["close"]["orders"] == 1
    assert sum(p["over"] for p in life["phases"]) == 1, "сверх нормы — только шаг 4"


# ── номера заказов в очереди (владелец 29.10.2026: «сделка одна — заказы разные 1/2/3») ────
def test_сделка_три_заказа_три_строки_по_номеру():
    """Одна сделка, три заказа с разными поставщиками и сроками клиента — три пункта «срок
    клиента прошёл» с номером заказа, поставщиком и своим сроком; подряд и по номеру заказа
    (пункт другой сделки с днями между ними их не разрывает)."""
    заказы = [заказ(id="3", title="900/3. Клапан", sup="Поставщик В", dl=д("2026-10-09")),
              заказ(id="1", title="900/1. Насос", sup="Поставщик А", dl=д("2025-10-15")),
              заказ(id="2", title="900/2. Привод", sup="Поставщик Б", dl=д("2026-09-30")),
              заказ(id="9", deal="101", seq=901, title="901/1. Узел", dl=д("2026-10-01"))]
    сделки = [{"deal": "100", "seq": 900, "customer": "Заказчик 1", "closed": False},
              {"deal": "101", "seq": 901, "customer": "Заказчик 2", "closed": False}]
    q = [x for x in lc.build(заказы, сделки, СЕГОДНЯ)["queue"] if x["code"] == "late"]
    assert [x["ordLbl"] for x in q] == ["900/1", "900/2", "900/3", "901/1"]
    assert [x["sup"] for x in q[:3]] == ["Поставщик А", "Поставщик Б", "Поставщик В"]
    assert [x["ref"] for x in q[:3]] == ["срок 15.10.2025", "срок 30.09.2026", "срок 09.10.2026"]
    assert [x["days"] for x in q[:3]] == [369, 19, 10] and q[0]["ordNo"] == 1
    # заказ без номера в названии — «id карточки»
    q = lc.build([заказ(id="77", title="Насос без номера", dl=д("2026-10-01"))], сделки[:1], СЕГОДНЯ)["queue"]
    assert next(x for x in q if x["code"] == "late")["ordLbl"] == "id 77"


def test_приёмка_по_назначению_шаг_4_с_даты_назначения():
    """«Заказ создан», назначение в день создания (14.10): s1Over нет, s2Over — с 14.10
    («назначение»), хозяин менеджер ОРК; назначение раньше начала приёмки — не в счёт: s1Over с
    14.10 («создан заказ»); без назначения — как раньше."""
    d = [{"deal": "100", "seq": 900, "closed": False, "kam": "Анна (КАМ)"}]
    o = заказ(dl=д("2027-01-01"), stage="Заказ создан", t0="2026-10-14T11:00:00+03:00", created=д("2026-10-14"),
              stepSince="2026-10-14T11:00:00+03:00", orkMgr="Илья (ОРК)")
    q = {x["code"]: x for x in lc.build([{**o, "assignDate": д("2026-10-14")}], d, СЕГОДНЯ)["queue"]}
    assert "s1Over" not in q and q["s2Over"]["days"] == 3 and q["s2Over"]["step"] == 4
    assert (q["s2Over"]["role"], q["s2Over"]["who"]) == ("orkMgr", "Илья (ОРК)")
    assert q["s2Over"]["ref"] == "с 14.10 (назначение)" and q["s2Over"]["since"] == "2026-10-14"
    for x in ({**o, "assignDate": д("2026-10-13")}, o):
        q = {y["code"]: y for y in lc.build([x], d, СЕГОДНЯ)["queue"]}
        assert "s2Over" not in q and q["s1Over"]["ref"] == "с 14.10 (создан заказ)" and q["s1Over"]["days"] == 3
    # начало приёмки — вход сделки в реализацию (позже создания заказа)
    вход = {**o, "t0": "2026-10-15T09:00:00+03:00"}
    q = {y["code"]: y for y in lc.build([вход], d, СЕГОДНЯ)["queue"]}
    assert q["s1Over"]["ref"] == "с 15.10 (вход в реализацию)" and q["s1Over"]["days"] == 2
    # год не текущий — с годом
    assert lc.since_text("2025-12-30", "вход в шаг", {}, СЕГОДНЯ) == "с 30.12.2025 (вход в шаг)"
