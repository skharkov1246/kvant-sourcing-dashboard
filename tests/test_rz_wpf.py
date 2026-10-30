"""R4 · WP-F: «Месяц», «Квартал», «Как считается» — данные.

Д-2: «доставлено к сроку» со знаменателем «срок наступил» (lifecycle.otd: due, dueOnTime,
dueLateDelivered, dueOpenLate, keys); прежние n/ontime не меняются. Д-10: ключи заказов за числами
«Месяца» (не доставлено, не считаем, столбцы по месяцам, не из ОРК), «Квартала» (Q2 и хвост P80, «в
наличии», «пачкой», «план = факт», проверка другим способом, гарантия) и «Как считается»
(нарушения порядка, КАМ ≠ КАМ, нет файла шага). Инвариант каждого списка — длина = число у кнопки;
скрытая доля ключей не получает. Корпус придуман (CLAUDE.md, правило 18).
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

import lifecycle as lc  # noqa: E402
import orkview  # noqa: E402

СЕГОДНЯ = dt.date(2026, 10, 19)
ДАТЫ = {"prodStart": dt.date(2026, 5, 1), "prodEnd": dt.date(2026, 7, 1), "delBudget": dt.date(2026, 9, 1)}


def д(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def заказ(**kw) -> dict:
    o = {"id": "1", "deal": "100", "seq": 900, "title": "900/1. Узел", "sup": "Поставщик А", "oss": "Ольга (ОСС)",
         "live": True, "success": False, **ДАТЫ}
    o.update(kw)
    return o


СДЕЛКИ = [
    {"deal": "100", "seq": 900, "customer": "Заказчик 1", "closed": False},
    {"deal": "101", "seq": 901, "customer": "Заказчик 2", "closed": False},
    {"deal": "102", "seq": 902, "customer": "Заказчик 3", "closed": True},       # закрыта (выиграна)
    {"deal": "103", "seq": 903, "customer": "Заказчик 4", "closed": False, "lost": True},
]


def _корпус() -> list[dict]:
    return [
        # сентябрь: к сроку, позже срока, не доставлен (живой, сделка открыта)
        заказ(id="11", dl=д("2026-09-10"), delFact=д("2026-09-08"), live=False, success=True),
        заказ(id="12", dl=д("2026-09-12"), delFact=д("2026-09-20"), live=False, success=True),
        заказ(id="13", dl=д("2026-09-15")),
        # сентябрь: доставки нет, есть подписанный УПД — доставлен по УПД (как у пункта late)
        заказ(id="14", dl=д("2026-09-20"), utdDate=д("2026-09-18"), utd=True),
        # сентябрь: сделка закрыта, заказ не доставлен — не в знаменателе (dueSkip)
        заказ(id="15", deal="102", dl=д("2026-09-22")),
        # сентябрь: проигранная сделка — вне вселенной вкладки
        заказ(id="16", deal="103", dl=д("2026-09-23")),
        # октябрь: срок прошёл — не доставлен; срок сегодня — ещё не наступил
        заказ(id="21", deal="101", seq=901, title="901/1. Узел", dl=д("2026-10-05")),
        заказ(id="22", deal="101", seq=901, title="901/2. Узел", dl=СЕГОДНЯ),
        # октябрь: факт доставки в будущем (вбит заранее) — не доставлен
        заказ(id="23", deal="101", seq=901, title="901/3. Узел", dl=д("2026-10-10"), delFact=д("2026-10-25")),
        # срок в будущем — не наступил
        заказ(id="24", deal="101", seq=901, title="901/4. Узел", dl=д("2026-11-20")),
    ]


@pytest.fixture(scope="module")
def life():
    return lc.build(_корпус(), СДЕЛКИ, СЕГОДНЯ)


def _мес(life, m):
    return next(x for x in life["otd"]["months"] if x["m"] == m)


def test_д2_знаменатель_срок_наступил(life):
    сен, окт = _мес(life, "09.2026"), _мес(life, "10.2026")
    assert (сен["due"], сен["dueOnTime"], сен["dueLateDelivered"], сен["dueOpenLate"], сен["dueSkip"]) == (4, 2, 1, 1, 1)
    assert сен["duePct"] == 50
    # срок сегодня не наступил; факт доставки в будущем — не доставлен
    assert (окт["due"], окт["dueOnTime"], окт["dueLateDelivered"], окт["dueOpenLate"]) == (2, 0, 1, 1) and окт["duePct"] == 0
    assert "24" not in {k for m in life["otd"]["months"] for k in m["keys"]["k"]}, "срок в будущем — не наступил"


def test_д2_инвариант_сложения_и_ключи(life):
    for m in life["otd"]["months"]:
        assert m["due"] == m["dueOnTime"] + m["dueLateDelivered"] + m["dueOpenLate"], m["m"]
        assert len(m["keys"]["k"]) == len(m["keys"]["t"]) == m["due"], m["m"]
        классы = [t.split(" · ")[0] for t in m["keys"]["t"]]
        assert классы.count("к сроку") == m["dueOnTime"]
        assert sum(1 for c in классы if c.startswith("позже срока")) == m["dueLateDelivered"]
        assert классы.count("не доставлен") == m["dueOpenLate"]
    сен = _мес(life, "09.2026")
    assert set(сен["keys"]["k"]) == {"11", "12", "13", "14"}, "закрытая и проигранная сделки — не в знаменателе"


def test_д2_не_доставлено_после_срока_равно_пунктам_late(life):
    """«19 сорванных сроков» и доля «к сроку» больше не спорят: не доставленные после срока в
    знаменателе — ровно заказы пунктов late очереди (сроки в окне 12 месяцев)."""
    late = {q["order"] for q in life["queue"] if q["code"] == "late"}
    open_ = {k for m in life["otd"]["months"] for k, t in zip(m["keys"]["k"], m["keys"]["t"])
             if t.startswith("не доставлен")}
    assert late == open_ == {"13", "21"}


def test_д2_прежние_n_ontime_не_тронуты(life):
    """Старая доля — по месяцу ДОСТАВКИ среди доставленных по факту (delFact ≤ сегодня): та же, что до R4."""
    сен = _мес(life, "09.2026")
    assert (сен["n"], сен["ontime"], сен["pct"]) == (2, 1, 50)
    assert life["otd"]["last90"] == {"n": 2, "ontime": 1, "pct": 50}


def test_д10_гарантия_и_проверка_поставщика_ключами():
    заказы = [заказ(id=str(i), deal="100", sup="Поставщик Б", shipFact=д("2026-06-0%d" % i), prodEnd=д("2026-06-03"),
                    delFact=д("2026-07-01"), utd=True, clientPaid=True, live=False, success=True) for i in range(1, 5)]
    L = lc.build(заказы, СДЕЛКИ, СЕГОДНЯ)
    w = L["warranty"]
    assert len(w["keys"]["k"]) == w["active"] == 4 and len(w["keys30"]["k"]) == w["ending30"]
    assert all(t.startswith("гарантия до 01.07.2027 (допущение 12 мес.)") for t in w["keys"]["t"])
    s = L["suppliers"][0]
    assert s["sup"] == "Поставщик Б" and len(s["keys"]["k"]) == s["n"] == 4
    assert [t.split(" · ")[0] for t in s["keys"]["t"]].count("вовремя") == s["ontime"] == 3


def test_д10_не_считаем_только_у_показанной_ячейки():
    seg = lambda **k: {"end": "2026-09-10", "wd": 1, "inNorm": True, **k}  # noqa: E731
    o = [{"id": str(i)} for i in range(4)]
    pairs = [(o[0], seg(burst=True), "S1"), (o[1], seg(ret=True), "S1"), (o[2], seg(skip=True), "S1"), (o[3], seg(), "S1")]
    d, used = {}, {}
    orkview._excl_keys({"state": "prelim", "excluded": 2, "skipped": 1}, pairs, d, "1.S1", "Приёмка", used)
    assert d["1.S1.excl"]["k"] == ["0", "1"] and d["1.S1.skip"]["k"] == ["2"]
    assert d["1.S1.excl"]["t"] == ["стадии прокликали разом", "возврат на шаг раньше"]
    d2: dict = {}
    orkview._excl_keys({"state": "hidden"}, pairs, d2, "1.mgr", "Шаги", {})
    assert d2 == {}, "скрытая ячейка — без списка: иначе счёт раскрыл бы скрытое"
    d3: dict = {}
    orkview._excl_keys({"state": "show", "excluded": 5, "skipped": 1}, pairs, d3, "0.S1", "Приёмка", {})
    assert "0.S1.excl" not in d3 and "0.S1.skip" in d3, "длина списка ≠ числу — ключа нет"


@pytest.fixture(scope="module")
def built():
    from tests import fixture
    return json.loads(json.dumps(fixture.build_realization(), ensure_ascii=False, default=str))


def _views(built):
    ct, om = built["contracts"], built["ork_month"]
    yield "main", ct, om["main"]
    if ct.get("sep") and om.get("sep"):
        yield "sep", ct["sep"], om["sep"]


def test_месяц_доля_к_сроку_из_life_и_списки(built):
    for view, X, M in _views(built):
        mo = M["month"]
        assert "_otd" not in mo and all("_due" not in r for r in mo["rows"]), view
        OM = {m["m"]: m for m in X["life"]["otd"]["months"]}
        D = mo["drill"]
        for i, r in enumerate(mo["rows"]):
            st, src = r["otdDue"], OM.get(r["m"]) or {}
            assert st["n"] == src.get("due", 0) and st.get("open", 0) == src.get("dueOpenLate", 0), (view, r["m"])
            if st["state"] in ("show", "prelim"):
                assert st["value"] == src["duePct"] and st["k"] == src["dueOnTime"]
                assert st["dk"] == f"m:{i}.due" and len(D[f"{i}.due"]["k"]) == st["n"]
                if st["open"]:
                    assert len(D[f"{i}.dueOpen"]["k"]) == st["open"]
                if st["late"]:
                    assert len(D[f"{i}.dueLate"]["k"]) == st["late"]
            # прежняя доля по доставленным — в данных та же, ключей у неё больше нет
            assert r["otd"]["n"] == (src.get("n") or 0) and "dk" not in r["otd"]
        for m, src in OM.items():
            if src["due"]:
                assert len(D[f"y.{m}"]["k"]) == src["due"], (view, m)
            else:
                assert f"y.{m}" not in D
        # строки списков «Месяца» — для каждого ключа, с датами срока и доставки
        rows = {x["k"] for x in mo["orders"]}
        ks = {k for v in D.values() for k in v["k"]}
        assert ks <= rows | {str(o["id"]) for d in built["contracts"]["rows"] for o in d.get("orders") or []}, view


def test_квартал_сроки_звеньев_и_хвост_p80(built):
    Q = built["contracts"]["ork"]["quarter"]
    D = Q["drill"]
    for c in ("S5", "S7", "S8", "cycle"):
        st = Q["Q2"][c]
        if st["state"] not in ("show", "prelim"):
            assert f"Q2.{c}" not in D
            continue
        lst = D[f"Q2.{c}"]
        assert st["dk"] == f"q:Q2.{c}" and len(lst["k"]) == st["n"]
        dn = [int(t.split()[0]) for t in lst["t"]]
        assert dn == sorted(dn, reverse=True), "длинные сверху"
        tail = D.get(f"Q2.{c}.tail")
        if tail:
            assert set(tail["k"]) <= set(lst["k"]) and all(int(t.split()[0]) >= st["p80"] for t in tail["t"])
    for i, q in enumerate(Q["quarters"]):
        s = q["Q1"]
        for nm, n in (("stock", s.get("stock")), ("burst", s.get("burstN"))):
            if f"{i}.{nm}" in D:
                assert s["state"] in ("show", "prelim") and len(D[f"{i}.{nm}"]["k"]) == n


def test_как_считается_нарушения_и_файлы(built):
    for view, X, _M in _views(built):
        H = X["ork"]["how"]
        D, V = H["drill"], H["violations"]
        for k in ("returns", "bursts", "negative", "payNoContract", "dealBehindOrders", "behindFacts", "unknownStage"):
            if V[k]:
                assert len(D[k]["k"]) == V[k], (view, k)
            else:
                assert k not in D
        for a in H["artifacts"]:
            miss = (a["req"] or 0) - (a["have"] or 0)
            if miss:
                assert len(D["art." + a["key"]]["k"]) == miss, (view, a["key"])
        ka = H["kamAgree"]
        assert len((D.get("kamMiss") or {}).get("k") or []) == ka["n"] - ka["same"]


def test_список_не_раскрывает_скрытое(built):
    """Скрытая ячейка «Месяца» (ворота людей) — без dk и без списков «не считаем»."""
    for view, _X, M in _views(built):
        D = M["month"]["drill"]
        for i, r in enumerate(M["month"]["rows"]):
            for c, st in (r["M1"] or {}).items():
                if st.get("state") == "hidden":
                    assert "dk" not in st and f"{i}.{c}" not in D and f"{i}.{c}.excl" not in D and f"{i}.{c}.skip" not in D
