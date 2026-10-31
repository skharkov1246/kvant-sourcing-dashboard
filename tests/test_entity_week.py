"""Сделка и заказ поставщика в данных «Недели», «Месяца», «Квартала» и «Как считается»
(спецификация «сделка и заказ» 30.10.2026, пакет P3: PY-5 + PY-6; решения владельца
30.10.2026: приёмка — одна на сделку, у руководителя ОРК; заказ, добавленный в сделку позже, —
назначение менеджера ОРК на новый заказ).

Инварианты пакета:
  * W1 — в сделках (ключи «d:»), W1o — в заказах; W2 — только шаги 4, 5, 8;
  * ведомость: шаги 1–3 и 13 — в сделках, 4–12 — в заказах; Σ заказных строк + ordersNow
    сделочных + неизвестные = живые заказы;
  * «Кого ждём» — {orders, deals}; «Клиент не оплатил» — сделки, заказы рядом, деньги — доля заказов;
  * у каждого списка за числом одна единица (unit), ключ сделки — только в списке сделок;
  * «Месяц»: S1 — один отрезок на сделку; доставка к сроку и в сделках (dueDeals); M2 —
    заказы рядом с файлами; «Квартал»: цикл сделки; «Как считается»: чей шаг и нарушения по
    единицам; гарантия — заказы и сделки.
Корпус придуман (CLAUDE.md, правило 18): сделки «A»…«F», заказы «a1»…, люди — ролями.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import lifecycle  # noqa: E402
import ork  # noqa: E402
import orkview  # noqa: E402
from tests import fixture  # noqa: E402

T = dt.date(2026, 10, 26)
OLD = "2026-10-01T10:00:00+03:00"


def _o(i, deal, stage, **kw):
    o = {"id": i, "deal": deal, "seq": 0, "title": f"{deal}/{i}", "lbl": f"{deal}/{i}", "sup": "Поставщик П",
         "live": True, "success": False, "stage": stage, "stageSince": OLD, "stepSince": OLD, "t0": OLD,
         "created": dt.date(2026, 9, 30), "dl": dt.date(2026, 12, 20), "prodStart": dt.date(2026, 10, 5),
         "prodEnd": dt.date(2026, 12, 1), "delBudget": dt.date(2026, 12, 5), "utd": False, "supPaidPct": 0,
         "art": {}, "_reach": {}, "_segs": {}, "orkMgr": "", "respOrk": False}
    o.update(kw)
    return o


def _d(deal, seq, **kw):
    d = {"deal": deal, "seq": seq, "customer": f"Компания {seq}", "closed": False, "lost": False, "inCat0": True,
         "norders": 1, "cat0At": OLD, "kam": "КАМ К", "kamId": "1", "orkResp": "", "ossDeal": "ОСС О",
         "dealStepMax": 9, "dealStep": 5, "stageName": "стадия", "orders": []}
    d.update(kw)
    return d


def _corpus():
    """A — три заказа на приёмке (сделка не принята); B — принята (заказ в производстве), новый
    заказ без менеджера ОРК; C — договор с заказчиком (стадия сделки шага 2); D — без заказа;
    E — два доставленных заказа без оплаты клиента; F — три дня назад в «Реализации», без заказа
    (в норме)."""
    paid_wait = dict(delFact=dt.date(2026, 9, 20), shipFact=dt.date(2026, 9, 1), utd=True,
                     utdRaw=dt.date(2026, 9, 22), utdDate=dt.date(2026, 9, 22), payPlan=dt.date(2026, 10, 5),
                     supPaidPct=100, orkMgr="Менеджер М", respOrk=True, dl=dt.date(2026, 9, 25))
    orders = [_o("a1", "A", "Заказ создан"), _o("a2", "A", "Заказ создан"), _o("a3", "A", "Заказ создан"),
              _o("b1", "B", "Производство заказа", orkMgr="Менеджер М", respOrk=True, supPaidPct=100),
              _o("b2", "B", "Заказ создан"),
              _o("c1", "C", "Подготовка спецификации и инвойса", orkMgr="Менеджер М", respOrk=True,
                 stepSince="2026-10-22T10:00:00+03:00"),
              _o("e1", "E", "Ожидание оплаты", **paid_wait), _o("e2", "E", "Ожидание оплаты", **paid_wait)]
    deals = [_d("A", 1, norders=3), _d("B", 2, norders=2), _d("C", 3, dealStep=2),
             _d("D", 4, norders=0, dealStep=2), _d("E", 5, norders=2, dealStep=11,
                                                    orders=[{"id": "e1", "revEur": 300}, {"id": "e2", "revEur": 200}]),
             _d("F", 6, norders=0, cat0At="2026-10-26T09:00:00+03:00", dealStep=2)]
    return orders, deals


@pytest.fixture(scope="module")
def built():
    orders, deals = _corpus()
    life = lifecycle.build(orders, deals, T)
    v, m = orkview.build(orders, deals, life["queue"], T, otd=life.get("otd"))
    return orders, deals, life, json.loads(json.dumps(v, default=str)), json.loads(json.dumps(m, default=str))


def _k(v):
    return v["k"] if isinstance(v, dict) else v


# ── W1, W1o, W2: приёмка — одна на сделку ────────────────────────────────────────────────
def test_приёмка_сделки_одна_на_сделку(built):
    _o_, _d_, life, v, _m = built
    w = v["week"]
    T1, T1o, T2 = w["tiles"]["W1"], w["tiles"]["W1o"], w["tiles"]["W2"]
    # у сделки A три заказа на приёмке — пункт s1Over один, пункт СДЕЛКИ с тремя заказами в ords
    # (свёртка lifecycle._fold_deal_codes); по заказам (per_order) — три; D — без заказа дольше 1 р.д.
    A1 = [q for q in life["queue"] if q["code"] == "s1Over" and q["deal"] == "A"]
    assert len(A1) == 1 and A1[0]["unit"] == "deal" and len(A1[0]["ords"]) == 3
    assert sum(1 for q in lifecycle.per_order(life["queue"]) if q["code"] == "s1Over" and q["deal"] == "A") == 3
    assert (T1["unit"], T1["value"], T1["accept"], T1["noOrder"]) == ("deal", 2, 1, 1)
    assert sorted(_k(w["drill"]["W1"])) == ["d:A", "d:D"] and w["drill"]["W1"]["unit"] == "deal"
    # n — сделки на шаге 1 в счёте: A, D и F (F — в норме)
    assert T1["n"] == 3
    # b2 — новый заказ принятой сделки B: назначить менеджера ОРК (W1o), не приёмка сделки
    assert (T1o["unit"], T1o["value"]) == ("order", 1) and _k(w["drill"]["W1o"]) == ["b2"]
    # W2 — шаги 4, 5, 8 (заказы); шага 1 в нём нет
    assert set(T2["byStep"]) == {"4", "5", "8"} and T2["unit"] == "order"
    assert not [q for q in life["queue"] if q["code"] == "s1Over" and q.get("w2")]
    # прежний W1 (заказы + сделки) = 3 + 1 (b2) + 1 (D) = 5 ≥ W1 + W1o: сумма разных единиц — не число
    assert T1["value"] + T1o["value"] <= 5


def test_приёмка_у_руководителя_орк_в_сделках(built):
    w = built[3]["week"]
    acc = {m["who"]: m for m in w["overByWho"]}
    # три пункта s1Over сделки A — одна приёмка сделки у хозяина пункта
    assert sum(m["accept"] for m in acc.values()) == 2               # A и D — разные сделки
    for m in w["overByWho"]:
        assert m["units"]["accept"] == "deal" and m["units"]["n"] == "order"
        assert len(w["drill"]["whoAccept"].get(m["who"], [])) == m["accept"]
        assert all(k.startswith("d:") for k in w["drill"]["whoAccept"].get(m["who"], []))
    assert sum(m["n"] for m in w["overByWho"]) == (w["tiles"]["W2"]["value"] or 0)


# ── ведомость по единицам ────────────────────────────────────────────────────────────────
def test_ведомость_шаги_сделки_в_сделках(built):
    w = built[3]["week"]
    led = {r["no"]: r for r in w["ledger"]}
    assert {n for n, r in led.items() if r["unit"] == "deal"} == {1, 2, 3, 13}
    assert all(r["unit"] == "order" for n, r in led.items() if 4 <= n <= 12)
    # шаг 1: A, D, F; шаг 2 — C (договор с заказчиком; D и F на шаге 1 — приёмка раньше); шаг 3 — своей стадии нет
    assert (led[1]["now"], led[1]["ordersNow"], led[1]["over"]) == (3, 4, 2)
    assert led[2]["now"] == 1 and _k(w["drill"]["step"]["2"]["now"]) == ["d:C"]
    assert led[3]["now"] == 0 and led[3]["noStage"] is True
    # Σ заказных строк + заказы на шагах сделки + стадия не из таблицы = живые заказы
    assert sum(r["now"] if r["unit"] == "order" else r["ordersNow"] for r in led.values()) + w["unknown"] == w["live"]
    for no, r in led.items():
        e = w["drill"]["step"][str(no)]
        assert e["now"]["unit"] == r["unit"] and len(e["now"]["k"]) == r["now"], no
        assert all(k.startswith("d:") == (r["unit"] == "deal") for k in e["now"]["k"]), no
        if r["unit"] == "deal":
            assert len(e["orders"]["k"]) == r["ordersNow"] and e["orders"]["unit"] == "order"


def test_кого_ждём_заказы_и_сделки(built):
    w = built[3]["week"]
    A = w["adjacent"]
    assert set(A) == set(ork.ADJACENT) and all(set(x) == {"orders", "deals"} for x in A.values())
    # КАМ: сделка C на шаге 2 (договор с заказчиком); заказов шага 10 нет
    assert A["kam"] == {"orders": 0, "deals": 1}
    assert A["sup"]["orders"] == 1                                   # b1 — производство
    assert w["drill"]["adj"]["unit"] == "order" and w["drill"]["adjDeals"]["unit"] == "deal"
    for r in ork.ADJACENT:
        assert len(w["drill"]["adj"][r]) == A[r]["orders"] and len(w["drill"]["adjDeals"][r]) == A[r]["deals"]


def test_клиент_не_оплатил_в_сделках(built):
    _o_, _d_, life, v, _m = built
    U = v["week"]["tiles"]["unpaid"]
    # долг по договору — пункт сделки E с двумя заказами в ords (свёртка); по заказам — два
    E1 = [q for q in life["queue"] if q["code"] == "unpaid"]
    assert len(E1) == 1 and E1[0]["unit"] == "deal" and sorted(r["id"] for r in E1[0]["ords"]) == ["e1", "e2"]
    assert sum(1 for q in lifecycle.per_order(life["queue"]) if q["code"] == "unpaid") == 2
    # один долг по договору — одна сделка; заказы — в скобках; деньги — доля этих заказов
    assert (U["unit"], U["deals"], U["orders"], U["rev"]) == ("deal", 1, 2, 500)
    D = v["week"]["drill"]
    assert _k(D["unpaidDeals"]) == ["d:E"] and D["unpaidDeals"]["unit"] == "deal"
    assert sorted(_k(D["unpaid"])) == ["e1", "e2"] and D["unpaid"]["unit"] == "order"
    assert "2 заказа" in D["unpaidDeals"]["t"][0]
    led13 = next(r for r in v["week"]["ledger"] if r["no"] == 13)
    assert led13["unpaidDeals"] == 1
    # строка сделки за числом — одна, с её заказами
    rows = {r["k"]: r for r in orkview.unpack(v["week"]["deals"], v["week"]["strs"])}
    assert "unpaid" in rows["d:E"]["kinds"] and set(rows["d:E"]["ords"]) == {"e1", "e2"}


def test_unpaid_из_пункта_сделки_с_ords():
    """После свёртки по сделке (PY-2) пункт unpaid — сделки с ords: тот же счёт, что у пунктов заказов."""
    deal_by = {"E": {"deal": "E", "saleEur": 900, "orders": [{"id": "e1", "revEur": 300}, {"id": "e2", "revEur": 200}]}}
    folded = [{"code": "unpaid", "deal": "E", "days": 21, "ref": "оплата до 05.10",
               "ords": [{"id": "e1", "lbl": "5/1", "days": 21}, {"id": "e2", "lbl": "5/2", "days": 14}]}]
    per_order = [{"code": "unpaid", "deal": "E", "order": "e1", "ordLbl": "5/1", "days": 21},
                 {"code": "unpaid", "deal": "E", "order": "e2", "ordLbl": "5/2", "days": 14}]
    a, b = orkview._unpaid(folded, deal_by, T), orkview._unpaid(per_order, deal_by, T)
    for u in (a, b):
        assert (u["deals"], u["orders"], u["rev"], u["dkeys"]) == (1, 2, 500, ["d:E"])
    assert a["oldest"] == b["oldest"] == (T - dt.timedelta(days=21)).isoformat()


def test_w5_точки_по_заказам_и_пульс(built):
    w = built[3]["week"]
    W5 = w["tiles"]["W5"]
    assert W5["unit"] == "point" and W5["nUnit"] == "order"
    assert W5.get("orders", 0) == len({c["order"] for c in w["checkpoints"]})
    assert w["drill"]["W5"]["unit"] == "point"
    P = w["pulse"]
    assert P["dealsLabel"] == "сделок с его заказами" and P["dealsSum"] >= P["deals"]
    assert w["hygiene"]["unit"] == "order" and w["hygiene"]["remarks"] >= w["hygiene"]["n"]


# ── списки за числами: одна единица ──────────────────────────────────────────────────────
MAPS = {"who", "adj", "adjDeals", "whoAccept", "whoAssign"}


def _leaves(D: dict, path=""):
    """(путь, единица, ключи) каждого списка drill; у карты по ролям и людям — единица карты."""
    for key, v in D.items():
        p = f"{path}.{key}" if path else key
        if key in MAPS and isinstance(v, dict) and "unit" in v:
            for who, x in v.items():
                if who == "unit":
                    continue
                if isinstance(x, dict):
                    for f, ks in x.items():
                        yield f"{p}.{who}.{f}", v["unit"], ks
                else:
                    yield f"{p}.{who}", v["unit"], x
        elif isinstance(v, dict) and "k" in v:
            yield p, v.get("unit"), v["k"]
        elif isinstance(v, dict):
            yield from _leaves(v, p)
        else:
            yield p, None, v


def _check_units(D: dict, where: str):
    n = 0
    for p, unit, ks in _leaves(D):
        n += 1
        assert unit in orkview.UNITS, (where, p, unit)
        assert all(str(k).startswith("d:") == (unit == "deal") for k in ks), (where, p, unit, ks[:3])
    return n


def test_у_каждого_списка_одна_единица(built):
    _o_, _d_, _l, v, m = built
    assert _check_units(v["week"]["drill"], "week") > 20
    _check_units(v["quarter"]["drill"], "quarter")
    _check_units(v["how"]["drill"], "how")
    _check_units(m["month"]["drill"], "month")


def test_у_каждого_списка_одна_единица_на_фикстуре():
    r = json.loads(json.dumps(fixture.build_realization(), ensure_ascii=False, default=str))
    for name, ct in (("main", r["contracts"]), ("sep", r["contracts"]["sep"])):
        O = ct["ork"]
        assert _check_units(O["week"]["drill"], name) > 20
        _check_units((O.get("quarter") or {}).get("drill") or {}, name)     # квартал — общий, у основного
        _check_units(O["how"]["drill"], name)
        led = O["week"]["ledger"]
        assert sum(x["now"] if x["unit"] == "order" else x["ordersNow"] for x in led) + O["week"]["unknown"] \
            == O["week"]["live"], name
    for name, om in (r["ork_month"] or {}).items():
        if om and om.get("month"):
            _check_units(om["month"]["drill"], name)
    # строка сделки за ключом «d:» — среди строк сделок «Недели», «Месяца», «Квартала»
    w = r["contracts"]["ork"]["week"]
    dk = {x["k"] for x in w["deals"]}
    for _p, unit, ks in _leaves(w["drill"]):
        if unit == "deal":
            assert set(ks) <= dk, _p
    q = r["contracts"]["ork"]["quarter"]
    assert {k for v in q["drill"].values() for k in v["k"] if k.startswith("d:")} <= {x["k"] for x in q["deals"]}


# ── «Месяц»: S1 по сделке, доставка к сроку в сделках, M2 ────────────────────────────────
def _s1(end):
    return {"wd": 3, "inNorm": False, "burst": False, "ret": False, "neg": False, "skip": False,
            "start": "2026-09-01T10:00:00+03:00", "end": end, "endBy": "стадия"}


def test_первая_приёмка_сделки():
    seg = lambda end: {"segs": {"S1": _s1(end)}}  # noqa: E731
    orders = [{"id": "x1", "deal": "X", "created": dt.date(2026, 9, 1), "_segs": seg("2026-09-03T10:00:00+03:00")},
              {"id": "x2", "deal": "X", "created": dt.date(2026, 9, 10), "_segs": seg("2026-09-12T10:00:00+03:00")},
              {"id": "x3", "deal": "X", "created": dt.date(2026, 9, 1), "_segs": seg("2026-09-05T10:00:00+03:00")},
              {"id": "y1", "deal": "Y", "created": dt.date(2026, 9, 1), "_segs": seg("2026-09-02T10:00:00+03:00")},
              # сделку Z приняли раньше, чем видит история: заказ z0 без отрезка уже дальше шага 1
              {"id": "z0", "deal": "Z", "created": dt.date(2026, 3, 1), "_segs": {}, "reachMax": 9},
              {"id": "z1", "deal": "Z", "created": dt.date(2026, 9, 1), "_segs": seg("2026-09-02T10:00:00+03:00")}]
    assert orkview.first_accept(orders) == {"x1", "y1"}


def test_месяц_s1_одна_на_сделку_и_журнал():
    seg = lambda end: {"segs": {"S1": _s1(end)}}  # noqa: E731
    era = dict(created=dt.date(2026, 9, 1), resp="Анна", orkMgr="Анна", respOrk=True, art={}, _reach={},
               seq=950, title="950. Узел", sup="П")
    orders = [{"id": "m1", "deal": "M", **era, "_segs": seg("2026-09-03T10:00:00+03:00")},
              {"id": "m2", "deal": "M", **era, "_segs": seg("2026-09-08T10:00:00+03:00")},
              {"id": "m3", "deal": "M", **era, "_segs": seg("2026-09-09T10:00:00+03:00")}]
    month, _p, J = orkview._month(orders, [], T, None, {}, deal_ok_all={"M": True})
    sep = next(r for r in month["rows"] if r["m"] == "09.2026")
    assert sep["M1"]["S1"]["n"] == 1, "три отрезка S1 одной сделки — одна приёмка"
    j = {r["order"]: r for r in J["rows"] if r["seg"] == "S1"}
    assert j["m1"]["counted"] and not j["m2"]["counted"] and not j["m3"]["counted"]
    assert j["m2"]["note"] == orkview.J_ASSIGN
    assert month["units"]["M1"] == "segment" and month["units"]["otdDueDeals"] == "deal"


def test_доставка_к_сроку_в_сделках():
    """Сделка G: два заказа, 9/1 доставлен к сроку, 9/2 — позже → сделка позже срока; сделка H:
    один заказ к сроку. Месяц — срок последнего заказа (сентябрь)."""
    T0 = dt.date(2026, 10, 26)
    def done(i, deal, dl, f):
        return _o(i, deal, "Закрытие заказа", live=False, success=True, dl=dl, delFact=f, utd=True)
    orders = [done("g1", "G", dt.date(2026, 9, 5), dt.date(2026, 9, 1)),
              done("g2", "G", dt.date(2026, 9, 20), dt.date(2026, 9, 28)),
              done("h1", "H", dt.date(2026, 9, 10), dt.date(2026, 9, 9))]
    deals = [_d("G", 7, norders=2, closed=True), _d("H", 8, norders=1, closed=True)]
    life = lifecycle.build(orders, deals, T0)
    sep = next(m for m in life["otd"]["months"] if m["m"] == "09.2026")
    dd = sep["dueDeals"]
    assert (dd["n"], dd["ontime"], dd["late"], dd["orders"], dd["unit"]) == (2, 1, 1, 3, "deal")
    assert sorted(dd["keys"]["k"]) == ["d:G", "d:H"] and dd["keys"]["unit"] == "deal"
    assert sep["due"] == 3 and sep["dueOnTime"] == 2, "та же доля в заказах — рядом, прежняя"
    # deals90 — сделки, ключи «d:»; за 3 месяца Σ dueDeals.n ≈ deals90.n
    d90 = life["otd"]["deals90"]
    assert (d90["n"], d90["ontime"], d90["unit"]) == (2, 1, "deal") and sorted(d90["keys"]["k"]) == ["d:G", "d:H"]
    assert sum(m["dueDeals"]["n"] for m in life["otd"]["months"][-3:]) == d90["n"]
    # «Месяц»: доля в сделках рядом с заказами, список сделок собран, у показателя — dk (число на странице)
    month, _p, _j = orkview._month(orders, [], T0, life["otd"], {})
    row = next(r for r in month["rows"] if r["m"] == "09.2026")
    assert (row["otdDueDeals"]["n"], row["otdDueDeals"]["k"], row["otdDueDeals"]["unit"]) == (2, 1, "deal")
    assert row["otdDue"]["unit"] == "order" and month["deals90"]["n"] == 2
    # гарантия: заказы и их сделки
    w = life["warranty"]
    assert w["units"] == {"active": "order", "ending30": "order", "deals": "deal", "deals30": "deal"}
    assert w["deals"] == len({o["deal"] for o in orders if o["id"] in set(w["keys"]["k"])})


def test_m2_файлы_и_заказы():
    a = ork.ARTIFACTS[0]
    era = dict(created=dt.date(2026, 9, 1), resp="Анна", orkMgr="Анна", respOrk=True, _segs={}, seq=1, sup="П")
    orders = [{"id": f"f{i}", "deal": f"F{i}", **era, "art": {a["key"]: i == 0}} for i in range(3)]
    orig = ork.passed_at, ork.art_required
    try:
        ork.passed_at = lambda o, need: dt.date(2026, 9, 10)
        ork.art_required = lambda o, a, r=None: a is ork.ARTIFACTS[0]
        month, _p, _j = orkview._month(orders, [], T, None, {})
    finally:
        ork.passed_at, ork.art_required = orig
    m2 = next(r for r in month["rows"] if r["m"] == "09.2026")["M2"]
    # три пары «заказ × файл» у трёх заказов, файл есть у одного: 1 из 3 файлов, полных карточек 1 из 3
    assert (m2["unit"], m2["ordersN"], m2["ordersFull"]) == ("file", 3, 1)


# ── «Квартал», «Как считается» ───────────────────────────────────────────────────────────
def test_цикл_сделки():
    fr = T - dt.timedelta(days=365)
    ms = [_o("k1", "K", "Закрытие заказа", live=False, success=True, delFact=dt.date(2026, 8, 1),
             dealCat0="2026-05-01T10:00:00+03:00"),
          _o("k2", "K", "Закрытие заказа", live=False, success=True, delFact=dt.date(2026, 9, 1),
             dealCat0="2026-05-01T10:00:00+03:00"),
          _o("l1", "L", "Производство заказа", dealCat0="2026-05-01T10:00:00+03:00"),       # не доставлен
          _o("l2", "L", "Закрытие заказа", live=False, success=True, delFact=dt.date(2026, 9, 1),
             dealCat0="2026-05-01T10:00:00+03:00")]
    c = orkview.deal_cycles(ms, T, fr)
    assert set(c) == {"K"} and c["K"]["cd"] == (dt.date(2026, 9, 1) - dt.date(2026, 5, 1)).days and c["K"]["n"] == 2
    q = orkview._quarter_view(ms, T)
    assert q["Q2"]["cycleDeal"]["unit"] == "deal" and q["Q2"]["cycleDeal"]["n"] == 1
    assert q["Q2"]["cycle"]["unit"] == "order"


def test_как_считается_чей_шаг_и_нарушения(built):
    h = built[3]["how"]
    kinds = {s["no"]: s["kind"] for s in h["steps"]}
    assert {n for n, k in kinds.items() if k == "deal"} == {1, 2, 3}
    # шаг 0 «Подтверждение передачи в ОРК» — заказа (решение владельца 31.10.2026), меряет его задача
    assert {n for n, k in kinds.items() if k == "order"} == {0} | set(range(4, 13)) and kinds[13] == "both"
    st0 = next(s for s in h["steps"] if s["no"] == 0)
    assert st0["measure"] == "task" and st0["stages"] == [] and st0["next"] == "" and st0["esc"] == "КАМ"
    st1 = next(s for s in h["steps"] if s["no"] == 1)
    assert st1["measure"] == "order" and st1["kindName"] == "сделка"
    V = h["viol"]
    assert V["orders"]["unit"] == "order" and V["deals"]["unit"] == "deal"
    assert V["orders"]["n"] <= V["orders"]["cases"] and V["deals"]["n"] <= V["deals"]["cases"]
    assert V["deals"]["payNoContract"] == h["violations"]["payNoContract"]
    units = {m["code"]: m["unit"] for m in h["metrics"]}
    assert units["W1"] == "deal" and units["M1"] == "segment" and units["M2"] == "file"
