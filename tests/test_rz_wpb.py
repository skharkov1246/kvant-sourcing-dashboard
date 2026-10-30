"""«Реализация» R4, пакет WP-B «Неделя»: изменения данных Д-1, Д-3, Д-4, Д-5а, Д-6, Д-7, Д-8, Д-9,
Д-12 (спецификация rz_r4_spec, раздел 4). Каждый инвариант — дважды разными способами (CLAUDE.md,
26.09.2026): юнит-тест помощника orkview на придуманных заказах и сверка в сборке на корпусе
fixture.build_realization (настоящий contracts.compute → orkview.build). Корпус придуман
(CLAUDE.md, правило 18): «Компания N», номера 9xx, суммы условные.
"""
from __future__ import annotations

import datetime as dt
import sys
import types
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import lifecycle  # noqa: E402
import ork  # noqa: E402
import orkview  # noqa: E402
from tests import fixture  # noqa: E402

TODAY = dt.date(2026, 10, 19)         # понедельник


def _q(order, code, deal="d1", days=1, who="Менеджер А", ref=""):
    return {"order": order, "code": code, "deal": deal, "days": days, "who": who, "ref": ref}


# ── Д-1: W3 по заказам, каждый заказ — один раз, по самому тяжёлому виду ─────────────────────
def test_д1_самый_тяжёлый_вид_и_сумма_частей():
    # заказ 901 — и «сорван», и «прогноз позже» (два пункта): в «сорван» один раз; 902 — под угрозой
    # и прогноз позже → «до срока»; 903 — только прогноз
    rq = [_q("901", "forecast"), _q("901", "late"), _q("902", "forecast"), _q("902", "risk"), _q("903", "forecast")]
    h = orkview.w3_heavy(rq)
    assert h == {"901": "late", "902": "risk", "903": "forecast"}
    P = orkview.w3_parts(rq, h)
    assert P["late"]["k"] == ["901"] and P["risk"]["k"] == ["902"] and P["forecast"]["k"] == ["903"]
    assert sorted(P["threat"]["k"]) == ["902", "903"]
    # инвариант: сумма частей = число разных заказов W3; «под угрозой» = risk + forecast
    assert len(P["late"]["k"]) + len(P["threat"]["k"]) == len({q["order"] for q in rq})
    assert len(P["threat"]["k"]) == len(P["risk"]["k"]) + len(P["forecast"]["k"])
    assert all(len(set(v["k"])) == len(v["k"]) for v in P.values()), "заказ в части — один раз"


@pytest.fixture(scope="module")
def built():
    return fixture.build_realization()


def _views(built):
    ct = built["contracts"]
    return [("main", ct), ("sep", ct["sep"])]


def test_д1_в_сборке(built):
    for nm, v in _views(built):
        W = v["ork"]["week"]
        T3 = W["tiles"]["W3"]
        bo = T3["byOrders"]
        assert sum(bo.values()) == T3["value"], nm                          # sum(byOrders) == W3.value
        P = W["drill"]["W3p"]
        assert len(P["late"]["k"]) == bo["late"] and len(P["threat"]["k"]) == bo["risk"] + bo["forecast"], nm
        assert set(P["late"]["k"]).isdisjoint(P["threat"]["k"]), nm
        assert T3["byDeals"]["late"] <= bo["late"]
        # второй способ: из очереди вида напрямую — разные заказы пунктов late/risk/forecast
        rq = [q for q in v["life"]["queue"] if q["code"] in ("late", "risk", "forecast")]
        assert len({q["order"] for q in rq}) == T3["value"], nm
        cnt = Counter(q["code"] for q in rq)
        assert all(T3["by"][c] == cnt.get(c, 0) for c in ("late", "risk", "forecast")), nm   # старое «by» — пункты


# ── Д-3: «Клиент не оплатил в срок» ─────────────────────────────────────────────────────────
def test_д3_неоплата_разные_заказы_и_старейший():
    deal_by = {"d1": {"saleEur": 1000, "saleKnown": True}, "d2": {"saleEur": 500, "saleKnown": True},
               "d3": {"saleEur": 9999, "saleKnown": False}}
    items = [_q("911", "unpaid", "d1", 10, ref="оплата до 09.10.2026"), _q("911", "unpaid", "d1", 3),
             _q("912", "unpaid", "d2", 40), _q("913", "unpaid", "d3", 1)]
    u = orkview._unpaid(items, deal_by, TODAY)
    assert u["orders"] == 3 == len(u["keys"]) and u["deals"] == 3
    assert u["saleEur"] == 1500, "курса нет — сумма не складывается"
    assert u["oldest"] == (TODAY - dt.timedelta(days=40)).isoformat()
    assert u["tags"][u["keys"].index("911")] == "оплата до 09.10.2026"


