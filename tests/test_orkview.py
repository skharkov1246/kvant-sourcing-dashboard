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
        assert sum(r["now"] for r in w["ledger"]) + w["unknown"] == w["live"] == view["deadlines"]["liveOrders"]
    assert ct["ork"]["trust"]["mapped"] + ct["ork"]["trust"]["unknown"] == ct["ork"]["week"]["live"]


def test_неделя_все_блоки_с_данными(ct):
    w = ct["ork"]["week"]
    t = w["tiles"]
    for k in ("W1", "W2", "W3", "W5"):
        assert t[k]["state"] in ("show", "prelim") and t[k]["value"] > 0, (k, t[k])
    assert t["W4"]["state"] == "nodata" and t["W4"]["value"] is None and "Эскалация" in t["W4"]["need"]
    # W2 — только шаги ОРК и УПД; счёт — тот же, что пункты очереди
    c = ct["life"]["counts"]
    assert t["W2"]["value"] == sum(len({q["order"] for q in ct["life"]["queue"] if q["code"] == k})
                                   for k in ("s1Over", "s2Over", "s3Over", "s6Over", "noUtd"))
    assert set(t["W2"]["byStep"]) == {1, 4, 5, 8, 12} and t["W2"]["label"].endswith(ork.NO_PAUSE)
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
    rows = o["month"]["rows"]
    assert len(rows) == ork.MONTHS_BACK
    assert any(r["M1"]["mgr"]["state"] == "prelim" for r in rows), "M1 — не выше «предварительно» до v60"
    assert all(r["M1"][k]["state"] != "show" for r in rows for k in r["M1"])
    assert any(r["M2"]["state"] in ("show", "prelim") for r in rows)
    assert all(r["otd"]["label"] == "результат поставщика и логистики" for r in rows)
    assert o["month"]["M3"]["state"] == "show" and o["month"]["M3"]["label"] == "без поправки на сложность"
    assert len(o["month"]["an4"]) == len(ork.AN4_NODATA) and {x["state"] for x in o["month"]["an4"]} == {"nodata"}
    q = o["quarter"]
    assert any(x["Q1"]["state"] in ("show", "prelim") for x in q["quarters"])
    assert q["Q2"]["S5"]["state"] in ("show", "prelim") and q["Q2"]["S5"]["p80"] >= q["Q2"]["S5"]["med"]
    assert q["Q2cut"]["category"]["state"] == "nodata" and q["Q2cut"]["direction"]["variants"] == 2
    assert q["Q3"]["state"] in ("show", "prelim") and q["Q3"]["meanP"] is not None
    assert [s["state"] for s in q["scorecard"]].count("nodata") == 5
    assert q["index"]["state"] == "hidden" and q["rating"]["state"] == "hidden"
    h = o["how"]
    assert {m["code"] for m in h["metrics"]} == {"W1", "W2", "W3", "W4", "W5", "M1", "M2", "M3", "Q1", "Q2", "Q3"}
    assert len(h["fieldsAdd"]) == len(ork.FIELDS_ADD) and {f["to"] for f in h["fieldsAdd"]} == {ork.FIELDS_TO}
    assert h["gatesNote"] == ork.GATES_NOTE and h["violations"]["payNoContract"] >= 1
    assert h["violations"]["returns"] >= 1 and h["violations"]["bursts"] >= 1
    assert {a["key"] for a in h["artifacts"]} == {a["key"] for a in ork.ARTIFACTS}
    assert h["kamAgree"]["n"] > 0 and h["kamAgree"]["same"] == h["kamAgree"]["n"]
    assert o["trust"]["yearsNoCal"] == [] and o["trust"]["ownerRule"] == "step" and o["trust"]["orkHead"]


def test_показатели_менеджеров_отдельно_от_вкладки(ct):
    """Решение владельца 26.10.2026, п. 3: поимённые показатели «Месяца» — отдельным
    массивом (orkMonth), в разделе для всех с вкладкой их нет; рейтинга нет."""
    assert ct["orkMonth"]["main"] and all("who" in m for m in ct["orkMonth"]["main"])
    публичный = json.dumps(ct["ork"]["month"], ensure_ascii=False)
    assert not any(m["who"] in публичный for m in ct["orkMonth"]["main"])
    assert all(set(m) >= {"who", "live", "M1", "M2"} and "score" not in m for m in ct["orkMonth"]["main"])


def test_страница_показатели_менеджеров_своим_массивом_под_правом():
    """dashboard.render: поимённые показатели «Месяца» — строкой window.__ORKMONTH__ (воркер
    вырезает её без права «ork_heads»), в window.__CONTRACTS__ их нет; без данных — null."""
    import dashboard
    r = fixture.build_realization()
    assert r["ork_month"]["main"] and "orkMonth" not in r["contracts"]
    html = dashboard.render({}, {}, contracts=r["contracts"], dealctx=r["dealctx"], ork_month=r["ork_month"])
    lines = {ln.split(" = ", 1)[0]: ln for ln in html.splitlines() if ln.startswith("window.__")}
    om = json.loads(lines["window.__ORKMONTH__"].split(" = ", 1)[1].rstrip(";"))
    assert om == json.loads(json.dumps(r["ork_month"], ensure_ascii=False, default=str))
    ct_line = lines["window.__CONTRACTS__"]
    assert '"orkMonth"' not in ct_line and '"loadDev"' not in ct_line
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
    assert cells and all(c["label"] == ork.NO_PAUSE for c in cells)
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
    assert (z["state"], z["value"]) == ("show", 0)
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
    assert (q2["state"], q2["value"], q2["n"]) == ("show", 0, 6)
