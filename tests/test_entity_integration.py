"""Сделка и заказ поставщика — сведение пакетов P1–P3 и T1–T4 (спецификация «сделка и заказ
поставщика» 30.10.2026, §4.3; решения владельца 30.10.2026).

Инварианты сведения, каждый — двумя способами:
- один счёт по частям (bucketCounts) у life, life_all, head.sep и закрытой части (dealctx) —
  lifecycle.queue_counts, а не два определения;
- приёмка сделки и назначение менеджера ОРК на новый заказ — одним правилом у очереди и «Недели»:
  W1 — сделки пунктов s1Over, W1o — заказы пунктов ordAssign, сделка не бывает в обоих;
- «Клиент не оплатил в срок» — одно значение на «Неделе» и «Сделках и деньгах» (сделки, деньги —
  доля заказов);
- у каждого собранного списка «Месяца» и «Квартала» — dk (dkNext не осталось);
- строка заказа «Недели» на шаге 1 принятой сделки помечена asg — так её читает путь заказа.

Корпус придуман (CLAUDE.md, правило 18): сделки ContractsStub и свод fixture.realctx_raw.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import contracts  # noqa: E402
import dealctx  # noqa: E402
import lifecycle  # noqa: E402
import orkview  # noqa: E402
from tests import fixture  # noqa: E402


@pytest.fixture(scope="module")
def R():
    return fixture.build_realization()


def _hand_buckets(queue: list[dict]) -> dict:
    """Тот же счёт по частям, посчитанный в лоб по полям пунктов (второй способ)."""
    out = {"exec": {"orders": set(), "deals": set(), "people": set()},
           "deal": {"deals": set(), "people": set()}, "hyg": {"orders": set(), "deals": set()}}
    for q in queue:
        b = out[q["bucket"]]
        if q["unit"] == "order":
            if "orders" in b:
                b["orders"].add(str(q["order"]))
            if q["bucket"] == "exec":
                b["deals"].add(str(q["deal"]))
        else:
            b["deals"].add(str(q["deal"]))
        who = str(q.get("whoId") or "") or (q.get("who") if q.get("who") != "не назначен" else "")
        if "people" in b and who:
            b["people"].add(who)
    return {k: {f: len(v) for f, v in b.items()} for k, b in out.items()}


def test_один_счёт_по_частям_у_всех_видов(R):
    ct = R["contracts"]
    life, sep = ct["life"], (ct.get("sep") or {}).get("life") or {}
    for nm, L in (("main", life), ("sep", sep)):
        if not L:
            continue
        q = L["queue"]
        assert L["bucketCounts"] == lifecycle.queue_counts(q)["bucketCounts"] == _hand_buckets(q), nm
        assert contracts.life_bucket_counts(L) == L["bucketCounts"], nm
    allq = contracts.life_all(ct)
    assert allq["bucketCounts"] == _hand_buckets(allq["queue"])
    # head.sep — тот же счёт вида направления
    if sep:
        assert ct["head"]["sep"]["bucketCounts"] == sep["bucketCounts"]
    # закрытая часть — та же функция (dealctx.bucket_counts зовёт lifecycle.queue_counts)
    dq = R["dealctx"]["queue"]
    main_q = [x for x in dq if not x.get("seg")]
    assert R["dealctx"]["bucketCounts"] == lifecycle.queue_counts(main_q)["bucketCounts"] == _hand_buckets(main_q)
    assert dealctx.unit_counts(main_q) == (R["dealctx"]["ordCounts"], R["dealctx"]["dealCounts"])
    # часть пункта закрытой части — по таблице lifecycle (одна таблица кодов на «Реализацию»)
    for x in dq:
        u, b = lifecycle.unit_bucket(x["code"], x.get("itemKey"), x.get("order") or None)
        assert (x["unit"], x["bucket"]) == (u, b), (x["code"], x.get("itemKey"))


def test_приёмка_сделки_и_новый_заказ_одним_правилом(R):
    ct = R["contracts"]
    w = ct["ork"]["week"]
    q = ct["life"]["queue"]
    s1 = [x for x in q if x["code"] == "s1Over"]
    oa = [x for x in q if x["code"] == "ordAssign"]
    # приёмка — пункт СДЕЛКИ (заказы — ords), новый заказ принятой сделки — пункт ЗАКАЗА
    assert all(x["unit"] == "deal" and x["bucket"] == "deal" for x in s1)
    assert oa and all(x["unit"] == "order" and x["bucket"] == "exec" for x in oa)
    assert not {x["deal"] for x in s1} & {x["deal"] for x in oa}, "сделка и на приёмке, и принята"
    D = w["drill"]
    W1 = set(D["W1"]["k"])
    assert all(k.startswith("d:") for k in W1) and D["W1"]["unit"] == "deal"
    assert {f"d:{x['deal']}" for x in s1 if x.get("w1")} <= W1
    assert set(D["W1o"]["k"]) == {str(x["order"]) for x in oa if x.get("w1o")}
    assert w["tiles"]["W1o"]["value"] == len(D["W1o"]["k"]) and D["W1o"]["unit"] == "order"
    # ни приёмка, ни назначение — не «шаги ОРК дольше нормы» (W2 — шаги 4, 5, 8)
    PQ = lifecycle.per_order(q)
    assert not [x for x in PQ if x["code"] in ("s1Over", "ordAssign") and x.get("w2")]
    # строка заказа «Недели»: asg — шаг 1 принятой сделки; заказы ordAssign — среди них
    rows = {r["k"]: r for r in orkview.unpack(w["orders"], w["strs"])}
    asg = {k for k, r in rows.items() if r.get("asg")}
    assert {str(x["order"]) for x in oa} <= asg
    assert all(rows[k]["step"] == 1 for k in asg)
    assert not asg & {str(o["id"]) for x in s1 for o in x.get("ords") or []}


def test_клиент_не_оплатил_одно_значение_на_подвкладках(R):
    ct = R["contracts"]
    U = ct["ork"]["week"]["tiles"]["unpaid"]
    D = ct["ork"]["week"]["drill"]
    q = ct["life"]["queue"]
    # «Неделя»: сделки пунктов unpaid (после свёртки — пункт сделки), список — сделки
    deals = {str(x["deal"]) for x in q if x["code"] == "unpaid"}
    assert U["deals"] == len(deals) == len(D["unpaidDeals"]["k"])
    assert set(D["unpaidDeals"]["k"]) == {f"d:{d}" for d in deals}
    # «Сделки и деньги»: открытые строки с пунктом unpaid — те же сделки (плитка — то же число)
    open_rows = [r for r in ct["rows"] if not r.get("closed") and str(r["deal"]) in deals]
    assert len(open_rows) == U["deals"]
    # заказы и деньги — доля неоплаченных заказов, одна и та же сумма двумя путями
    ords = {o for x in q if x["code"] == "unpaid" for o in lifecycle.item_orders(x)}
    assert U["orders"] == len(ords) == sum(r["unpaidN"] for r in ct["rows"])
    assert round(U["rev"]) == round(sum(r["unpaidRev"] for r in ct["rows"]))


def _walk(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield path + "." + k, k, v
            yield from _walk(v, path + "." + k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _walk(v, f"{path}[{i}]")


def test_у_каждого_списка_месяца_и_квартала_dk(R):
    blob = {"contracts": R["contracts"], "orkMonth": R["ork_month"]}
    assert "dkNext" not in json.dumps(blob, ensure_ascii=False, default=str)
    M = R["ork_month"]["main"]["month"]
    for i, r in enumerate(M["rows"]):
        dd = r.get("otdDueDeals") or {}
        if dd.get("state") in ("show", "prelim"):
            assert dd["dk"] == f"m:{i}.dueDeals" and len(M["drill"][f"{i}.dueDeals"]["k"]) == dd["n"]
            assert M["drill"][f"{i}.dueDeals"]["unit"] == "deal"
    if (M.get("deals90") or {}).get("n"):
        assert M["deals90"]["dk"] == "m:deals90" and len(M["drill"]["deals90"]["k"]) == M["deals90"]["n"]
    Q = R["contracts"]["ork"]["quarter"]
    cd = Q["Q2"]["cycleDeal"]
    if cd.get("state") in ("show", "prelim"):
        assert cd["dk"] == "q:Q2.cycleDeal" and len(Q["drill"]["Q2.cycleDeal"]["k"]) == cd["n"]
