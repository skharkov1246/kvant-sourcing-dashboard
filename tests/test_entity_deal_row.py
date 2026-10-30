"""Сделка и заказ поставщика: строка сделки (contracts.compute, пакет PY-4) и пункты закрытой
части (dealctx.build, пакет PY-3) — спецификация «сделка и заказ поставщика» 30.10.2026, §1.6,
§4.3; жалоба владельца 30.10.2026 («перепутаны сделки и заказы поставщика»).

Инварианты: пункт сделки не берёт менеджера ОРК и шаг у одного заказа; флаг договора с заказом —
пункт этого заказа; срок сделки — только по недоставленным заказам и с номером; ячейка шага — с
номером заказа; у каждого счёта — своя единица; счёт — разные объекты, а не длина списка.

Корпус придуман (CLAUDE.md, правило 18): сделки ContractsStub и свод fixture.realctx_raw.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import contracts  # noqa: E402
import dealctx  # noqa: E402
import lifecycle  # noqa: E402
from tests import fixture  # noqa: E402

NOW = dt.datetime.fromisoformat(fixture.CT_NOW)
TODAY = fixture.CT_TODAY


@pytest.fixture(scope="module")
def R():
    return fixture.build_realization()


def _rows(R) -> dict:
    return {x["deal"]: x for x in R["contracts"]["rows"]}


def _dkey(s: str) -> str:
    return s[6:] + s[3:5] + s[:2]


# ── PY-4: строка сделки ────────────────────────────────────────────────────────────────

def test_срок_сделки_только_по_недоставленным_заказам_и_с_номером(R):
    """E33: у сделки 973 срок 26.09 — у доставленного 973/2; ближайший срок открытого — 06.12 у
    973/1. Ни у одной строки dlNextOpen не берётся у доставленного заказа."""
    r = _rows(R)["6023"]
    assert r["dlNext"] == "26.09.2026", "прежнее поле не меняется"
    assert (r["dlNextOpen"], r["dlNextLbl"]) == ("06.12.2026", "973/1")
    assert r["ordStat"] == {"n": 3, "delivered": 2, "ontime": 2, "late": 0}, "доставлено 2 из 3, к сроку 2"
    for x in R["contracts"]["rows"]:
        open_ = [o for o in x["orders"] if o["live"] and o["dl"] and not o["delivered"]]
        if not open_:
            assert x["dlNextOpen"] == "" and x["dlNextLbl"] == "" and x["dlNextOrder"] == "", x["deal"]
            continue
        o = next(o for o in x["orders"] if o["id"] == x["dlNextOrder"])
        assert not o["delivered"] and o["live"] and o["dl"] == x["dlNextOpen"] and o["lbl"] == x["dlNextLbl"]
        assert _dkey(x["dlNextOpen"]) == min(_dkey(y["dl"]) for y in open_)


def test_счёт_заказов_сделки_в_заказах():
    """ordStat — заказов (провал не считается): доставлено, к сроку, сорван (доставлен позже или
    срок прошёл без доставки); заказ без срока — ни к сроку, ни сорван. Корпус придуман."""
    ords = [
        {"id": "1", "lbl": "900/1", "ordNo": 1, "live": True, "dl": "10.10.2026", "delivered": "", "late": True,
         "oss": "ОСС-1", "ossGone": False, "respGone": True},
        {"id": "2", "lbl": "900/2", "ordNo": 2, "live": True, "dl": "01.12.2026", "delivered": "", "late": False,
         "oss": "", "ossGone": False, "respGone": False},
        {"id": "3", "lbl": "900/3", "ordNo": 3, "live": False, "dl": "01.09.2026", "delivered": "05.09.2026",
         "late": False, "oss": "ОСС-2", "ossGone": True, "respGone": True},
        {"id": "4", "lbl": "900/4", "ordNo": 4, "live": False, "dl": "", "delivered": "01.08.2026", "late": False,
         "oss": "", "ossGone": False, "respGone": False},
        {"id": "5", "lbl": "900/5", "ordNo": 5, "live": False, "lost": True, "dl": "01.07.2026", "delivered": "",
         "late": False, "oss": "", "ossGone": False, "respGone": False},
    ]
    s = contracts.deal_orders_summary(ords)
    assert s["ordStat"] == {"n": 4, "delivered": 2, "ontime": 0, "late": 2}
    # прошедший 10.10 — у живого недоставленного 900/1: это и есть ближайший срок открытого заказа
    assert (s["dlNextOpen"], s["dlNextLbl"], s["dlNextOrder"]) == ("10.10.2026", "900/1", "1")
    assert s["ossOrders"] == [{"order": "1", "lbl": "900/1", "who": "ОСС-1", "gone": False}], "только живые, с номером"
    assert s["respGoneOrders"] == ["1"], "только живые заказы с уволенным ответственным"
    assert contracts.deal_orders_summary([])["ordStat"] == {"n": 0, "delivered": 0, "ontime": 0, "late": 0}


def test_ячейка_шага_с_номером_заказа(R):
    """E32: шаг строки сделки, взятый у заказа, — с номером этого заказа; шаг сделки (1, 13) —
    без номера, единица «сделка». Список шагов — все живые заказы с шагом."""
    n_order = 0
    for x in R["contracts"]["rows"]:
        c = x["stepCell"]
        if not c:
            continue
        live = sorted((o["id"], o["step"]) for o in x["orders"] if o["live"] and o["step"])
        assert sorted((y["order"], y["step"]) for y in c["orders"]) == live, x["deal"]
        if c["unit"] == "order":
            n_order += 1
            o = next(o for o in x["orders"] if o["id"] == c["order"])
            assert c["lbl"] and c["lbl"] == o["lbl"] and c["step"] == o["step"], x["deal"]
        else:
            assert c["unit"] == "deal" and c["step"] in (1, 13) and c["order"] == "" and c["lbl"] == ""
    assert n_order > 10


def test_деньги_долга_клиента_доля_неоплаченных_заказов(R):
    """E35, §1.5: unpaidRev — Σ долей продажи (revEur) неоплаченных заказов, unpaidN — разных
    заказов; сумма всей сделки сюда не идёт. Пункт сделки, свёрнутый по заказам (unit = deal,
    ords), даёт те же заказы, что пункты заказов."""
    ct = R["contracts"]
    queue = list(ct["life"]["queue"]) + list((ct.get("sep") or {}).get("life", {}).get("queue") or [])
    # пункт unpaid — сделки, заказы строками ords (свёртка lifecycle._fold_deal_codes)
    unpaid = {o for q in queue if q["code"] == "unpaid" for o in lifecycle.item_orders(q)}
    assert unpaid, "корпус: долг клиента есть"
    for x in ct["rows"]:
        mine = [o for o in x["orders"] if o["id"] in unpaid]
        assert x["unpaidN"] == len(mine) and x["unpaidRev"] == sum(o["revEur"] for o in mine), x["deal"]
        assert bool(x["unpaidRevLbl"]) == bool(mine)
        assert x["unpaidRev"] <= x["saleEur"] or not x["saleEur"]
    folded = [{"code": "unpaid", "unit": "deal", "order": "", "deal": "1",
               "ords": [{"id": "11"}, {"id": "12"}]}, {"code": "unpaid", "order": "13", "deal": "2"},
              {"code": "noPayPlan", "order": "14", "deal": "2"}]
    assert contracts.unpaid_orders(folded) == {"11", "12", "13"}


def test_единицы_сводок_названы_и_сходятся(R):
    """E37: у сводок сроков, уволенных и фаз — единица каждого счёта; числа те же. Глубина
    просрочки — в заказах: её сумма = lateOrders, а не lateDeals."""
    ct = R["contracts"]
    for v in [ct] + ([ct["sep"]] if ct.get("sep") else []):
        dl = v["deadlines"]
        assert dl["units"]["depth"] == "order" and dl["units"]["lateDeals"] == "deal"
        assert sum(dl["depth"].values()) == dl["lateOrders"]
        assert v["gone"]["units"]["orders"] == "order" and v["gone"]["units"]["deals"] == "deal"
        pu = v["life"]["phaseUnits"]
        assert pu["n"] == "deal" and all(pu[k] == "order" for k in ("orders", "late", "risk", "over"))
        for ph in v["life"]["phases"]:
            assert ph["n"] <= ph["orders"], "сделок в фазе не больше её заказов"
    s = ct["head"]["sep"]
    assert s["lateOrders"] == s["late"] == ct["sep"]["life"]["counts"].get("late", 0)
    assert "bucketCounts" in s


def test_части_очереди_вида_и_всех_видов():
    """bucketCounts — разные объекты своей единицы по частям (§2.1); life_all складывает
    ordCounts/dealCounts двух видов (заказы и сделки видов не пересекаются), а части очереди
    считает заново по общей очереди: человек обоих видов — один. Корпус придуман."""
    q_main = [
        {"code": "late", "unit": "order", "bucket": "exec", "order": "1", "deal": "A", "who": "Пётр", "sev": 1},
        {"code": "risk", "unit": "order", "bucket": "exec", "order": "1", "deal": "A", "who": "Пётр", "sev": 2},
        {"code": "late", "unit": "order", "bucket": "exec", "order": "2", "deal": "A", "who": "не назначен", "sev": 1},
        {"code": "unpaid", "unit": "deal", "bucket": "deal", "order": "", "deal": "A", "who": "Анна", "sev": 3},
        {"code": "noFact", "unit": "order", "bucket": "hyg", "order": "3", "deal": "B", "who": "логистика", "sev": 9},
        {"code": "dealClose", "unit": "deal", "bucket": "hyg", "order": "", "deal": "C", "who": "Глеб", "sev": 9},
    ]
    q_sep = [{"code": "late", "unit": "order", "bucket": "exec", "order": "9", "deal": "S", "who": "Пётр", "sev": 1}]
    bc = dealctx.bucket_counts(q_main)
    assert bc == {"exec": {"orders": 2, "deals": 1, "people": 1}, "deal": {"deals": 1, "people": 1},
                  "hyg": {"orders": 1, "deals": 1}}
    assert dealctx.bucket_counts([{k: v for k, v in q.items() if k != "bucket"} for q in q_main]) is None
    assert contracts.life_bucket_counts({"queue": q_main}) == bc
    assert contracts.life_bucket_counts({"queue": [], "bucketCounts": {"exec": {}}}) == {"exec": {}}
    oc, dc = dealctx.unit_counts(q_main)
    assert oc == {"late": 2, "risk": 1, "noFact": 1} and dc == {"unpaid": 1, "dealClose": 1}
    life = {"queue": q_main, "counts": {"late": 2}, "ordCounts": oc, "dealCounts": dc}
    sep = {"queue": q_sep, "counts": {"late": 1}, "ordCounts": {"late": 1}, "dealCounts": {}}
    both = contracts.life_all({"life": life, "sep": {"life": sep}})
    assert both["ordCounts"]["late"] == 3 and both["dealCounts"] == dc and both["counts"]["late"] == 3
    assert both["bucketCounts"]["exec"] == {"orders": 3, "deals": 2, "people": 1}, "Пётр в двух видах — один"


# ── PY-3: пункты закрытой части ────────────────────────────────────────────────────────

def test_у_каждого_пункта_единица_и_часть_очереди(R):
    dc = R["dealctx"]
    rows = _rows(R)
    for q in dc["queue"]:
        assert q["unit"] in ("order", "deal") and q["bucket"] in dealctx.BUCKETS, q["code"]
        assert (q["unit"] == "order") == bool(q["order"]), q["code"]
        if q["unit"] == "deal":
            # хозяин пункта сделки — роль сделки, не менеджер ОРК (E9); шаг — шаг сделки или нет
            assert q["role"] != "orkMgr", (q["code"], q["deal"])
            assert q["step"] == dealctx._step_now(rows[q["deal"]]) and q["step"] in (*dealctx.DEAL_STEPS, None)
            assert q["ordLbl"] == "" and q["sup"] == ""
        else:
            o = next(o for o in rows[q["deal"]]["orders"] if o["id"] == q["order"])
            assert q["ordLbl"] == o["lbl"] and q["sup"] == o["sup"] and q["step"] == o["step"]
        if q["copyRole"] == "orkMgr" and q["unit"] == "deal":
            mg = dealctx.ord_mgrs(rows[q["deal"]])
            n_live = sum(1 for o in rows[q["deal"]]["orders"] if o["live"])
            assert sum(m["n"] for m in mg) == n_live and q["escalateTo"] == dealctx.ord_mgrs_text(mg)
    # счёты — разные объекты своей единицы
    oc, dcn = dealctx.unit_counts([q for q in dc["queue"] if not q.get("seg")])
    assert dc["ordCounts"] == oc and dc["dealCounts"] == dcn
    assert sum(dc["counts"].values()) == len([q for q in dc["queue"] if not q.get("seg")])
    assert dc["bucketCounts"] == dealctx.bucket_counts([q for q in dc["queue"] if not q.get("seg")])


def test_менеджер_заказа_один_у_строки_и_у_очереди(R):
    """orders[].orkMgr строки — тот же менеджер ОРК, что у пунктов заказа очереди lifecycle
    (ответственный заказа из ОРК, иначе «Ответственный за реализацию» сделки)."""
    rows = _rows(R)
    n = 0
    for q in R["contracts"]["life"]["queue"]:
        if q.get("order") and q["role"] == "orkMgr":
            o = next(o for o in rows[q["deal"]]["orders"] if o["id"] == str(q["order"]))
            assert q["who"] == (o["orkMgr"] or "не назначен"), (q["code"], q["order"])
            n += 1
    assert n > 5


def test_флаг_договора_с_заказом_пункт_этого_заказа():
    """E10: флаги сверки договоров с f.order — по пункту на заказ (номер, поставщик, шаг этого
    заказа, часть «Дела по заказам»); флаг без заказа или с заказом не этой сделки — пункт сделки
    «заказ не определён». Сделок с риском — по-прежнему одна."""
    raw = fixture.realctx_raw()
    raw["contracts"]["6001"]["flags"] = [
        {"code": "penaltyUncovered", "order": 7002, "days": None},
        {"code": "docsGap", "order": 7003, "days": None},
        {"code": "basisGap", "order": 7003, "days": None},
        {"code": "cashGap", "order": None, "days": None},
        {"code": "fxGap", "order": 999999, "days": None},
    ]
    ctx = dealctx.clean(raw)
    ct = fixture.build_contracts(ctx_chats=ctx["chats"], ctx_econ=ctx["econ"], ctx_contracts=ctx["contracts"])
    rows = {x["deal"]: x for x in ct["rows"]}
    head, dc = dealctx.build(ct["rows"], contracts.life_all(ct), ctx, NOW, today=TODAY, ork_head="Роза (рук. ОРК)")
    risk = [q for q in dc["queue"] if q["code"] == "contractRisk"]
    by = {q["order"]: q for q in risk}
    assert set(by) == {"7002", "7003", ""}
    for oid in ("7002", "7003"):
        o = next(o for o in rows["6001"]["orders"] if o["id"] == oid)
        q = by[oid]
        assert (q["unit"], q["bucket"], q["ordLbl"], q["sup"], q["step"]) == ("order", "exec", o["lbl"], o["sup"],
                                                                              o["step"])
        assert q["role"] == "orkHead" and o["lbl"] in q["act"]
    assert by["7003"]["act"].endswith("документы, базис")
    assert by[""]["unit"] == "deal" and by[""]["bucket"] == "deal" and by[""]["what"].endswith(dealctx.ORDER_UNKNOWN)
    assert by[""]["act"].endswith("оплата, валюта")
    assert head["contract"]["risk"] == 1 and head["contract"]["flags"] == 5, "сделок с риском — одна"
    assert dc["counts"]["contractRisk"] == 3 and dc["ordCounts"]["contractRisk"] == 2
    assert dc["dealCounts"]["contractRisk"] == 1
    flags = {f["code"]: f for f in dc["deals"]["6001"]["contract"]["flags"]}
    assert flags["docsGap"]["ordLbl"] == by["7003"]["ordLbl"] and flags["fxGap"]["ordLbl"] == ""


def test_что_делать_первым_с_номером_заказа(R):
    """E38: «что делать первым» по сделке — с единицей и номером заказа, если это пункт заказа."""
    n = 0
    for did, rec in R["dealctx"]["deals"].items():
        t = rec.get("todo")
        if not t:
            continue
        assert t["unit"] in ("order", "deal") and (t["unit"] == "order") == bool(t["order"])
        if t["order"]:
            n += 1
            assert t["ordLbl"], did
    assert n > 5
    assert R["dealctx"]["deals"]["6101"]["todo"]["unit"] == "order"


def test_главное_по_строке_заказа_сводки():
    """Ключ сводки oN — N-я строка заказов сводки (живые заказы строки по порядку): один заказ —
    пункт заказа; несколько или ни одного — сделки. Корпус придуман."""
    r = {"deal": "1", "orders": [
        {"id": "a", "lbl": "1/1", "live": True, "step": 5, "stageName": "x"},
        {"id": "b", "lbl": "1/2", "live": False, "step": 13, "stageName": "x"},
        {"id": "c", "lbl": "1/3", "live": True, "step": 9, "stageName": "y", "orkMgr": "Пётр"}]}
    assert [o["id"] for o in dealctx.imp_orders(["o2"], r)] == ["c"], "закрытый заказ в сводку не входит"
    assert [o["id"] for o in dealctx.imp_orders(["o1", "o1", "stage"], r)] == ["a"]
    assert dealctx.imp_orders(["o3"], r) == []
    x = {"kind": "срыв_срока_клиента", "sev": "высокая", "text": "Срок под угрозой", "keys": ["o2"],
         "ev": NOW}
    it = dealctx._imp_item(x, {**r, "kam": "Анна"}, TODAY, dealctx._people({**r, "kam": "Анна"}, "Роза"))
    assert (it["unit"], it["order"], it["ordLbl"], it["who"], it["step"]) == ("order", "c", "1/3", "Пётр", 9)
    it2 = dealctx._imp_item({**x, "keys": ["o1", "o2"]}, {**r, "kam": "Анна"}, TODAY,
                            dealctx._people({**r, "kam": "Анна"}, "Роза"))
    assert it2["unit"] == "deal" and it2["who"] == "Роза" and it2["roleKey"] == "orkHead"
    assert it2["copy"] == "менеджеры заказов: Пётр (1), не назначен (1)"
    assert (it2["alsoCopy"], it2["alsoCopyRole"]) == ("Анна", "kam"), "КАМ из копии ITEM не теряется"


def test_пункт_сделки_без_заказов_приёмка():
    """Сделка в реализации без заказа: шаг пункта сделки — 1 (приёмка); менеджеров заказов нет —
    копия по таблице ITEM (КАМ у риска по чату)."""
    r = {"deal": "1", "kam": "Анна", "norders": 0, "orders": [], "dealStep": 2, "inCat0": True}
    ow = dealctx._reg("chatRisk", r, dealctx._people(r, "Роза"))
    assert (ow["step"], ow["role"], ow["who"], ow["copyRole"], ow["escalateTo"]) == (1, "orkHead", "Роза", "kam", "Анна")
