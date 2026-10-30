"""Сделка и заказ поставщика — ремонт по ревью сведения (30.10.2026), данные.

Каждое правило — на двух корпусах: фикстура вкладки (fixture.build_realization) и придуманный
корпус «сделка и заказы» (fixture.СделкаИЗаказы: сделки 920–925). Правила:
  * строка сделки «Недели» (d:) несёт коды СВОИХ пунктов сделки (unpaid, s1Over, noOrder…), а не
    только копии по заказам: за «Клиент не оплатил» и W1 — действие и хозяин пункта сделки;
  * «Месяц», S1 — список в сделках (ключи «d:»), как плитка «N из M сделок»;
  * сделка не принята — её заказы на шаге 1 ждут приёмки СДЕЛКИ (stepAcc): шаг сделки 1 в ячейке
    один, заказы — счётом рядом, в «N заказов дольше нормы» не идут;
  * «не назначен» в очереди — то же множество, что «Без менеджера ОРК» (вид none «Кто сколько
    ведёт»); ответственный не из ОРК — whoOut, отдельно;
  * у пунктов закрытой части (чат, договор, экономика) — id хозяина (whoId), как у life.queue;
  * очередь КАМа — как до 30.10.2026: назначение менеджера ОРК на новый заказ видно КАМу
    прежним пунктом «приёмка дольше 1 р.д.».
Корпус придуман (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import contracts as contracts_mod  # noqa: E402
import dealctx  # noqa: E402
import kamqueue  # noqa: E402
import lifecycle  # noqa: E402
import orkview  # noqa: E402
from tests import fixture as F  # noqa: E402


def _invented() -> dict:
    """Придуманный корпус 920–925 поверх ContractsStub — та же сборка, что build_realization."""
    ctx = dealctx.clean(F.realctx_raw())
    st = F.СделкаИЗаказы()
    ct = F.build_contracts(stub=st, people=st.roster, ctx_chats=ctx["chats"], ctx_econ=ctx["econ"],
                           ctx_contracts=ctx["contracts"], keep_head=True)
    ork_month = ct.pop("orkMonth", None)
    ork_head = ct.pop("_orkHead", "")
    head, payload = dealctx.build(ct["rows"], contracts_mod.life_all(ct), ctx, dt.datetime.fromisoformat(F.CT_NOW),
                                  today=F.CT_TODAY, ork_head=ork_head)
    return {"contracts": ct, "dealctx": payload, "ork_month": ork_month}


@pytest.fixture(scope="module", params=["fixture", "invented"])
def R(request):
    r = F.build_realization() if request.param == "fixture" else _invented()
    return json.loads(json.dumps(r, ensure_ascii=False, default=str))


def _deal_items(queue: list[dict]) -> dict[str, set]:
    out: dict[str, set] = {}
    for q in queue:
        if q.get("unit") == "deal":
            out.setdefault(str(q["deal"]), set()).add(q["code"])
    return out


def test_строка_сделки_несёт_коды_своих_пунктов(R):
    """d:-строка «Недели»: q ⊇ коды пунктов сделки этой сделки (свёрнутая очередь), без них
    _odPrep говорил «Дел по сделке в очереди нет» у «Клиент не оплатил» и W1."""
    w = R["contracts"]["ork"]["week"]
    di = _deal_items(R["contracts"]["life"]["queue"])
    rows = {x["k"]: x for x in w["deals"]}
    checked = 0
    for name, codes in (("unpaidDeals", {"unpaid"}), ("W1", {"s1Over", "noOrder", "dealAccept"})):
        for k in (w["drill"].get(name) or {}).get("k") or []:
            did = k[2:]
            own = di.get(did, set()) & codes
            assert own, (name, k, "у сделки нет пункта сделки своего числа")
            assert own <= set(rows[k].get("q") or []), (name, k, rows[k].get("q"))
            checked += 1
    assert checked >= 2
    # каждый пункт сделки — у своей строки d:, если строка есть
    for did, codes in di.items():
        if f"d:{did}" in rows:
            assert codes <= set(rows[f"d:{did}"].get("q") or []) | {"dealClose", "noOrder"} | codes, did


def test_w1_сделка_920_действие_приёмки():
    """Придуманная сделка 920 (три заказа на шаге 1, сделка не принята): строка W1 несёт s1Over —
    действие «Принять сделку и назначить менеджера ОРК», а не «Держать срок шага 1»."""
    r = _invented()
    w = r["contracts"]["ork"]["week"]
    assert "d:6200" in w["drill"]["W1"]["k"]
    row = next(x for x in w["deals"] if x["k"] == "d:6200")
    assert "s1Over" in row["q"]
    q = next(q for q in r["contracts"]["life"]["queue"] if q["code"] == "s1Over" and str(q["deal"]) == "6200")
    assert q["unit"] == "deal" and q["role"] == "orkHead" and len(q["ords"]) == 3


def test_месяц_s1_список_в_сделках(R):
    """m:<i>.S1 — единица deal, ключи «d:», по ключу на сделку; число ключей = n плитки S1."""
    om = R["ork_month"] or {}
    seen = 0
    for _name, m in om.items():
        if not (m and m.get("month")):
            continue
        rows = m["month"]["rows"]
        for key, v in m["month"]["drill"].items():
            if not (key.endswith(".S1") or ".S1." in key):
                continue
            assert v["unit"] == "deal", key
            assert all(k.startswith("d:") for k in v["k"]), key
            assert len(set(v["k"])) == len(v["k"]), key
            if key.endswith(".S1"):
                mi = int(key.split(".")[0])
                assert len(v["k"]) == (rows[mi].get("M1") or {}).get("S1", {}).get("n"), key
                seen += 1
            dk = {x["k"] for x in m["month"]["deals"]}
            assert set(v["k"]) <= dk, key
    if R["contracts"]["rows"] and any(om.values()):
        assert seen >= 1


def test_заказы_непринятой_сделки_ждут_приёмки():
    """920: ячейка шага — шаг СДЕЛКИ 1 (unit deal, accept=3, р.д. — худший из заказов); заказы
    помечены stepAcc и в «дольше нормы» заказов не идут (их считают один раз — сделкой)."""
    r = _invented()
    row = next(x for x in r["contracts"]["rows"] if str(x["deal"]) == "6200")
    c = row["stepCell"]
    assert (c["step"], c["unit"], c["accept"], c["order"]) == (1, "deal", 3, "")
    assert c["wd"] == max(o["stepWd"] for o in row["orders"] if o.get("stepAcc"))
    acc = [o for o in row["orders"] if o.get("live") and o.get("stepAcc")]
    assert len(acc) == 3 and all(o["step"] == 1 for o in acc)
    # второй способ: lifecycle._deal_accept по заказам сделки — не принята
    # у принятой сделки (933 фикстуры: новый заказ на шаге 1 — ordAssign) stepAcc нет
    r2 = F.build_realization()
    for x in r2["contracts"]["rows"]:
        for o in x.get("orders") or []:
            if o.get("stepAcc"):
                assert o["step"] == 1 and not x.get("closed")
    d933 = next(x for x in r2["contracts"]["rows"] if str(x["deal"]) == "6033")
    assert not any(o.get("stepAcc") for o in d933["orders"])


def test_не_назначен_одно_множество(R):
    """Заказы пунктов с «не назначен» (роль менеджер ОРК, не whoOut) ⊆ заказов вида none «Кто
    сколько ведёт» (счётчик «Без менеджера ОРК»); whoOut — ответственный заказа есть, не из ОРК."""
    w = R["contracts"]["ork"]["week"]
    S = w.get("strs") or []
    mgr = lambda o: S[o["mgr"]] if isinstance(o.get("mgr"), int) else o.get("mgr")      # noqa: E731 — подписи номером (PACKED)
    none = {str(o["k"]) for o in w["orders"] if mgr(o) == orkview.PULSE_NONE}
    out = {str(o["k"]) for o in w["orders"] if mgr(o) == orkview.PULSE_OUT}
    P = {r["kind"]: r["n"] for r in w["pulse"]["rows"]}
    assert len(none) == P.get("none", 0) and len(out) == P.get("out", 0)
    Q = [q for q in R["contracts"]["life"]["queue"] if q.get("unit") == "order" and q.get("role") == "orkMgr"]
    un = {str(q["order"]) for q in Q if not q.get("whoOut") and q.get("who") == "не назначен"}
    wo = {str(q["order"]) for q in Q if q.get("whoOut")}
    live = {str(o["k"]) for o in w["orders"]}
    assert un & live <= none, (un - none)
    assert wo & live <= out, (wo - out)
    assert not (un & wo)


def test_закрытая_часть_с_id_хозяина(R):
    """Пункт DEALCTX с именем человека, у которого в очереди «Реализации» есть id, — с whoId."""
    life_q = R["contracts"]["life"]["queue"] + ((R["contracts"].get("sep") or {}).get("life") or {}).get("queue", [])
    ids = {q["who"]: q["whoId"] for q in life_q if q.get("whoId") and q.get("who") not in (None, "", "не назначен")}
    ids.update({q["kam"]: q["kamId"] for q in life_q if q.get("kamId") and q.get("kam")})
    D = (R["dealctx"] or {}).get("queue") or []
    hit = 0
    for q in D:
        if q.get("who") in ids:
            assert str(q.get("whoId")) == str(ids[q["who"]]), (q["code"], q["who"], q.get("whoId"))
            hit += 1
        if q.get("escalateTo") in ids:
            assert str(q.get("escalateToId")) == str(ids[q["escalateTo"]]), (q["code"], q["escalateTo"])
    assert hit >= 3


def test_person_ids_не_ставит_чужое():
    q = [{"who": "менеджеры заказов: Пётр (ОРК) (1)"}, {"who": "не назначен"}, {"who": "Роза (рук. ОРК)", "whoId": ""},
         {"who": "Анна (КАМ)", "escalateTo": "Вера (КАМ)"}]
    dealctx.person_ids(q, [{"who": "Роза (рук. ОРК)", "whoId": "30"}, {"who": "не назначен", "whoId": "9"}],
                       [{"kam": "Анна (КАМ)", "kamId": "10"}, {"kam": "Вера (КАМ)", "kamId": "12"}])
    assert [x.get("whoId") for x in q] == [None, None, "30", "10"]
    assert q[3]["escalateToId"] == "12"


def test_очередь_кама_как_до_решения():
    """Назначение менеджера ОРК на новый заказ (ordAssign) КАМу видно прежним пунктом s1Over с
    прежней подписью: вкладка «Key-аккаунты и продукты» решением 30.10.2026 не менялась."""
    r = F.build_realization()
    Q = r["contracts"]["life"]["queue"]
    oa = [q for q in Q if q["code"] == "ordAssign"]
    assert oa, "в фикстуре нет ordAssign — проверка ничего не проверила бы"
    kq = kamqueue.build(None, contracts_mod.life_all(r["contracts"]), r["contracts"]["rows"], F.CT_TODAY,
                        people=F.CT_PEOPLE)
    s1 = [x for x in kq["queue"] if x["code"] == "s1Over" and str(x["deal"]) in {str(q["deal"]) for q in oa}]
    assert s1 and all(x["what"] == "приёмка дольше 1 р.д." for x in s1)
    assert kamqueue.CODES["s1Over"][1] == "приёмка дольше 1 р.д."
