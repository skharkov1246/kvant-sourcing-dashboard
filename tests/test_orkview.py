"""Раздел «ork» вкладки «Реализация» (orkview.build): неделя, месяц, квартал и «как
считается» руководителя ОРК по регламенту (решения владельца 26.10.2026).

Два слоя: синтетика smoke-сборки (fixture.ContractsStub через настоящий contracts.compute)
— каждый блок с данными и сходится со счётом вкладки; ручные входы — пути контракта
показателя (show, prelim, nodata, hidden). Корпус придуман (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import contracts as contracts_mod  # noqa: E402
import ork  # noqa: E402
import orkview  # noqa: E402
from tests import fixture  # noqa: E402

СЕГОДНЯ = fixture.CT_TODAY


@pytest.fixture(scope="module")
def ct():
    return fixture.build_contracts()


def _orders(stub=None, **kw) -> tuple[dict, list[dict]]:
    """contracts.compute и заказы, которые он отдал orkview (life_orders основного вида)."""
    got: dict[str, list] = {}
    orig = orkview.build

    def spy(orders, *a, **k):
        got.setdefault("o", orders)
        return orig(orders, *a, **k)
    old = os.environ.get("KVANT_NOW")
    os.environ["KVANT_NOW"] = fixture.CT_NOW
    orkview.build = spy
    try:
        stub = stub or fixture.ContractsStub()
        data = contracts_mod.compute(stub, as_of=СЕГОДНЯ, people=fixture.CT_ROSTER,
                                     departments=stub.departments(), **kw)
    finally:
        orkview.build = orig
        if old is None:
            os.environ.pop("KVANT_NOW", None)
        else:
            os.environ["KVANT_NOW"] = old
    return data, got["o"]


# ── сборка целиком ───────────────────────────────────────────────────────────────────────
def test_сейчас_на_шаге_сходится_с_живыми_заказами_вида(ct):
    """Σ «сейчас на шаге» по 13 шагам + «стадия не из таблицы» = живые заказы вида (та же
    вселенная, что «живых заказов» вкладки) — и в основном виде, и в виде направления."""
    for view in (ct, ct["sep"]):
        w = view["ork"]["week"]
        assert sum(r["now"] for r in w["ledger"]) + w["unknown"] == w["live"]
        # ведомость — живые заказы открытых сделок и шаг 12 закрытых (closed12, Рег. 14.2.4)
        assert w["live"] - w["closed12"] == view["deadlines"]["liveOrders"]
    assert ct["ork"]["trust"]["mapped"] + ct["ork"]["trust"]["unknown"] == ct["ork"]["week"]["live"]


def test_неделя_все_блоки_с_данными(ct):
    w = ct["ork"]["week"]
    t = w["tiles"]
    for k in ("W1", "W2", "W3", "W5"):
        assert t[k]["state"] in ("show", "prelim") and t[k]["value"] > 0, (k, t[k])
    assert t["W4"]["state"] == "nodata" and t["W4"]["value"] is None and "Эскалация" in t["W4"]["need"]
    # W2 — только шаги ОРК (1, 4, 5, 8); УПД (шаг 12) — «ждём УПД от клиента», не в W2
    # (решение владельца 27.10.2026, Б); счёт — тот же, что пункты очереди
    c = ct["life"]["counts"]
    assert t["W2"]["value"] == sum(len({q["order"] for q in ct["life"]["queue"] if q["code"] == k and q.get("w2")})
                                   for k in ("s1Over", "s2Over", "s3Over", "s6Over"))
    assert set(t["W2"]["byStep"]) == {1, 4, 5, 8} and t["W2"]["label"].endswith(ork.NO_PAUSE)
    assert "noUtd" not in t["W2"]["src"] and t["W2"]["waitUtd"] == w["waitUtd"]["n"] > 0
    assert t["W1"]["noOrder"] == c.get("noOrder", 0) >= 1
    assert t["W3"]["by"]["late"] == c["late"] and t["W3"]["label"] == "результат поставщика и логистики"
    led = {r["no"]: r for r in w["ledger"]}
    assert len(led) == 13 and led[5]["roleName"] == "менеджер ОРК" and led[1]["roleName"] == "руководитель ОРК"
    assert led[7]["over"] is None and led[7]["waits"] == "sup", "производство не нормируется — ждём поставщика"
    assert led[5]["artReq"] >= led[5]["artMiss"] > 0
    assert w["adjacent"]["log"] > 0 and w["overByWho"] and w["risk"]
    assert any(p["kind"] == "P50" for p in w["checkpoints"]) and any(p["kind"] == "P14" for p in w["checkpoints"])
    mon, sun = dt.date.fromisoformat(w["mon"]), dt.date.fromisoformat(w["sun"])
    assert all(mon <= dt.date.fromisoformat(p["date"]) <= sun for p in w["checkpoints"])
    assert {"behind", "abandoned", "unknown", "noMgr"} <= set(w["hygiene"]["by"])
    assert w["unknownStages"] == {"архив (старая стадия)": 1}


def test_месяц_квартал_и_как_считается(ct):
    o = ct["ork"]
    mo = ct["orkMonth"]["main"]["month"]           # «Месяц» — под правом, не в разделе для всех
    rows = mo["rows"]
    assert len(rows) == ork.MONTHS_BACK
    # в корпусе за отрезками S2/S3/S6 и артефактами — один ответственный: показатель отдела
    # раскрыл бы человека (решение п. 3) — не показываем, ни значения, ни k
    assert any(r["M1"]["mgr"]["state"] == "hidden" and "раскрыл бы человека" in r["M1"]["mgr"]["why"] for r in rows)
    assert all(r["M1"][k]["state"] != "show" for r in rows for k in r["M1"])
    assert all("k" not in r["M1"][c] for r in rows for c in ("S2", "S3", "S6", "mgr")
               if r["M1"][c]["state"] == "hidden")
    assert any(r["M2"]["state"] == "hidden" and "have" not in r["M2"] for r in rows)
    assert any(r["M1"]["S1"]["state"] == "prelim" for r in rows), "S1 — руководитель ОРК, без ворот людей"
    assert all(r["otd"]["label"] == "результат поставщика и логистики" for r in rows)
    M3 = mo["M3"]
    assert M3["label"] == "без поправки на сложность"
    # менеджеров ОРК в корпусе меньше трёх: среднее — нагрузка человека, без права не показываем
    assert M3["managers"] < ork.GATES["n_managers_min"] and M3["state"] == "hidden" and M3["value"] is None
    assert len(mo["an4"]) == len(ork.AN4_NODATA) and {x["state"] for x in mo["an4"]} == {"nodata"}
    q = o["quarter"]
    assert any(x["Q1"]["state"] in ("show", "prelim") for x in q["quarters"])
    assert q["Q2"]["S5"]["state"] in ("show", "prelim") and q["Q2"]["S5"]["p80"] >= q["Q2"]["S5"]["med"]
    assert q["Q2cut"]["category"]["state"] == "nodata" and q["Q2cut"]["direction"]["variants"] == 2
    assert q["Q3"]["state"] in ("show", "prelim") and q["Q3"]["meanP"] is not None
    assert [s["state"] for s in q["scorecard"]].count("nodata") == 5
    assert q["index"]["state"] == "hidden" and q["rating"]["state"] == "hidden"
    h = o["how"]
    assert {m["code"] for m in h["metrics"]} == {"W1", "W2", "W3", "W4", "W5", "M1", "M2", "M3", "Q1", "Q2", "Q3"}
    # у M1–M3 в разделе для всех — формула и поля, вердикт и n — под правом
    assert all(m["state"] == "right" and m["n"] is None and m["cover"] is None
               for m in h["metrics"] if m["code"] in ("M1", "M2", "M3"))
    assert [m["code"] for m in ct["orkMonth"]["main"]["how"]] == ["M1", "M2", "M3"]
    assert all(m["state"] != "right" for m in ct["orkMonth"]["main"]["how"])
    assert len(h["fieldsAdd"]) == len(ork.FIELDS_ADD) and {f["to"] for f in h["fieldsAdd"]} == {ork.FIELDS_TO}
    assert h["gatesNote"] == ork.GATES_NOTE and h["violations"]["payNoContract"] >= 1
    assert h["violations"]["returns"] >= 1 and h["violations"]["bursts"] >= 1
    assert {a["key"] for a in h["artifacts"]} == {a["key"] for a in ork.ARTIFACTS}
    assert h["kamAgree"]["n"] > 0 and h["kamAgree"]["same"] == h["kamAgree"]["n"]
    assert o["trust"]["yearsNoCal"] == [] and o["trust"]["ownerRule"] == "step" and o["trust"]["orkHead"]


def test_показатели_менеджеров_отдельно_от_вкладки(ct):
    """Решение владельца 26.10.2026, п. 3: «подвкладка «Месяц» — только руководителям и
    владельцу». Весь «Месяц» — показатели отдела (S1 руководителя ОРК — один человек), M3 и
    поимённо — отдельным массивом (orkMonth), в разделе для всех с вкладкой его нет; рейтинга нет."""
    for key, view in (("main", ct["ork"]), ("sep", (ct.get("sep") or {}).get("ork"))):
        if view is None:
            continue
        assert "month" not in view, key
        mo = ct["orkMonth"][key]
        assert set(mo) == {"month", "people", "how"} and mo["month"]["rows"], key
    P = ct["orkMonth"]["main"]["people"]
    assert P and all("who" in m for m in P)
    assert all(set(m) >= {"who", "live", "M1", "M2"} and "score" not in m for m in P)
    публичный = json.dumps({k: v for k, v in ct.items() if k != "orkMonth"}, ensure_ascii=False, default=str)
    for слово in ('"loadDev"', '"an4"', '"outBand"', '"S2+S3+S6"'):
        assert слово not in публичный, слово


def test_страница_показатели_менеджеров_своим_массивом_под_правом():
    """dashboard.render: поимённые показатели «Месяца» — строкой window.__ORKMONTH__ (воркер
    вырезает её без права «ork_heads»), в window.__CONTRACTS__ их нет; без данных — null."""
    import dashboard
    r = fixture.build_realization()
    assert r["ork_month"]["main"]["people"] and "orkMonth" not in r["contracts"]
    html = dashboard.render({}, {}, contracts=r["contracts"], dealctx=r["dealctx"], ork_month=r["ork_month"])
    lines = {ln.split(" = ", 1)[0]: ln for ln in html.splitlines() if ln.startswith("window.__")}
    om = json.loads(lines["window.__ORKMONTH__"].split(" = ", 1)[1].rstrip(";"))
    assert om == json.loads(json.dumps(r["ork_month"], ensure_ascii=False, default=str))
    ct_line = lines["window.__CONTRACTS__"]
    assert '"orkMonth"' not in ct_line and '"loadDev"' not in ct_line and '"an4"' not in ct_line
    пусто = dashboard.render({}, {}, contracts=r["contracts"])
    assert "window.__ORKMONTH__ = null;" in пусто


def test_сырые_переходы_в_страницу_не_идут(ct):
    """В страницу — только агрегаты и шаг живых заказов; переходы и сегменты остаются в расчёте."""
    s = json.dumps({k: v for k, v in ct.items() if k != "orkMonth"}, ensure_ascii=False, default=str)
    for слово in ('"_segs"', '"_reach"', '"transitions"', '"returnPairs"', '"stepSince"'):
        assert слово not in s, слово
    o = next(o for r in ct["rows"] for o in r["orders"] if o["live"] and o["step"])
    assert {"step", "stepRole", "stepWho", "stepWd", "stepNorm", "stepOver"} <= set(o)
    cells = [r["stepCell"] for r in ct["rows"] if r.get("stepCell")]
    assert cells and all(c["label"] in (ork.NO_PAUSE, "закрыть сделку") for c in cells)
    assert len(json.dumps(ct["ork"], ensure_ascii=False)) < 120_000


def test_сумма_прячет_потерю(ct):
    """У скольких сделок картина «Замедлено» стала хуже против прежних 2× медианы — счётом."""
    c = ct["ork"]["slowCmp"]
    assert c["new"] == c["old"] - c["better"] + c["worse"]
    assert all(isinstance(v, int) and v >= 0 for v in c.values())


def test_нет_упд_по_сырой_дате_а_не_подставленной():
    """Доставки нет, УПД подписан (дата УПД подставлена в доставку): S9 не считается «УПД в
    день доставки», пункта noUtd нет. Доставлено 3 недели назад, УПД нет — noUtd в р.д."""
    stub = fixture.ContractsStub()
    a = next(o for o in stub.orders if contracts_mod.DEL_FACT in o and contracts_mod.UTD_DATE not in o
             and o["stageId"].endswith(":WAITPAY"))
    b = next(o for o in stub.orders if contracts_mod.DEL_FACT in o and contracts_mod.UTD_DATE in o)
    a[contracts_mod.DEL_FACT] = (СЕГОДНЯ - dt.timedelta(days=21)).isoformat()
    b.pop(contracts_mod.DEL_FACT)
    data, orders = _orders(stub)
    lo = {o["id"]: o for o in orders}
    sb = lo[str(b["id"])]
    assert sb["utdRaw"] and sb["delFact"] is None and sb["utdDate"] == sb["utdRaw"]
    assert "S9" not in sb["_segs"]["segs"], "S9 — от сырой доставки; её нет — сегмента нет"
    q = [x for x in data["life"]["queue"] if x["order"] == str(a["id"]) and x["code"] == "noUtd"]
    assert q and q[0]["days"] == 15 and q[0]["inWd"], "21 кал. день — 15 р.д."
    assert not [x for x in data["life"]["queue"] if x["order"] == str(b["id"]) and x["code"] == "noUtd"]


# ── контракт показателя на ручных входах ─────────────────────────────────────────────────
def _seg(wd, *, burst=False, end="2026-10-10T10:00:00+03:00", norm=2):
    return {"wd": wd, "inNorm": wd is not None and 0 <= wd <= norm, "burst": burst, "ret": False, "neg": False,
            "end": end}


def test_пути_состояния_показателя():
    assert orkview._timeliness([], "S2")["state"] == "nodata"
    show = orkview._timeliness([_seg(1)] * 40, "S2")
    assert (show["state"], show["value"], show["n"]) == ("show", 100, 40)
    pre = orkview._timeliness([_seg(1)] * 3 + [_seg(5)], "S2")
    assert pre["state"] == "prelim" and pre["value"] == 75 and "n = 4" in pre["why"]
    # «пачек» больше 10 % — не показываем, значение не отдаётся
    hid = orkview._timeliness([_seg(1)] * 30 + [_seg(1, burst=True)] * 10, "S2")
    assert hid["state"] == "hidden" and hid["value"] is None and "пачек" in hid["why"]
    # год без календаря: сегмент не посчитан — покрытие падает, нуля нет
    cov = orkview._timeliness([_seg(None)] * 21 + [_seg(1)] * 39, "S2")
    assert cov["state"] == "prelim" and "покрытие 65 %" in cov["why"] and cov["n"] == 39
    assert orkview._timeliness([_seg(None)] * 31 + [_seg(1)] * 29, "S2")["state"] == "hidden", "покрытие < 50 %"
    nocal = orkview._timeliness([_seg(None)] * 10, "S2")
    assert nocal["state"] == "nodata" and nocal["value"] is None
    # счёт «сейчас»: на шагах никого — известный ноль, не «нет данных»
    z = orkview._count(0, 0, None, src="x")
    assert (z["state"], z["value"]) == ("show", 0) and z["why"] == "сейчас на шагах никого"
    # на шаге есть заказы, но все не в счёте — «в счёте никого; не в счёте 1», не «никого»
    ze = orkview._count(0, 0, None, src="x", extra={"excluded": 1})
    assert ze["value"] == 0 and "не в счёте 1" in ze["why"]
    assert orkview._count(3, 10, 0.4, src="x")["state"] == "hidden"
    assert orkview._probe_cap("M1", dict(show))["state"] == "prelim"


def test_первичный_срок_поставщика_план_равен_факту_не_показываем():
    """Q1: «план = факт» > 30 % — плановую дату переписали под факт, первичного срока нет."""
    def o(i, ready, pe):
        return {"id": str(i), "sup": "Поставщик А", "prodEnd": pe, "_reach": {8: f"{ready}T10:00:00+03:00"},
                "_segs": {"segs": {}}, "created": dt.date(2026, 7, 1)}
    same = [o(i, "2026-10-05", dt.date(2026, 10, 5)) for i in range(4)]
    late = [o(10 + i, "2026-10-12", dt.date(2026, 10, 5)) for i in range(6)]
    q = orkview._quarter_view(same + late, СЕГОДНЯ)["quarters"][0]["Q1"]
    assert q["state"] == "hidden" and q["value"] is None and q["planEqFact"] == 0.4
    q2 = orkview._quarter_view(late, СЕГОДНЯ)["quarters"][0]["Q1"]
    # отдел целиком — общий показатель: n меньше n_min — «предварительно»; строка поставщика —
    # ворота n_supplier_quarter (6 ≥ 5 — показываем)
    assert (q2["state"], q2["value"], q2["n"]) == ("prelim", 0, 6) and "n = 6 < 30" in q2["why"]
    sup = orkview._quarter_view(late, СЕГОДНЯ)["quarters"][0]["suppliers"][0]
    assert (sup["state"], sup["n"]) == ("show", 6)


# ── одна совокупность у «сейчас», «сверх нормы» и W2 ──────────────────────────────────────
class _ОплаченоБезУпд(fixture.ContractsStub):
    """Придуманный корпус: сделка «Оплата получена» (закрыта) с тремя живыми заказами,
    доставленными 45 дн. назад без УПД, и два живых заказа открытой сделки, доставленных без
    УПД, — в стадиях «Заказ отгружен» и «Заказ получен клиентом»."""

    def __init__(self):
        super().__init__()
        import contracts as ct_
        now = dt.datetime.fromisoformat(fixture.CT_NOW)
        t = lambda a: (now - dt.timedelta(days=a)).isoformat()            # noqa: E731
        d = lambda a: (fixture.CT_TODAY - dt.timedelta(days=a)).isoformat()  # noqa: E731
        self.deals.append({"ID": "6900", "TITLE": "990. Оплачено, заказ не закрыт", "OPPORTUNITY": "1000",
                           "CURRENCY_ID": "EUR", "COMPANY_ID": "301", "STAGE_ID": "C0:PAID", "STAGE_SEMANTIC_ID": "P",
                           "CATEGORY_ID": "0", "DATE_CREATE": t(200), "ASSIGNED_BY_ID": "10", ct_.OSS_DEAL: ["21"],
                           ct_.KAM_F: "11"})
        self.deals.append({"ID": "6901", "TITLE": "991. Доставлено без УПД", "OPPORTUNITY": "1000",
                           "CURRENCY_ID": "EUR", "COMPANY_ID": "301", "STAGE_ID": "C0:DELIVERED",
                           "STAGE_SEMANTIC_ID": "P", "CATEGORY_ID": "0", "DATE_CREATE": t(200), "ASSIGNED_BY_ID": "10",
                           ct_.OSS_DEAL: ["21"], ct_.KAM_F: "11"})
        self.hist[2] += [("6900", "C0:NEW", t(200)), ("6900", "C0:PAID", t(10)),
                         ("6901", "C0:NEW", t(200)), ("6901", "C0:DELIVERED", t(30))]
        plan = [("6900", 99000, "WAITPAY"), ("6900", 99001, "WAITPAY"), ("6900", 99002, "WAITPAY"),
                ("6901", 99100, "TRUCK"), ("6901", 99101, "GOT")]
        for deal, oid, stage in plan:
            self.orders.append({"id": oid, "title": f"{deal}/{oid}. Узел", "companyId": "401",
                                "parentId2": int(deal), "opportunity": 100, "currencyId": "EUR",
                                "stageId": f"DT172_26:{stage}", "createdTime": t(150), "movedTime": t(40),
                                ct_.OSS_F: "21", ct_.DL_CUSTOMER: d(30), ct_.DEL_FACT: d(45), ct_.SUP_PAID: 100,
                                "assignedById": "31"})
            path = ("NEW", "SENT", "PO", "PAY", "PREPARATION", "READY", "SHIPPREP", "TRUCK", "GOT", "WAITPAY")
            for k, st in enumerate(path[:path.index(stage) + 1]):
                self.hist[172].append((str(oid), f"DT172_26:{st}", t(150 - 12 * k)))
        # все заказы закрыты, сделка открыта (dealOpen) — шаг 13 ОСС «закрыть сделку»
        self.deals.append({"ID": "6902", "TITLE": "992. Заказы закрыты", "OPPORTUNITY": "1000",
                           "CURRENCY_ID": "EUR", "COMPANY_ID": "301", "STAGE_ID": "C0:DELIVERED",
                           "STAGE_SEMANTIC_ID": "P", "CATEGORY_ID": "0", "DATE_CREATE": t(200), "ASSIGNED_BY_ID": "10",
                           ct_.OSS_DEAL: ["21"], ct_.KAM_F: "11"})
        self.hist[2] += [("6902", "C0:NEW", t(200)), ("6902", "C0:DELIVERED", t(20))]
        self.orders.append({"id": 99300, "title": "6902/1. Узел", "companyId": "401", "parentId2": 6902,
                            "opportunity": 100, "currencyId": "EUR", "stageId": "DT172_26:SUCCESS",
                            "createdTime": t(150), "movedTime": t(15), ct_.OSS_F: "21", ct_.DL_CUSTOMER: d(30),
                            ct_.DEL_FACT: d(45), ct_.UTD_DATE: d(40), ct_.SUP_PAID: 100, "assignedById": "31"})
        self.hist[172] += [("99300", "DT172_26:NEW", t(150)), ("99300", "DT172_26:SUCCESS", t(15))]


def _инварианты(view: dict, rows: list | None = None, sep: bool = False) -> None:
    """rows — все сделки (они только у основного вида: data["rows"]); сверяются строки ЭТОГО вида:
    у основного — без сегмента, у направления (sep=True) — с сегментом."""
    w = view["ork"]["week"]
    for r in w["ledger"]:
        if r["over"] is not None:
            assert r["over"] <= r["now"], (r["no"], r["over"], r["now"])
    W2 = w["tiles"]["W2"]
    assert W2["value"] <= (W2["n"] or 0), W2
    assert sum(r["now"] for r in w["ledger"]) + w["unknown"] == w["live"]
    assert sum(m["n"] for m in w["overByWho"]) == W2["value"], "поимённо — тот же счёт, что у W2"
    # W2 = Σ «сверх нормы» по менеджерам = пункты очереди с w2 (решение 27.10.2026, Б: шаг 12 — нет)
    q = view["life"]["queue"]
    assert W2["value"] == len({x["order"] for x in q if x.get("w2")}) == sum(1 for x in q if x.get("w2"))
    assert 12 not in W2["byStep"] and not [x for x in q if x["code"] == "noUtd" and x.get("w2")]
    # «ждём УПД от клиента» — одно число у ведомости (шаг 12), столбца по менеджерам, плитки,
    # очереди (noUtd с waitUtd) и заказов таблицы сделок (stepWaitUtd)
    led = {r["no"]: r for r in w["ledger"]}
    n = w["waitUtd"]["n"]
    assert led[12]["waitUtd"] == n == W2["waitUtd"] == sum(m["waitUtd"] for m in w["overByWho"])
    assert n == sum(1 for x in q if x.get("waitUtd")) == sum(1 for x in q if x["code"] == "noUtd")
    assert led[12]["over"] == 0 and all(r["waitUtd"] is None for r in w["ledger"] if r["no"] != 12)
    assert not [x for x in q if x.get("w2") and x.get("waitUtd")]
    rows = view.get("rows") if rows is None else rows
    if rows is not None:
        rows = [r for r in rows if bool(r["seg"]) == sep]
        live = [o for r in rows if not r["lost"] for o in r["orders"] if o["live"]]
        assert n == sum(1 for o in live if o["stepWaitUtd"])
        assert not [o for o in live if o["stepWaitUtd"] and (o["stepOver"] or o["slow"] or o["step"] != 12)]
        for r in rows:
            c = r.get("stepCell") or {}
            assert not (c.get("waitUtd") and c.get("over")), r["deal"]
            if c.get("waitUtd"):
                assert c["step"] == 12 and c["role"] == "orkMgr", r["deal"]
            # «ждём УПД» — не «Замедлено»: сверх нормы шага в «почему» — только заказы с stepOver
            k = sum(1 for o in r["orders"] if o["live"] and o["stepOver"])
            assert (f"сверх нормы шага: {k} заказ" in (r["why"] or "")) == bool(k), (r["deal"], r["why"])


def test_сверх_нормы_не_больше_сейчас(ct):
    _инварианты(ct)
    _инварианты(ct["sep"], ct["rows"], sep=True)
    stub = _ОплаченоБезУпд()
    data, _o = _orders(stub)
    _инварианты(data)
    q = data["life"]["queue"]
    paid = [x for x in q if x["code"] == "noUtd" and str(x["order"]).startswith("9900")]
    # шаг 12 — на менеджере ОРК при любой стадии оплаты сделки: заказ сделки «Оплата получена»,
    # доставленный без УПД, — пункт noUtd в очереди и «ждём УПД от клиента» в ведомости, а не
    # «сверх нормы» и не W2 (решение владельца 27.10.2026, Б)
    assert len(paid) == 3 and all(x.get("waitUtd") and not x.get("w2") for x in paid), "закрытая сделка: ждём УПД"
    w = data["ork"]["week"]
    W2 = w["tiles"]["W2"]
    assert 12 not in W2["byStep"]
    led = {r["no"]: r for r in w["ledger"]}
    # три заказа сделки 6900 и один — сделки 6030 корпуса (оплата получена, УПД нет)
    assert led[12]["waitUtd"] == sum(1 for x in q if x["code"] == "noUtd") >= 4 and led[12]["over"] == 0
    assert w["closed12"] == 4
    assert w["live"] - w["closed12"] == data["deadlines"]["liveOrders"]
    # ячейка «Шаг» сделки «Оплата получена» с заказом без УПД дольше 10 р.д. — шаг 12 менеджера
    # ОРК «ждём УПД от клиента», как в карточке и ведомости (а не шаг 13 без отметки и не ⚠);
    # закрыть сделку — второй строкой (пункт dealClose ОСС остаётся)
    r = next(x for x in data["rows"] if x["deal"] == "6900")
    c = r["stepCell"]
    oo = [o for o in r["orders"] if o["live"] and o["stepWaitUtd"]]
    assert oo and all((o["step"], o["stepRole"], o["stepOver"]) == (12, "orkMgr", False) for o in oo)
    assert (c["step"], c["role"], c["over"], c["waitUtd"], c["close"]) == (12, "orkMgr", False, True, "paid")
    assert (c["wd"], c["norm"], c["who"]) == (oo[0]["stepWd"], oo[0]["stepNorm"], oo[0]["stepWho"])
    assert any(x["code"] == "dealClose" and x["deal"] == "6900" for x in q)
    # оговорка Рег. 15.6 (14.6) — одна у ячейки и пункта dealClose: три заказа доставлены без УПД
    dc = next(x for x in q if x["code"] == "dealClose" and x["deal"] == "6900")
    assert c["closeNote"] == "доставлено без УПД: 3 — закрыть после исполнения (Рег. 15.6, 14.6)"
    assert dc["what"].endswith("; " + c["closeNote"])
    r = next(x for x in data["rows"] if x["deal"] == "6902")
    assert r["stepCell"]["closeNote"] == ""
    assert (r["stepCell"]["step"], r["stepCell"]["role"], r["stepCell"]["close"]) == (13, "oss", "open")
    assert any(x["code"] == "dealOpen" and x["deal"] == "6902" for x in q)
    # доставлено без УПД — шаг 12 ведомости, а не 11 («Заказ получен клиентом»)
    assert led[11]["now"] == sum(1 for r in data["rows"] if not r["closed"] for o in r["orders"]
                                 if o["live"] and o["step"] == 11 and not o["delivered"])
    assert any(x.get("waitUtd") for x in q if str(x["order"]) in ("99100", "99101"))


def test_ждём_упд_направления_своим_видом():
    """Решение владельца 27.10.2026, Б, у отдельного направления: заказ шага 12 сделки
    реализации компании направления, 28 дн. без УПД, — «ждём УПД» вида направления, не
    основного; строки таблицы сделок сверяются с видом по сегменту (rows — только у основного)."""
    data = fixture.build_contracts(stub=fixture.Шаг12Направления(wait=True))
    sep = data["sep"]
    _инварианты(data)
    _инварианты(sep, data["rows"], sep=True)
    w = sep["ork"]["week"]
    led = {r["no"]: r for r in w["ledger"]}
    assert w["waitUtd"] == {**w["waitUtd"], "n": 1, "unknown": 0} and led[12]["waitUtd"] == 1
    q = [x for x in sep["life"]["queue"] if x["order"] == "97808"]
    assert [(x["role"], x.get("waitUtd"), x.get("w2")) for x in q if x["code"] == "noUtd"] == [("orkMgr", True, None)]
    assert not [x for x in q if x.get("w2")]
    assert not [x for x in data["life"]["queue"] if x["order"] == "97808"]
    r = next(r for r in data["rows"] if r["deal"] == "7808")
    assert r["seg"] and r["stepCell"]["waitUtd"] and not r["stepCell"]["over"] and not r["slow"]


def test_срок_упд_неизвестен_не_ноль_ждём():
    """Единственный заказ шага 12 вида — «Ожидание оплаты» без факта доставки: «ждём УПД» — 0
    известных, срок неизвестен 1 (week.waitUtd.unknown), в год без календаря шаг 12 не
    считается (trust.noCalendar — только шаги W2)."""
    sep = fixture.build_contracts(stub=fixture.Шаг12Направления(wait=False))["sep"]
    w = sep["ork"]["week"]
    r12 = {r["no"]: r for r in w["ledger"]}[12]
    assert (r12["now"], r12["over"], r12["waitUtd"], r12["overUnknown"], r12["notCounted"]) == (1, 0, 0, 1, 0)
    assert w["waitUtd"]["n"] == 0 and w["waitUtd"]["unknown"] == 1
    _инварианты(sep)


def test_гигиена_не_назначен_в_счёте(ct):
    """«Менеджер ОРК не назначен» — гигиена, но в счёте «сверх нормы» есть (решение п. 1):
    в «не идут в счёт» — только стадия позади фактов, брошенная и стадия не из таблицы."""
    h = ct["ork"]["week"]["hygiene"]
    assert h["excluded"] == sum(1 for x in h["items"] if x["excl"])
    assert all(set(x["kinds"]) & {"behind", "abandoned", "old", "unknown"} for x in h["items"] if x["excl"])
    assert h["noMgrOnly"] > 0 and all(not x["excl"] for x in h["items"] if x["kinds"] == ["noMgr"])
    nm = next((m for m in ct["ork"]["week"]["overByWho"] if m["who"] == "не назначен"), None)
    assert nm and nm["n"] > 0, "не назначенные — в «сверх нормы по менеджерам»"
    assert all("live" not in m for m in ct["ork"]["week"]["overByWho"]), "нагрузка поимённо — под правом"


def test_контрольные_точки_у_заказов_шагов_6_7(ct):
    w = ct["ork"]["week"]
    W5 = w["tiles"]["W5"]
    assert W5["value"] == len(w["checkpoints"]) and (W5["n"] or 0) > 0
    rows = {o["id"]: o for r in ct["rows"] for o in r["orders"]}
    assert all(rows[str(c["order"])]["step"] in (6, 7) for c in w["checkpoints"])


def test_q1_пачка_не_первичный_срок():
    """Придуманный корпус: 10 заказов поставщика «П-1», вход в «Заказ готов» прокликан пачкой
    через 40 дн. после бюджетного конца производства — не «0 % в срок», а «не показываем»."""
    names = {"NEW": "Заказ создан", "SENT": "Заказ направлен", "PO": "Сформирован РО/Подготовка описаний",
             "PAY": "Оплата поставщику / агенту", "PROD": "Производство заказа", "READY": "Заказ готов",
             "SHIP": "Подготовка к отгрузке", "TRUCK": "Подготовка груза/Поиск транспорта",
             "GOT": "Заказ получен клиентом", "CLOSE": "Закрытие заказа"}
    msk = dt.timezone(dt.timedelta(hours=3))
    orders = []
    for k in range(10):
        tr = [("NEW", "2026-04-01T10:00:00+03:00"), ("SENT", "2026-04-02T10:00:00+03:00"),
              ("PO", "2026-04-06T10:00:00+03:00"), ("PAY", "2026-04-10T10:00:00+03:00"),
              ("PROD", "2026-04-13T10:00:00+03:00")]
        base = dt.datetime(2026, 8, 19, 10, 0, tzinfo=msk)
        tr += [(st, (base + dt.timedelta(seconds=20 * i)).isoformat())
               for i, st in enumerate(("READY", "SHIP", "TRUCK", "GOT", "CLOSE"))]
        orders.append({"id": str(k), "sup": "П-1", "prodEnd": dt.date(2026, 7, 10), "created": dt.date(2026, 4, 1),
                       "_reach": ork.reach_times(tr, names), "_segs": ork.segments(tr, names)})
    q = orkview._quarter_view(orders, dt.date(2026, 10, 26))
    q3 = next(x for x in q["quarters"] if x["q"] == "3 кв. 2026")
    assert q3["Q1"]["state"] == "hidden" and q3["Q1"]["value"] is None and q3["Q1"]["burstN"] == 10
    assert [(r["sup"], r["state"], r["value"]) for r in q3["suppliers"]] == [("П-1", "hidden", None)]
    assert q["Q2"]["S5"]["state"] == "hidden", "Q2: «пачек» больше порога — не показываем"
    # та же готовность без пачки — в счёте
    for o in orders:
        o["_segs"]["segs"]["S5"]["burstEnd"] = False
    q3 = next(x for x in orkview._quarter_view(orders, dt.date(2026, 10, 26))["quarters"] if x["q"] == "3 кв. 2026")
    assert q3["Q1"]["n"] == 10 and q3["Q1"]["value"] == 0


def test_менеджер_за_последний_полный_месяц():
    """Поимённо — главное последний ПОЛНЫЙ месяц; текущий (идёт, неполный) — рядом, cur."""
    _d, _o = _orders()
    mp = _d["orkMonth"]["main"]["people"]
    assert mp and all(m["m"] == "09.2026" and "M1" in m and m["cur"]["m"] == "10.2026" and m["cur"]["partial"]
                      for m in mp)
    assert all("prev" not in m for m in mp)


# ── ревизия 2: одно правило шага 12, гигиена старой доставки, неизвестные стадии ─────────
class _Шаг12(fixture.ContractsStub):
    """Придуманный корпус: три открытые сделки по одному живому заказу без УПД — «Заказ получен
    клиентом» и «Ожидание оплаты», доставлены 20 р.д. назад (21.09.2026), и «Ожидание оплаты»,
    доставлен 400 дн. назад."""

    def __init__(self):
        super().__init__()
        import contracts as ct_
        now = dt.datetime.fromisoformat(fixture.CT_NOW)
        t = lambda a: (now - dt.timedelta(days=a)).isoformat()            # noqa: E731
        d = lambda a: (fixture.CT_TODAY - dt.timedelta(days=a)).isoformat()  # noqa: E731
        for deal, oid, stage, ago in (("6910", 99200, "GOT", 28), ("6911", 99201, "WAITPAY", 28),
                                      ("6912", 99202, "WAITPAY", 400)):
            self.deals.append({"ID": deal, "TITLE": f"99{deal[-1]}. Доставлено без УПД", "OPPORTUNITY": "1000",
                               "CURRENCY_ID": "EUR", "COMPANY_ID": "301", "STAGE_ID": "C0:DELIVERED",
                               "STAGE_SEMANTIC_ID": "P", "CATEGORY_ID": "0", "DATE_CREATE": t(500),
                               "ASSIGNED_BY_ID": "10", ct_.OSS_DEAL: ["21"], ct_.KAM_F: "11"})
            self.hist[2] += [(deal, "C0:NEW", t(500)), (deal, "C0:DELIVERED", t(ago))]
            self.orders.append({"id": oid, "title": f"{deal}/1. Узел", "companyId": "401", "parentId2": int(deal),
                                "opportunity": 100, "currencyId": "EUR", "stageId": f"DT172_26:{stage}",
                                "createdTime": t(480), "movedTime": t(ago - 1), ct_.OSS_F: "21",
                                ct_.DL_CUSTOMER: d(ago - 5), ct_.DEL_FACT: d(ago), ct_.SUP_PAID: 100,
                                ct_.SHIP_FACT: d(ago + 20), ct_.PROD_START: d(470), ct_.PROD_END: d(ago + 30),
                                ct_.DEL_BUDGET: d(ago - 5), ct_.PAY_PLAN: d(ago - 60), "assignedById": "31"})
            self.hist[172] += [(str(oid), "DT172_26:NEW", t(480)), (str(oid), f"DT172_26:{stage}", t(ago - 1))]


def test_шаг_12_один_во_всех_подвкладках():
    """«Заказ получен клиентом» и «Ожидание оплаты», доставлены 20 р.д. назад без УПД: один шаг
    12, исполнитель менеджер ОРК, 20 р.д. при норме 10 — «ждём УПД от клиента» (решение
    владельца 27.10.2026, Б) в заказе, ячейке сделки, очереди (noUtd, не W2) и ведомости; не
    «сверх нормы» и не «Замедлено». Доставка 400 дн. назад — гигиена «старая доставка без УПД»:
    ни «ждём УПД», ни «в норме», ни «Замедлено»."""
    data, _o = _orders(_Шаг12())
    rows = {r["deal"]: r for r in data["rows"]}
    q = data["life"]["queue"]
    for deal, oid in (("6910", "99200"), ("6911", "99201")):
        r = rows[deal]
        o = r["orders"][0]
        assert (o["step"], o["stepRole"], o["stepWd"], o["stepNorm"], o["stepOver"], o["stepWaitUtd"]) == (
            12, "orkMgr", 20, 10, False, True)
        c = r["stepCell"]
        assert (c["step"], c["role"], c["wd"], c["norm"], c["over"], c["waitUtd"]) == (
            12, "orkMgr", 20, 10, False, True), deal
        # не кандидат в «Замедлено»: ждём клиента, а не простой менеджера — для обеих стадий
        assert "сверх нормы шага" not in r["why"] and not r["slow"] and not o["slow"], (deal, r["why"])
        qq = [x for x in q if x["order"] == oid and x["code"] == "noUtd"]
        assert qq and qq[0]["waitUtd"] and not qq[0].get("w2") and qq[0]["step"] == 12
        assert (qq[0]["role"], qq[0]["days"], qq[0]["copyRole"]) == ("orkMgr", 20, "kam"), "эскалация КАМу"
    old = rows["6912"]
    oo = old["orders"][0]
    assert oo["step"] == 12 and oo["stepOver"] is None and oo["stepHyg"] and "старая доставка" in oo["stepWhy"]
    assert not oo["stepWaitUtd"] and not old["stepCell"]["waitUtd"]
    assert "сверх нормы шага" not in old["why"] and old["stepCell"]["hyg"] and old["stepCell"]["over"] is None
    assert not [x for x in q if x["order"] == "99202" and x["code"] == "noUtd"]
    w = data["ork"]["week"]
    led = {r["no"]: r for r in w["ledger"]}
    W2 = w["tiles"]["W2"]
    assert 12 not in W2["byStep"] and led[12]["over"] == 0
    assert led[12]["waitUtd"] == w["waitUtd"]["n"] == sum(1 for x in q if x["code"] == "noUtd" and x.get("waitUtd"))
    assert {x["order"] for x in q if x.get("waitUtd")} >= {"99200", "99201"}
    assert led[12]["notCounted"] >= 1
    hy = next(x for x in w["hygiene"]["items"] if x["order"] == "99202")
    assert "old" in hy["kinds"] and hy["excl"]
    _инварианты(data)


class _ЗакрытаяШаг5(fixture.ContractsStub):
    """Придуманный корпус: живой заказ в «Подготовка спецификации и инвойса» (шаг 5, оплаты
    поставщику нет) 35 дн. на шаге — у сделки «Оплата получена» (6920), выигранной (6921) и
    открытой (6922, контроль)."""

    def __init__(self):
        super().__init__()
        import contracts as ct_
        now = dt.datetime.fromisoformat(fixture.CT_NOW)
        t = lambda a: (now - dt.timedelta(days=a)).isoformat()            # noqa: E731
        d = lambda a: (fixture.CT_TODAY - dt.timedelta(days=a)).isoformat()  # noqa: E731
        for deal, oid, stage, sem in (("6920", 99400, "C0:PAID", "P"), ("6921", 99401, "C0:WON", "S"),
                                      ("6922", 99402, "C0:PREPARATION", "P")):
            self.deals.append({"ID": deal, "TITLE": f"99{deal[-1]}. Шаг 5 у закрытой", "OPPORTUNITY": "1000",
                               "CURRENCY_ID": "EUR", "COMPANY_ID": "301", "STAGE_ID": stage,
                               "STAGE_SEMANTIC_ID": sem, "CATEGORY_ID": "0", "DATE_CREATE": t(220),
                               "ASSIGNED_BY_ID": "10", ct_.OSS_DEAL: ["21"], ct_.KAM_F: "11"})
            self.hist[2] += [(deal, "C0:NEW", t(220)), (deal, stage, t(12))]
            self.orders.append({"id": oid, "title": f"{deal}/1. Узел", "companyId": "401", "parentId2": int(deal),
                                "opportunity": 100, "currencyId": "EUR", "stageId": "DT172_26:SPEC",
                                "createdTime": t(150), "movedTime": t(35), ct_.OSS_F: "21",
                                ct_.DL_CUSTOMER: d(-60), ct_.SUP_PAID: 0, "assignedById": "31"})
            path = fixture.CT_PATH[:fixture.CT_PATH.index("SPEC") + 1]
            self.hist[172] += [(str(oid), f"DT172_26:{st}", t(150 - (115 if st == "SPEC" else 8 * k)))
                               for k, st in enumerate(path)]


def test_закрытая_сделка_шаг_не_12_не_сверх_нормы():
    """Закрытая выигранная сделка и «Оплата получена»: живой заказ на шаге 5 дольше нормы — не
    отклонение (в счёте у закрытой только шаг 12, C12): ни красной строки заказа, ни ⚠
    «сверх нормы шага» в карточке, ни «замедлена» — как ячейка «Шаг», ведомость, W2 и очередь.
    Открытая сделка с тем же заказом — сверх нормы везде."""
    data, _o = _orders(_ЗакрытаяШаг5())
    rows = {r["deal"]: r for r in data["rows"]}
    q = data["life"]["queue"]
    ctl = rows["6922"]
    oc = ctl["orders"][0]
    assert (oc["step"], oc["stepOver"], oc["slow"], oc["stepClosed"]) == (5, True, True, False)
    assert "сверх нормы шага: 1 заказ" in ctl["why"] and ctl["stepCell"]["over"] is True
    assert any(x["code"] == "s3Over" and x["order"] == "99402" for x in q)
    for deal, cell in (("6920", (13, "oss")), ("6921", None)):
        r = rows[deal]
        o = r["orders"][0]
        assert r["closed"] and o["live"] and o["step"] == 5 and o["stepWd"] > o["stepNorm"], deal
        assert not o["slow"] and o["stepOver"] is None and not o["stepWaitUtd"] and o["stepClosed"], deal
        assert "шаг 12" in o["stepWhy"], deal
        assert not r["stuck"] and not r["slow"] and "сверх нормы шага" not in (r["why"] or ""), deal
        c = r["stepCell"]
        assert (c and (c["step"], c["role"])) == (cell or None), deal
        assert not [x for x in q if x["order"] == o["id"] and x["code"].endswith("Over")], deal
    # W2 — без заказов закрытых сделок вне шага 12
    assert not [x for x in q if x.get("w2") and x["order"] in ("99400", "99401")]
    _инварианты(data)


def test_ячейка_сделки_сверх_нормы_первым_и_оплата_получена(ct):
    """Ячейка «шаг · исполнитель · р.д./норма»: есть заказ сверх нормы — он (почему строка
    замедлена); «Оплата получена», сделка не закрыта — шаг 13, ОСС «закрыть сделку» (п. 4)."""
    for r in ct["rows"]:
        if any(o["live"] and o["stepOver"] for o in r["orders"]) and not r["closed"]:
            assert r["stepCell"]["over"] is True, r["deal"]
    paid = [r for r in ct["rows"] if r["paidStage"]]
    assert paid
    for r in paid:
        c = r["stepCell"]
        o12 = [o for o in r["orders"] if o["live"] and o["step"] == 12 and o["stepWaitUtd"]]
        if o12:      # ждём УПД от клиента — шаг 12 менеджера ОРК, как в карточке и ведомости (сделка 6030)
            assert (c["step"], c["role"], c["over"], c["waitUtd"], c["wd"], c["close"]) == (
                12, "orkMgr", False, True, o12[0]["stepWd"], "paid")
        else:
            assert (c["step"], c["role"], c["label"], c["close"]) == (13, "oss", "закрыть сделку", "paid")
    assert any(r["stepCell"]["step"] == 12 for r in paid), "корпус: «Оплата получена», ждём УПД"
    # все заказы закрыты, сделка открыта (dealOpen) — шаг 13 ОСС, а не «—»
    for r in ct["rows"]:
        if not r["closed"] and r["orders"] and not any(o["live"] for o in r["orders"]) \
                and all(o["stage"].endswith(":SUCCESS") for o in r["orders"]):
            assert (r["stepCell"]["step"], r["stepCell"]["role"], r["stepCell"]["close"]) == (13, "oss", "open")


def _синт(i, stage, **kw):
    o = {"id": str(i), "deal": "D1", "seq": 1, "title": f"o{i}", "sup": "S", "live": True, "success": False,
         "stage": stage, "stageSince": "2026-10-01T10:00:00+03:00", "stepSince": "2026-10-01T10:00:00+03:00",
         "t0": "2026-09-01T10:00:00+03:00", "created": dt.date(2026, 9, 1), "dl": dt.date(2026, 12, 1),
         "prodStart": dt.date(2026, 9, 5), "prodEnd": dt.date(2026, 11, 20), "delBudget": dt.date(2026, 11, 25),
         "utd": False, "supPaidPct": 0, "art": {}, "_reach": {}, "_segs": {}, "orkMgr": "М", "respOrk": True}
    o.update(kw)
    return o


_СДЕЛКИ = [{"deal": "D1", "seq": 1, "customer": "К", "closed": False, "lost": False, "inCat0": True, "norders": 2,
            "cat0At": "2026-09-01T10:00:00+03:00", "kam": "К", "kamId": "1", "orkResp": "", "ossDeal": "О",
            "dealStepMax": 9}]


def test_неизвестные_стадии_не_ноль():
    """Стадии переименованы в портале (не из таблицы шагов): W1, W2, W5 — «Нет данных», а не
    «0 · сейчас на шагах никого»; почти все неизвестны — покрытие падает, «не показываем»."""
    import lifecycle
    T = dt.date(2026, 10, 26)
    orders = [_синт(i, "Новая стадия X") for i in range(10)]
    life = lifecycle.build(orders, _СДЕЛКИ, T)
    v, _ = orkview.build(orders, _СДЕЛКИ, life["queue"], T)
    t = v["week"]["tiles"]
    for k in ("W1", "W2", "W5"):
        assert t[k]["state"] == "nodata" and t[k]["value"] is None and "STAGE_STEP" in t[k]["need"], (k, t[k])
    orders = [_синт(i, "Новая стадия X") for i in range(8)] + [
        _синт(100 + i, "Подготовка спецификации и инвойса", stepSince="2026-10-22T10:00:00+03:00") for i in range(2)]
    life = lifecycle.build(orders, _СДЕЛКИ, T)
    v, _ = orkview.build(orders, _СДЕЛКИ, life["queue"], T)
    W2 = v["week"]["tiles"]["W2"]
    assert W2["n"] == 10 and W2["state"] == "hidden" and W2["value"] is None and "покрытие 20 %" in W2["why"]
    assert "byStep" not in W2, "скрытая плитка — без слагаемых значения"


def test_скрытая_ячейка_без_k():
    """«Не показываем» не читается как «k из n»: у hidden нет ни value, ни k."""
    hid = orkview._timeliness([_seg(1)] * 9 + [_seg(1, burst=True)] * 3, "S2")
    assert hid["state"] == "hidden" and hid["value"] is None and "k" not in hid
    ret = orkview._timeliness([{**_seg(1), "ret": True}] * 4, "S2")
    assert ret["state"] == "hidden" and "возврат" in ret["why"]


def test_месяц_отдела_по_людям():
    """M1/M2 отдела за одним-двумя ответственными — показатель человека: без права не
    показываем (решение п. 3); за тремя — показываем (предварительно до v60)."""
    T = dt.date(2026, 10, 26)
    def era(i, who):
        seg = {**_seg(1, end="2026-10-10T10:00:00+03:00"), "skip": False}
        return {"id": str(i), "created": dt.date(2026, 9, 1), "resp": who, "orkMgr": who, "respOrk": True,
                "stage": "Оплата поставщику / агенту", "_segs": {"segs": {"S2": seg}},
                "_reach": {5: "2026-10-10T10:00:00+03:00", 6: "2026-10-10T10:00:00+03:00"}, "art": {"po": True}}
    один, _ = orkview._month([era(i, "Илья") for i in range(12)], [], T, None, {})
    s2 = один["rows"][0]["M1"]["S2"]
    assert s2["state"] == "hidden" and "k" not in s2 and s2["value"] is None and "раскрыл бы человека" in s2["why"]
    assert один["rows"][0]["M2"]["state"] == "hidden" and "have" not in один["rows"][0]["M2"]
    трое, _ = orkview._month([era(i, ("Илья", "Пётр", "Олег")[i % 3]) for i in range(12)], [], T, None, {})
    s2 = трое["rows"][0]["M1"]["S2"]
    assert s2["state"] == "prelim" and s2["value"] == 100 and s2["k"] == 12 and s2["people"] == 3


# ── ревизия 3: последний полный месяц, «дольше P80» шага 7 ────────────────────────────────
def test_месяц_заголовок_последний_полный():
    """Заголовок «Месяца» — последний полный месяц (head); текущий идёт — partial, рядом. У
    менеджера отрезок сентября — в главном, октября — в «идёт, неполный»."""
    T = dt.date(2026, 10, 26)

    def era(i, who, end):
        seg = {**_seg(1, end=end), "skip": False}
        return {"id": str(i), "created": dt.date(2026, 8, 25), "resp": who, "orkMgr": who, "respOrk": True,
                "stage": "Оплата поставщику / агенту", "_segs": {"segs": {"S2": seg}}, "_reach": {}, "art": {}}
    orders = [era(i, ("Илья", "Пётр", "Олег")[i % 3], "2026-09-15T10:00:00+03:00") for i in range(9)]
    orders += [era(100 + i, "Илья", "2026-10-15T10:00:00+03:00") for i in range(2)]
    month, mp = orkview._month(orders, [], T, None, {})
    rows = month["rows"]
    assert month["head"] == 1 and rows[1]["m"] == "09.2026" and not rows[1]["partial"]
    assert rows[0]["m"] == "10.2026" and rows[0]["partial"] is True
    assert rows[1]["M1"]["S2"]["n"] == 9
    # октябрь — один человек: ячейка скрыта воротами людей, и n у неё не отдаётся
    assert rows[0]["M1"]["S2"]["state"] == "hidden" and "n" not in rows[0]["M1"]["S2"]
    илья = next(m for m in mp if m["who"] == "Илья")
    assert илья["m"] == "09.2026" and илья["M1"]["S2"]["n"] == 3
    assert илья["cur"]["partial"] and илья["cur"]["M1"]["S2"]["n"] == 2


def _s5(i, cd, end="2026-09-01T10:00:00+03:00"):
    return {"id": f"m{i}", "sup": "П", "created": dt.date(2025, 12, 1), "live": False,
            "_segs": {"segs": {"S5": {"cd": cd, "end": end, "start": "2026-07-01T10:00:00+03:00",
                                      "skip": False, "burst": False, "ret": False, "neg": False}}}}


def test_шаг7_дольше_p80_сигнал_а_не_нарушение():
    """Живой заказ в производстве дольше P80 S5 по всем заказам — сигнал ведомости и «Ждут
    смежников» (мяч у поставщика), не «сверх нормы»: шаг 7 регламент не нормирует."""
    import lifecycle
    T = dt.date(2026, 10, 26)
    measure = [_s5(i, 10 + i) for i in range(30)]          # P80 = s[⌈0,8·30⌉−1] = s[23] = 33
    def prod(i, days, **kw):
        at = (dt.datetime(2026, 10, 26, 10, tzinfo=dt.timezone(dt.timedelta(hours=3)))
              - dt.timedelta(days=days)).isoformat()
        return _синт(i, "Производство заказа", supPaidPct=100, _reach={7: at}, stepSince=at, **kw)
    orders = [prod(1, 60), prod(2, 5), _синт(3, "Производство заказа", supPaidPct=100, stepSince="", stageSince="")]
    life = lifecycle.build(orders, _СДЕЛКИ, T)
    v, _ = orkview.build(orders, _СДЕЛКИ, life["queue"], T, measure=measure + orders)
    lp = v["week"]["longP80"]
    assert lp["state"] == "show" and lp["p80"] == 33 and lp["k"] == 1 and lp["n"] == 2 and lp["unknown"] == 1
    assert [x["order"] for x in lp["items"]] == ["1"] and lp["items"][0]["days"] == 60
    assert "не нарушение нормы" in lp["label"]
    led7 = next(r for r in v["week"]["ledger"] if r["no"] == 7)
    assert led7["longP80"] == 1 and led7["over"] is None and led7["waits"] == "sup"
    assert all(r["longP80"] is None for r in v["week"]["ledger"] if r["no"] != 7)
    # не в «сверх нормы»: W2 его не считает, пунктов s*Over у заказа нет
    assert v["week"]["tiles"]["W2"]["value"] == 0
    assert not [q for q in life["queue"] if q.get("order") == "1" and q["code"].endswith("Over")]
    # P80 не посчитан — «Нет данных», а не ноль
    v0, _ = orkview.build(orders, _СДЕЛКИ, life["queue"], T, measure=orders)
    lp0 = v0["week"]["longP80"]
    assert lp0["state"] == "nodata" and lp0["k"] is None and lp0["items"] == []
    assert next(r for r in v0["week"]["ledger"] if r["no"] == 7)["longP80"] is None


# ── ревизия 4: шаг 12 закрытых сделок, вычитание в «Месяце», S1 вне «Реализации» ────────────
def test_шаг12_закрытой_сделки_одна_совокупность():
    """Живой заказ сделки «Оплата получена» (закрыта, не проиграна), доставленный без УПД, —
    на шаге 12 у менеджера ОРК (Рег. 14.2.4–14.3.2): пункт noUtd в очереди, и он же — «ждём УПД
    от клиента» в ведомости (шаг 12) и в своём столбце «Сверх нормы по менеджерам»; в W2 и в
    «сверх нормы» не идёт (решение владельца 27.10.2026, Б)."""
    import lifecycle
    T = dt.date(2026, 10, 19)
    deals = [{**_СДЕЛКИ[0], "deal": "A", "seq": 1, "closed": True, "done": True, "paidStage": True, "norders": 1,
              "stageDays": 40},
             {**_СДЕЛКИ[0], "deal": "B", "seq": 2, "norders": 1}]
    def dlv(i, deal):
        return _синт(i, "Ожидание оплаты", deal=deal, delFact=dt.date(2026, 9, 1), shipFact=dt.date(2026, 7, 20),
                     supPaidPct=100, stepSince="2026-09-01T10:00:00+03:00", orkMgr="Пётр", resp="Пётр")
    orders = [dlv(1, "A"), dlv(2, "B")]
    life = lifecycle.build(orders, deals, T)
    no_utd = [q for q in life["queue"] if q["code"] == "noUtd"]
    assert {q["deal"] for q in no_utd} == {"A", "B"}
    v, _ = orkview.build(orders, deals, life["queue"], T)
    W2 = v["week"]["tiles"]["W2"]
    led12 = next(r for r in v["week"]["ledger"] if r["no"] == 12)
    assert len(no_utd) == led12["waitUtd"] == led12["now"] == v["week"]["waitUtd"]["n"] == 2
    assert led12["over"] == 0 and 12 not in W2["byStep"] and W2["value"] == 0
    assert all(q.get("waitUtd") and not q.get("w2") for q in no_utd)
    ow = v["week"]["overByWho"]
    assert [(m["who"], m["n"], m["waitUtd"], m["byStep"]) for m in ow] == [("Пётр", 0, 2, {})]
    assert v["week"]["closed12"] == 1 and v["week"]["live"] == 2
    # прочие шаги закрытой сделки — не отклонения: W1 и шаги 1/4/5/8 их не видят
    assert W2["byStep"][1] == W2["byStep"][5] == 0


def test_месяц_вычитание_не_раскрывает_скрытую_ячейку():
    """S2 — трое, S3 — один, S6 — трое: S3 скрыта воротами людей, и сумма S2+S3+S6 тоже (иначе
    сумма − показанные = скрытая); у скрытой ячейки нет n, ended, people."""
    T = dt.date(2026, 10, 26)
    end = "2026-09-15T10:00:00+03:00"
    def era(i, who, code):
        seg = {**_seg(1, end=end), "skip": False}
        return {"id": f"{code}{i}", "created": dt.date(2026, 9, 1), "resp": who, "orkMgr": who, "respOrk": True,
                "stage": "Оплата поставщику / агенту", "_segs": {"segs": {code: seg}}, "_reach": {}, "art": {}}
    orders = [era(i, w, "S2") for i, w in enumerate(("Анна", "Борис", "Вера"))]
    orders += [era(9, "Анна", "S3")]
    orders += [era(i, w, "S6") for i, w in enumerate(("Борис", "Вера", "Гриша"))]
    month, _ = orkview._month(orders, [], T, None, {})
    m = month["rows"][1]["M1"]
    assert m["S2"]["state"] == "prelim" and m["S6"]["state"] == "prelim"
    assert m["S3"]["state"] == "hidden" and not ({"n", "ended", "people", "k", "excluded", "skipped"} & set(m["S3"]))
    assert m["mgr"]["state"] == "hidden" and m["mgr"]["value"] is None and "n" not in m["mgr"]
    assert "вычитанием" in m["mgr"]["why"]


def test_месяц_приёмка_вне_реализации_не_в_счёте():
    """S1 сделки вне воронки «Реализация» не в счёте «Месяца» — как у ork.judged и W1."""
    T = dt.date(2026, 10, 26)
    def era(i, deal):
        seg = {**_seg(5, end="2026-09-15T10:00:00+03:00", norm=1), "skip": False}
        return {"id": str(i), "deal": deal, "created": dt.date(2026, 9, 1), "resp": "Р", "orkMgr": "Р",
                "respOrk": True, "_segs": {"segs": {"S1": seg}}, "_reach": {}, "art": {}}
    month, _ = orkview._month([era(1, "D1"), era(2, "D9")], [], T, None, {}, deal_ok_all={"D1": True, "D9": False})
    assert month["rows"][1]["M1"]["S1"]["n"] == 1


def test_первый_месяц_эпохи_неполный():
    T = dt.date(2026, 10, 26)
    month, _ = orkview._month([], [], T, None, {})
    by = {r["m"]: r for r in month["rows"]}
    assert by["08.2026"]["eraPartial"] is True and by["09.2026"]["eraPartial"] is False


def test_год_без_календаря_приёмка_за_20_рд_нет_календаря():
    """Сегодня в году без производственного календаря: «в срок за 20 р.д.» — «Нет данных: нет
    календаря», а не «нет законченных приёмок»."""
    T = dt.date(2099, 2, 1)
    v, _ = orkview.build([], _СДЕЛКИ, [], T)
    rec = v["week"]["tiles"]["W1"]["recent"]
    assert rec["state"] == "nodata" and "календаря" in rec["need"]


def test_стадия_сделки_позади_заказов_по_диапазону_регламента():
    """Пары «стадия сделки — стадия заказа» из таблиц регламента (п. 8.1 шаг 6, 12.1 шаг 10,
    14.1 шаг 12 и шаг 13 с УПД, 13.1 с УПД; п. 2.2–2.3 — шаги 4–5 до договора с заказчиком) —
    не нарушение порядка;
    «Договор с заказчиком подписан» при заказе на складе (шаг 9) — нарушение (ork.DEAL_SPAN,
    одно правило с зондом v60)."""
    T = dt.date(2026, 10, 19)
    pairs = [("Оплата товара произведена | Начато производство", "Производство заказа", {}, 0),
             ("Товар отгружен | Доставка клиенту", "Подготовка груза/Поиск транспорта",
              {"shipFact": dt.date(2026, 9, 1)}, 0),
             ("Доставка клиенту произведена | Ожидание оплаты", "Ожидание оплаты",
              {"delFact": dt.date(2026, 10, 14), "shipFact": dt.date(2026, 9, 1)}, 0),
             # п. 14.1 и 13.1 с подписанным УПД: заказ на шаге 13, сделка ждёт денег клиента в
             # своей стадии («Оплата получена» — только после оплаты, Рег. 15.5) — не позади
             ("Доставка клиенту произведена | Ожидание оплаты", "Ожидание оплаты",
              {"delFact": dt.date(2026, 9, 14), "shipFact": dt.date(2026, 9, 1), "utd": True}, 0),
             ("Доставка клиенту произведена | Ожидание оплаты", "Заказ получен клиентом",
              {"delFact": dt.date(2026, 9, 14), "shipFact": dt.date(2026, 9, 1), "utd": True}, 0),
             ("ТКП согласован (конкурс выигран) | Согласование договора/спецификации с заказчиком",
              "Подготовка спецификации и инвойса", {}, 0),
             ("Договор с заказчиком подписан | Согласование спецификаций/инвойсов с агентами/поставщиками",
              "Доставка до склада", {"shipFact": dt.date(2026, 9, 1), "supPaidPct": 100}, 1)]
    for k, (dst, ost, facts, want) in enumerate(pairs):
        deals = [{**_СДЕЛКИ[0], "deal": f"D{k}", "norders": 1, "dealStep": ork.stage_step(dst, "deal")}]
        orders = [_синт(k, ost, deal=f"D{k}", **facts)]
        v, _ = orkview.build(orders, deals, [], T)
        assert v["how"]["violations"]["dealBehindOrders"] == want, (dst, ost)
    assert ork.deal_behind(4, [9]) and not ork.deal_behind(2, [4, 5]) and not ork.deal_behind(6, [7])
    assert not ork.deal_behind(11, [13]) and ork.deal_behind(9, [11])
    assert not ork.deal_behind(None, [9]) and not ork.deal_behind(4, [])
    # таблица покрывает каждый шаг стадии сделки STAGE_STEP, и шаг стадии внутри своего диапазона
    for st in set(ork.STAGE_STEP["deal"].values()):
        lo, hi = ork.DEAL_SPAN[st]
        assert lo <= st <= hi, st


def test_закрыть_сделку_при_недоставленных_заказах_с_оговоркой():
    """«Оплата получена», а живой заказ не доставлен: пункт dealClose ОСС остаётся (решение п. 4),
    но с оговоркой Рег. 15.6 — закрывать после исполнения обязательств."""
    import lifecycle
    T = dt.date(2026, 10, 19)
    deals = [{**_СДЕЛКИ[0], "deal": "P", "closed": True, "done": True, "paidStage": True, "norders": 1,
              "stageDays": 5}]
    life = lifecycle.build([_синт(1, "Производство заказа", deal="P", supPaidPct=100)], deals, T)
    dc = [q for q in life["queue"] if q["code"] == "dealClose"]
    assert len(dc) == 1 and "не доставлено: 1" in dc[0]["what"] and "15.6" in dc[0]["what"]
    life = lifecycle.build([_синт(1, "Ожидание оплаты", deal="P", supPaidPct=100, delFact=T, utd=True)], deals, T)
    dc = [q for q in life["queue"] if q["code"] == "dealClose"]
    assert len(dc) == 1 and "не доставлено" not in dc[0]["what"]
    # даты доставки нет, УПД подписан — доставлен по дате УПД (решение 25.10.2026, delivered):
    # не «не доставлено», как и в карточке («по УПД»)
    o = _синт(1, "Заказ получен клиентом", deal="P", supPaidPct=100, utd=True, utdDate=dt.date(2026, 9, 30))
    assert lifecycle.delivered(o) == dt.date(2026, 9, 30) and not o.get("delFact")
    dc = [q for q in lifecycle.build([o], deals, T)["queue"] if q["code"] == "dealClose"]
    assert len(dc) == 1 and dc[0]["what"] == "оплата получена, сделка не закрыта"
    # доставлен без УПД — шаг 12 не закончен (Рег. 14.6): в оговорке отдельно от недоставленных
    os_ = [_синт(1, "Производство заказа", deal="P", supPaidPct=100),
           _синт(2, "Заказ получен клиентом", deal="P", supPaidPct=100, delFact=dt.date(2026, 10, 1))]
    dc = [q for q in lifecycle.build(os_, deals, T)["queue"] if q["code"] == "dealClose"]
    assert len(dc) == 1 and "не доставлено: 1" in dc[0]["what"] and "без УПД: 1" in dc[0]["what"]
    assert "15.6, 14.6" in dc[0]["what"]
    assert lifecycle.close_note([_синт(3, "Закрытие заказа", live=False, success=True)]) == ""
