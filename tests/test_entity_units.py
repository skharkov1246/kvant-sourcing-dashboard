"""«Реализация»: сделка и заказ поставщика — разные единицы (спецификация «сделка и заказ», T-1;
решения владельца 30.10.2026). Инварианты расчёта очереди (lifecycle.build, PY-1 и PY-2):

  • у каждого пункта есть единица (unit: order | deal) и часть очереди (bucket: exec | deal | hyg)
    по таблицам lifecycle.UNIT и lifecycle.BUCKET; у пункта заказа есть заказ, у пункта сделки — нет;
  • приёмка — одна на сделку: сделка с тремя заказами на шаге 1 — один пункт s1Over с тремя
    заказами строками ords; заказ, добавленный в принятую сделку, — пункт заказа ordAssign;
  • долг клиента и срок оплаты — один пункт сделки: два неоплаченных заказа — один пункт unpaid
    с двумя заказами в ords, unpaidRev — сумма их долей;
  • очередь КАМа согласна: сделок с unpaid у КАМов = dealCounts.unpaid, заказов в пункте — 2;
  • счёт = число разных объектов своей единицы; bucketCounts сходится с пунктами;
  • прежний счёт counts (пункт × код до свёртки) не меняется: та же сумма, что per_order.

Корпус придуман (CLAUDE.md, правило 18): tests/fixture.СделкаИЗаказы поверх ContractsStub.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import contracts as contracts_mod  # noqa: E402
import kamqueue  # noqa: E402
import lifecycle as lc  # noqa: E402
import ork  # noqa: E402
from tests import fixture  # noqa: E402


@pytest.fixture(scope="module")
def ct():
    st = fixture.СделкаИЗаказы()
    return fixture.build_contracts(stub=st, people=st.roster)


@pytest.fixture(scope="module")
def base():
    return fixture.build_contracts()


def _views(data):
    yield "main", data["life"]
    if (data.get("sep") or {}).get("life"):
        yield "sep", data["sep"]["life"]
    yield "all", contracts_mod.life_all(data)


def _by(life, deal, code):
    return [q for q in life["queue"] if str(q["deal"]) == deal and q["code"] == code]


# ── единица и часть у каждого пункта ──────────────────────────────────────────────────────
def test_у_каждого_пункта_единица_и_часть(ct, base):
    for data in (ct, base):
        for nm, life in _views(data):
            assert life["queue"], nm
            for q in life["queue"]:
                assert q["unit"] in ("order", "deal") and q["bucket"] in ("exec", "deal", "hyg"), (nm, q["code"])
                assert (q["unit"], q["bucket"]) == lc.unit_bucket(q["code"], order=q.get("order")), q["code"]
                if q["unit"] == "order":
                    assert q["order"] and q["ordLbl"], (nm, q["code"])
                else:
                    assert not q["order"] and not q["ordLbl"] and q["ordNo"] is None, (nm, q["code"])
                # у пункта сделки шаг — стадии сделки, не ork.ITEM (§4.3)
                if q["unit"] == "deal":
                    row = next((r for r in data["rows"] if str(r["deal"]) == str(q["deal"])), {})
                    assert q["step"] == row.get("dealStep"), (q["code"], q["deal"])


def test_таблицы_единиц_и_частей_по_решениям_владельца():
    """Решения 30.10.2026: dealClose и noPayPlan — порядок в карточках, вне дневного счёта;
    приёмка — сделка, назначение на новый заказ — заказ; долг клиента — сделка."""
    assert (lc.UNIT["s1Over"], lc.BUCKET["s1Over"]) == ("deal", "deal")
    assert (lc.UNIT["ordAssign"], lc.BUCKET["ordAssign"]) == ("order", "exec")
    assert (lc.UNIT["unpaid"], lc.BUCKET["unpaid"]) == ("deal", "deal")
    assert (lc.UNIT["noPayPlan"], lc.BUCKET["noPayPlan"]) == ("deal", "hyg")
    assert (lc.UNIT["dealClose"], lc.BUCKET["dealClose"]) == ("deal", "hyg")
    assert (lc.UNIT["gone"], lc.UNIT["goneOrd"]) == ("deal", "order")
    for c in ("late", "risk", "forecast", "planAfterDl", "prodDev", "noUtd", "s2Over", "s3Over", "s6Over"):
        assert (lc.UNIT[c], lc.BUCKET[c]) == ("order", "exec"), c
    for c in ("noDl", "noPlanDates", "noFact"):
        assert (lc.UNIT[c], lc.BUCKET[c]) == ("order", "hyg"), c
    # каждый код очереди lifecycle — в обеих таблицах
    assert set(lc.EXC) <= set(lc.UNIT) and set(lc.EXC) <= set(lc.BUCKET)
    # договор поставщика с заказом (закрытая часть, f.order) — дело заказа; без заказа — сделки
    assert lc.unit_bucket("contractMissing", "contractMissing:supplier", order="7001") == ("order", "exec")
    assert lc.unit_bucket("contractMissing", "contractMissing:supplier") == ("deal", "hyg")
    assert lc.unit_bucket("contractMissing", "contractMissing:customer") == ("deal", "deal")
    assert lc.unit_bucket("contractRisk", order="7001") == ("order", "exec")
    assert lc.unit_bucket("chatDue", "chatDue:оплата") == ("deal", "deal")
    assert lc.unit_bucket("неизвестный", order="7001") == ("order", "exec")
    assert lc.unit_bucket("неизвестный") == ("deal", "deal")


# ── приёмка: одна на сделку ───────────────────────────────────────────────────────────────
def test_сделка_с_тремя_заказами_на_шаге_1_одна_приёмка(ct):
    life = ct["life"]
    acc = _by(life, "6200", "s1Over")
    assert len(acc) == 1, "приёмка сделки — один пункт, а не по пункту на заказ"
    a = acc[0]
    assert [r["lbl"] for r in a["ords"]] == ["920/1", "920/2", "920/3"]
    assert (a["unit"], a["bucket"], a["role"], a["copyRole"]) == ("deal", "deal", "orkHead", "kam")
    assert a["days"] == max(r["days"] for r in a["ords"]) and a["inWd"] is True
    assert a["what"].startswith("приёмка сделки") and "3 заказа: 920/1, 920/2 и ещё 1" in a["what"]
    assert all(r["step"] == 1 and r["inWd"] for r in a["ords"])
    # прежний счёт — по заказам (3), счёт сделок — 1; заказы пункта в прежних единицах — 3
    per = [q for q in lc.per_order(life["queue"]) if q["code"] == "s1Over" and str(q["deal"]) == "6200"]
    assert sorted(q["ordLbl"] for q in per) == ["920/1", "920/2", "920/3"]
    assert all(q["_of"] is a for q in per)


def test_новый_заказ_принятой_сделки_назначение_менеджера(base):
    """Базовый корпус: 933/3 «Заказ создан» 5 дн., а другие заказы сделки 933 приёмку прошли —
    не «приёмка сделки», а ordAssign у руководителя ОРК, КАМа в копии нет, слова «сделка» нет."""
    life = base["life"]
    assert not _by(life, "6033", "s1Over")
    oa = _by(life, "6033", "ordAssign")
    assert [q["ordLbl"] for q in oa] == ["933/3"]
    q = oa[0]
    assert (q["unit"], q["bucket"], q["role"], q["copyRole"], q["inWd"]) == ("order", "exec", "orkHead", "", True)
    assert "сделк" not in q["what"] and "сделк" not in q["act"]
    assert q["ref"].startswith("с ") and q["days"] >= 1
    # прежний счёт counts — s1Over, как до 30.10.2026; новые счёты — в своих единицах
    assert life["counts"]["s1Over"] == 1 and life["ordCounts"]["ordAssign"] == 1
    assert "s1Over" not in life["dealCounts"]
    assert ork.STEP_CODES[1] == ("s1Over", "ordAssign") and "ordAssign" in ork.TERM_CODES


def test_сделка_принята_по_заказам():
    o = {"live": True, "stage": "Заказ создан"}
    assert not lc._deal_accept([o, dict(o)])
    assert lc._deal_accept([o, {"live": True, "stage": "Заказ направлен"}])
    assert lc._deal_accept([o, {"live": False, "success": True}])
    assert not lc._deal_accept([o, {"live": False, "success": False, "stage": "Заказ направлен"}]), "провал"
    assert lc._deal_accept([{**o, "reachMax": 4}]), "стадия уже выходила дальше шага 1"


# ── долг клиента и срок оплаты: один пункт сделки ─────────────────────────────────────────
def test_сделка_с_двумя_неоплаченными_заказами_один_пункт(ct):
    life = ct["life"]
    up = _by(life, "6201", "unpaid")
    assert len(up) == 1
    u = up[0]
    assert [r["lbl"] for r in u["ords"]] == ["921/1", "921/2"]
    assert (u["unit"], u["bucket"], u["role"], u["copyRole"]) == ("deal", "deal", "kam", "oss")
    assert u["days"] == max(r["days"] for r in u["ords"]) == 30
    assert u["ref"] == next(r["ref"] for r in u["ords"] if r["days"] == 30)
    assert all(r["due"] and r["sup"] for r in u["ords"])
    row = next(r for r in ct["rows"] if r["deal"] == "6201")
    rev = {str(o["id"]): o["revEur"] for o in row["orders"]}
    assert u["unpaidRev"] == round(sum(rev[str(r["id"])] for r in u["ords"])) > 0
    np_ = _by(life, "6201", "noPayPlan")
    assert len(np_) == 1 and [r["lbl"] for r in np_[0]["ords"]] == ["921/1", "921/2"]
    assert (np_[0]["unit"], np_[0]["bucket"], np_[0]["role"]) == ("deal", "hyg", "oss")
    assert np_[0]["who"] == "Дина (ОСС)", "хозяин — ОСС сделки"
    # прежний счёт — заказы (по 2), счёт сделок — 1
    assert life["counts"]["unpaid"] - life["dealCounts"]["unpaid"] >= 1
    assert life["counts"]["noPayPlan"] == 2 and life["dealCounts"]["noPayPlan"] == 1


def test_очередь_кама_согласна(ct):
    """kamqueue: дело одно на сделку и код, заказы строками ords (образец §3.7). Сделок с unpaid у
    КАМов (без noKam) — ровно dealCounts.unpaid; у сделки 921 — два заказа."""
    life = contracts_mod.life_all(ct)
    K = kamqueue.build(None, life, ct["rows"], fixture.CT_TODAY, people=fixture.CT_PEOPLE)
    kq = [x for x in K["queue"] if x["code"] == "unpaid"]
    no_kam = {str(x["deal"]) for x in K["queue"] if x["code"] == "noKam"
              and any(a["code"] == "unpaid" for a in x.get("also") or [])}
    assert len({str(x["deal"]) for x in kq}) + len(no_kam) == life["dealCounts"]["unpaid"]
    x = next(x for x in kq if str(x["deal"]) == "6201")
    assert x["orders"] == 2 and [r["lbl"] for r in x["ords"]] == ["921/1", "921/2"]
    # ordAssign (назначение на новый заказ) — не разговор с клиентом: у КАМа его нет
    assert not [x for x in K["queue"] if x["code"] == "ordAssign"]


# ── счёты в своих единицах ────────────────────────────────────────────────────────────────
def test_счёт_разных_объектов_своей_единицы(ct, base):
    for data in (ct, base):
        for nm, life in _views(data):
            Q = life["queue"]
            for code, n in life["ordCounts"].items():
                assert n == len({q["order"] for q in Q if q["code"] == code and q["unit"] == "order"}), (nm, code)
            for code, n in life["dealCounts"].items():
                assert n == len({str(q["deal"]) for q in Q if q["code"] == code and q["unit"] == "deal"}), (nm, code)
            # у кода — одна единица: ни один код не стоит в обоих счётах
            assert not set(life["ordCounts"]) & set(life["dealCounts"]), nm


def test_bucket_counts_сходятся_с_пунктами(ct, base):
    for data in (ct, base):
        for nm, life in _views(data):
            Q, B = life["queue"], life["bucketCounts"]
            assert set(B) == {"exec", "deal", "hyg"} and set(B["exec"]) == {"orders", "deals", "people"}
            assert set(B["deal"]) == {"deals", "people"} and set(B["hyg"]) == {"orders", "deals"}
            ex = [q for q in Q if q["bucket"] == "exec"]
            assert B["exec"]["orders"] == len({q["order"] for q in ex})
            assert B["exec"]["deals"] == len({str(q["deal"]) for q in ex}) <= B["exec"]["orders"]
            assert B["exec"]["people"] == len({q["who"] for q in ex if q["who"] != "не назначен"})
            dl = [q for q in Q if q["bucket"] == "deal"]
            assert all(q["unit"] == "deal" for q in dl), "в делах по сделкам — только пункты сделки"
            assert B["deal"]["deals"] == len({str(q["deal"]) for q in dl})
            hy = [q for q in Q if q["bucket"] == "hyg"]
            assert B["hyg"]["orders"] == len({q["order"] for q in hy if q["unit"] == "order"})
            assert B["hyg"]["deals"] == len({str(q["deal"]) for q in hy if q["unit"] == "deal"})
            # разные объекты части — не больше суммы счётов её кодов (заказ с двумя кодами — один)
            ex_codes = {q["code"] for q in ex}
            assert B["exec"]["orders"] <= sum(life["ordCounts"].get(c, 0) for c in ex_codes)
            assert B["deal"]["deals"] <= sum(life["dealCounts"].get(c, 0) for c in {q["code"] for q in dl})


def test_прежний_счёт_не_теряет_ни_пункта(ct, base):
    """Сумма прячет потерю: пункты в прежних единицах (per_order: пункт сделки из заказов — по
    заказу) — ровно прежний счёт counts; gone — сделки с любым уволенным, как был."""
    for data in (ct, base):
        for nm, life in _views(data):
            per = lc.per_order(life["queue"])
            got = Counter(lc.LEGACY_CODE.get(q["code"], q["code"]) for q in per if q["code"] not in ("gone", "goneOrd"))
            want = Counter({k: v for k, v in life["counts"].items() if k != "gone"})
            assert got == want, nm
            gone_deals = {str(q["deal"]) for q in life["queue"] if q["code"] in ("gone", "goneOrd")}
            assert life["counts"].get("gone", 0) == len(gone_deals), nm


def test_одна_подпись_одно_значение(ct, base):
    """«Клиент не оплатил в срок»: сделок на плитке недели — dealCounts.unpaid; заказы за ней —
    заказы строк ords. «Без плановых дат» — заказы: ordCounts.noPlanDates."""
    for data in (ct, base):
        W = data["ork"]["week"]
        U = W["tiles"]["unpaid"]
        life = data["life"]
        assert U["deals"] == life["dealCounts"].get("unpaid", 0)
        ords = {str(r["id"]) for q in life["queue"] if q["code"] == "unpaid" for r in q["ords"]}
        assert U["orders"] == len(ords) and set(W["drill"]["unpaid"]["k"]) == ords
        assert life["ordCounts"]["noPlanDates"] == len({q["order"] for q in life["queue"] if q["code"] == "noPlanDates"})


# ── уволенный: сделка и заказ раздельно ───────────────────────────────────────────────────
def test_уволенный_ответственный_заказа_пункт_заказа(ct):
    life = ct["life"]
    go = _by(life, "6205", "goneOrd")
    assert [q["ordLbl"] for q in go] == ["925/1"]
    assert (go[0]["unit"], go[0]["bucket"], go[0]["role"]) == ("order", "hyg", "orkHead")
    assert go[0]["what"] == "заказ ведёт уволенный: ответственный заказа"
    assert not _by(life, "6205", "gone"), "люди сделки на месте — пункта сделки нет"


# ── оплата без договора, закрыть сделку, клиентское действие ──────────────────────────────
def test_оплата_без_договора_называет_заказы(base):
    pn = [q for q in base["life"]["queue"] if q["code"] == "payNoContract"]
    assert pn
    for q in pn:
        assert q["ords"] and all(r["supPaidPct"] > 0 and r["lbl"] for r in q["ords"])
        assert (q["unit"], q["bucket"]) == ("deal", "deal")


def test_закрыть_сделку_оговорка_отдельным_полем(base):
    dc = [q for q in base["life"]["queue"] if q["code"] == "dealClose"]
    assert dc
    for q in dc:
        assert q["what"] == lc.EXC["dealClose"][1], "оговорка не в what"
        assert (q["unit"], q["bucket"]) == ("deal", "hyg")
        assert bool(q.get("note")) == bool(q.get("ords"))


def test_клиентское_действие_в_шапке_сделки(base):
    """E7: у пункта заказа — действие исполнения; «согласовать с клиентом» — один раз, actClient."""
    for q in base["life"]["queue"]:
        if q["code"] in lc.ACT_CLIENT:
            assert q["actClient"] == lc.ACT_CLIENT[q["code"]] and "клиент" in q["actClient"]
        else:
            assert "actClient" not in q
    for c in ("late", "forecast", "planAfterDl"):
        assert "клиент" not in lc.EXC[c][2], c