def test_д3_в_сборке(built):
    for nm, v in _views(built):
        W = v["ork"]["week"]
        U = W["tiles"]["unpaid"]
        # unpaid — пункт сделки с заказами в ords (решение 30.10.2026): плитка — в заказах
        want = {q["order"] for q in lifecycle.per_order(v["life"]["queue"]) if q["code"] == "unpaid"}
        assert U["orders"] == len(want) == len(W["drill"]["unpaid"]["k"]), nm
        assert set(W["drill"]["unpaid"]["k"]) == {str(k) for k in want}, nm
        assert "keys" not in U and "label" in U


# ── Д-4: одна причина на заказ ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("codes,step,over,want", [
    (["s3Over", "prodDev"], 5, True, ("our", "sup")),
    (["prodDev"], 7, False, ("sup", "")),
    ([], 9, False, ("log", "")),
    ([], 11, False, ("log", "")),
    (["noUtd"], 12, False, ("kam", "")),
    ([], 6, False, ("oss", "")),
    (["noPlanDates"], 5, False, ("nodata", "")),
    ([], None, False, ("nodata", "")),
    ([], 4, False, ("ork", "")),                   # шаг ОРК в норме — «на шаге ОРК», не «дольше нормы»
    ([], 5, False, ("ork", "")),                   # ревью раунда 3: вошёл в шаг 5, норма 9 не прошла
    ([], 8, False, ("ork", "")),
    ([], 4, True, ("our", "")),                    # тот же шаг дольше нормы — «наш шаг дольше нормы»
    (["late", "noPlanDates"], 7, False, ("sup", "nodata")),
])
def test_д4_причина(codes, step, over, want):
    assert orkview.order_cause(codes, step, over) == want


def test_д4_в_сборке_сумма_причин_равна_числу(built):
    for nm, v in _views(built):
        W = v["ork"]["week"]
        rows = orkview.unpack(W["orders"], W["strs"])
        assert len(rows) == W["live"], nm
        assert all(r["cause"] in orkview.CAUSES for r in rows), nm
        by = {r["k"]: r["cause"] for r in rows}
        # по каждому списку заказов «Недели»: сумма причин = длина списка (ключи заказов)
        lists = [W["drill"]["W2"], W["drill"]["W3p"]["late"]["k"], W["drill"]["W3p"]["threat"]["k"], W["drill"]["live"]]
        lists += [e["now"] for e in W["drill"]["step"].values()]
        for L in lists:
            ks = [k for k in L if k in by]
            assert sum(Counter(by[k] for k in ks).values()) == len(ks), nm
        # второй способ: cause2 ≠ cause и тоже из списка
        assert all(r.get("cause2", "") in ("", *orkview.CAUSES) and r.get("cause2") != r["cause"] for r in rows)


# ── Д-5а, Д-6: ведомость — разные заказы без файла, «дольше всех» ──────────────────────────
def test_д5а_д6_в_сборке(built):
    for nm, v in _views(built):
        W = v["ork"]["week"]
        rows = orkview.unpack(W["orders"], W["strs"])
        for s in W["ledger"]:
            e = W["drill"]["step"][str(s["no"])]
            assert s["artOrders"] <= s["artMiss"], (nm, s["no"])                 # artOrders ≤ artPairs
            assert s["artOrders"] == len(e["artO"]["k"]) == len(set(e["art"]["k"])), (nm, s["no"])
            if s["no"] not in ork.NORM_WD:
                assert s["maxWd"] is None
                continue
            # второй способ: максимум р.д. по строкам заказов шага в счёте (не «срок неизвестен», не «не в счёте»)
            wd = [r["wd"] for r in rows if r.get("step") == s["no"] and r.get("wd") is not None
                  and not r.get("tu") and not r.get("nc")]
            assert s["maxWd"] == (max(wd) if wd else None), (nm, s["no"])
        assert any(s["maxWd"] is not None for s in W["ledger"]) or nm == "sep"


# ── Д-7: сорванный срок по исполнителю ─────────────────────────────────────────────────────
def test_д7_в_сборке(built):
    for nm, v in _views(built):
        W = v["ork"]["week"]
        assert sum(W["lateByWho"].values()) == W["tiles"]["W3"]["byOrders"]["late"], nm
        for w, n in W["lateByWho"].items():
            assert len(W["drill"]["who"][w]["late"]) == n, (nm, w)
        # второй способ: пункты late очереди по исполнителю
        want = Counter((q.get("who") or "не назначен") for q in v["life"]["queue"] if q["code"] == "late")
        assert dict(want) == W["lateByWho"], nm


