"""R4 · WP-E «Сделки и деньги» и карточка сделки (У4д): данные Д-11.

Корпус придуман (CLAUDE.md, правило 18): сделки и заказы — ContractsStub, заказы без сделки —
добавлены тестом с вымышленными номерами. Проверяется дважды и разными способами (26.09.2026):
счёт сборки против строк списка и против исходных заказов стаба.

Инварианты Д-11:
- у строки сделки `kamId` — id из поля «КАМ» (запасное — старое поле), пусто — когда поле пусто;
- `orphan.ids` и `orphan.lbl` — ровно `orphan.n` записей, ключи — id заказов без сделки;
- прежние числа (`orphan.n`, `orphan.buy`) не меняются от добавления строк.
"""
import datetime as dt
import os

import contracts as ct
from tests import fixture


def _compute(stub=None, **kw) -> dict:
    old = os.environ.get("KVANT_NOW")
    os.environ["KVANT_NOW"] = fixture.CT_NOW
    kw.setdefault("fx_loader", fixture.ct_fx_loader)
    try:
        return ct.compute(stub or fixture.ContractsStub(), as_of=fixture.CT_TODAY, people={}, **kw)
    finally:
        if old is None:
            os.environ.pop("KVANT_NOW", None)
        else:
            os.environ["KVANT_NOW"] = old


def _stub_with_orphans():
    """Три заказа без сделки (придуманы): с номером в названии, без номера, в валюте без курса."""
    stub = fixture.ContractsStub()
    base = stub.orders[0]
    now = dt.datetime.fromisoformat(fixture.CT_NOW)
    mk = lambda oid, title, days, **kw: dict(base, id=oid, title=title, parentId2=0,
                                             createdTime=(now - dt.timedelta(days=days)).isoformat(), **kw)
    stub.orders += [mk(99101, "990/1. Узел без сделки", 20, opportunity=4_000, currencyId="EUR"),
                    mk(99102, "Узел без номера", 40, opportunity=1_000, currencyId="EUR"),
                    mk(99103, "991/2. Узел в валюте без курса", 5, opportunity=700, currencyId="XAU")]
    return stub


def test_у_строки_сделки_id_кама_из_поля_КАМ():
    stub = fixture.ContractsStub()
    data = _compute(stub)
    by = {str(d["ID"]): d for d in stub.deals}
    seen_empty = seen_full = 0
    for r in data["rows"]:
        d = by.get(str(r["deal"]))
        if d is None:
            continue
        v = d.get(ct.KAM_F) or d.get(ct.KAM_OLD)
        want = "" if v in (None, "", "0") else str(v[0] if isinstance(v, list) else v)
        assert r["kamId"] == want, r["deal"]
        seen_empty += not want
        seen_full += bool(want)
    # обе ветви проверены: и сделка с КАМом, и без него (последняя сделка стаба)
    assert seen_full and seen_empty


def test_заказы_без_сделки_строками_ровно_n():
    data_before = _compute()
    assert data_before["orphan"]["n"] == len(data_before["orphan"]["ids"]) == len(data_before["orphan"]["lbl"])
    data = _compute(_stub_with_orphans())
    O = data["orphan"]
    added = {"99101", "99102", "99103"}
    # способ 1: счёт сборки = длина списка ключей = длина подписей = строк
    assert O["n"] == len(O["ids"]) == len(O["lbl"]) == len(O["rows"]) == data_before["orphan"]["n"] + 3
    # способ 2: ключи — ровно заказы стаба без сделки (по исходным данным, не по счёту сборки)
    assert added <= set(O["ids"])
    assert all(r["id"] == k for r, k in zip(O["rows"], O["ids"]))
    lbl = dict(zip(O["ids"], O["lbl"]))
    assert lbl["99101"] == "990/1" and lbl["99102"] == "id 99102" and lbl["99103"] == "991/2"
    # от давних к новым
    created = [r["created"] for r in O["rows"]]
    assert created == sorted(created)
    # без курса — без суммы и с пометкой; сумма закупки и счёт «без курса» прежние
    row = {r["id"]: r for r in O["rows"]}
    assert row["99103"]["noRate"] is True and row["99103"]["buy"] == ""
    assert row["99101"]["buy"] and row["99101"]["noRate"] is False
    assert O["noRate"] == 1


def test_строки_не_меняют_прежние_числа():
    """Сумма и счёт заказов без сделки считаются как до правки: строки — только добавка."""
    data = _compute(_stub_with_orphans())
    O = data["orphan"]
    # способ 2 — по исходным заказам стаба: без сделки только три придуманных, курс есть у двух (EUR)
    assert _compute()["orphan"]["n"] == 0
    assert O["buy"] == ct._money(4_000 + 1_000)
    assert sum(1 for r in O["rows"] if r["buy"]) == O["n"] - O["noRate"]
    # строки вида направления — без списка заказов без сделки (как и было: orphan только у основного)
    assert set(O) >= {"n", "buy", "ids", "lbl", "rows"}