# ── Д-8: даты у точек и «дольше обычного» ──────────────────────────────────────────────────
def test_д8_в_сборке(built):
    W = built["contracts"]["ork"]["week"]
    assert W["checkpoints"], "корпус: есть звонки поставщикам"
    for c in W["checkpoints"]:
        assert c["prodStart"] and c["prodEnd"], c
        if c["kind"] == "P50":
            a, b = dt.date.fromisoformat(c["prodStart"]), dt.date.fromisoformat(c["prodEnd"])
            assert a <= dt.date.fromisoformat(c["date"]) <= b
    it = W["longP80"]["items"]
    assert it and all(x["since"] for x in it)
    for x in it:
        assert (TODAY_OF(built) - dt.date.fromisoformat(x["since"])).days == x["days"]


def TODAY_OF(built) -> dt.date:
    return dt.date.fromisoformat(built["contracts"]["ork"]["asOf"])


# ── Д-9: ключи за счётами подписей ─────────────────────────────────────────────────────────
def test_д9_ключи_за_счётами(built):
    for nm, v in _views(built):
        W = v["ork"]["week"]
        D, T1, T5 = W["drill"], W["tiles"]["W1"], W["tiles"]["W5"]
        assert len(D["W1byAssign"]) == T1.get("byAssign", 0), nm
        assert len(D["W1assignStale"]) == T1.get("assignStale", 0), nm
        assert len(D["W1assignNoMgr"]) == T1.get("assignNoMgr", 0), nm
        assert len(D["W5prod"]) == (T5.get("prod") or 0), nm
        assert len(D["W5noDates"]) == (T5.get("noDates") or 0), nm
        assert len(D["noCal"]) == v["ork"]["trust"]["noCalendar"], nm
        rows = orkview.unpack(W["orders"], W["strs"])
        assert all(("why" not in r) or r["why"] for r in rows)
        assert all(r.get("ncWhy") for r in rows if r.get("nc")), "у «не в счёте» — почему"


# ── Д-12: «сорван» на прошлые пятницы ──────────────────────────────────────────────────────
def test_д12_история_на_пятницы():
    fr = orkview._fridays(TODAY)
    assert len(fr) == 12 and fr[-1] == dt.date(2026, 10, 16) and all(d.weekday() == 4 for d in fr)
    assert orkview._fridays(dt.date(2026, 10, 16))[-1] == dt.date(2026, 10, 9), "сегодня пятница — прошлая"
    deal_by = {"d1": {"lost": False}, "d2": {"lost": True}}
    o = lambda **k: {"deal": "d1", "live": True, "created": dt.date(2026, 6, 1), **k}  # noqa: E731
    orders = [
        o(id=1, dl=dt.date(2026, 10, 1)),                                     # сорван с 02.10
        o(id=2, dl=dt.date(2026, 9, 1), delFact=dt.date(2026, 10, 10)),        # сорван 04.09–09.10
        o(id=3, dl=dt.date(2026, 9, 1), deal="d2"),                           # проигранная сделка
        o(id=4, dl=dt.date(2026, 9, 1), live=False, success=False),           # провален
        o(id=5, dl=dt.date(2026, 9, 1), created=dt.date(2026, 10, 12)),       # создан позже
        o(id=6, dl=dt.date(2026, 10, 30)),                                    # срок впереди
    ]
    h = orkview.late_hist(orders, deal_by, TODAY)
    by = dict(zip(fr, h))
    assert by[dt.date(2026, 8, 28)] == 0
    assert by[dt.date(2026, 9, 4)] == 1 and by[dt.date(2026, 9, 25)] == 1
    assert by[dt.date(2026, 10, 2)] == 2 and by[dt.date(2026, 10, 9)] == 2
    assert by[dt.date(2026, 10, 16)] == 2, "доставлен 10.10, но 1 сорван, 5 создан 12.10 и сорван"


def test_д12_в_сборке(built):
    for nm, v in _views(built):
        W = v["ork"]["week"]
        L = W["tiles"]["late"]
        assert len(L["hist"]) == 12 == len(L["fridays"]) and "переносы срока не хранятся" in L["label"]
        late = W["tiles"]["W3"]["byOrders"]["late"]
        # последнее значение = сорванные сейчас ± заказы, созданные или сорванные после пятницы
        assert abs(L["hist"][-1] - late) <= max(3, late // 2), (nm, L["hist"], late)
