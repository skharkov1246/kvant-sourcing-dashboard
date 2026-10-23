"""Деньги «Реализации»: число — только если ему можно верить (ревизия 23.10.2026).

Повод: в карточке сделки продажа €107K и закупка €645K при «поставка ~30к, закуп ~15к».
  · валюта без курса в справочнике Битрикса — сумма неизвестна, а не 1:1 к базовой;
  · закупка, несоизмеримая с продажей, — текстом, не в марже и не в итогах;
  · заказ без суммы закупки при суммах у других доли выручки не получает;
  · итоги — по открытым сделкам;
  · надёжность поставщика — только к бюджетной дате.
Ревизия 23.10.2026, вторая часть: «ведёт уволенный» — и по ответственному заказа;
пени «прикидкой» по ставкам «из практики» сняты до чтения условий договоров.
Корпус придуман (CLAUDE.md, правило 18): сделки ContractsStub.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import contracts as ct
import lifecycle
from tests import fixture


def _compute(stub, people: dict | None = None) -> dict:
    old = os.environ.get("KVANT_NOW")
    os.environ["KVANT_NOW"] = fixture.CT_NOW
    try:
        return ct.compute(stub, as_of=fixture.CT_TODAY, people=people or {})
    finally:
        if old is None:
            os.environ.pop("KVANT_NOW", None)
        else:
            os.environ["KVANT_NOW"] = old


def _row(data: dict, did: str) -> dict:
    return next(r for r in data["rows"] if r["deal"] == did)


def test_валюта_без_курса_не_один_к_одному():
    stub = fixture.ContractsStub()
    stub.deals[0]["CURRENCY_ID"] = "AED"          # курса AED в справочнике стаба нет
    stub.deals[0]["OPPORTUNITY"] = "107000"
    base = _compute(fixture.ContractsStub())
    data = _compute(stub)
    r = _row(data, stub.deals[0]["ID"])
    assert r["saleKnown"] is False and r["saleEur"] == 0 and r["saleLbl"] == "курса нет"
    assert r["fxMissing"] == ["AED"] and r["marginEur"] is None
    assert data["head"]["nFx"] == 1 and data["head"]["fxCodes"] == ["AED"]
    # в итоги не вошла: Σ продажи меньше прежней ровно на сумму этой сделки (она открыта)
    b = _row(base, stub.deals[0]["ID"])
    live_sum = sum(x["saleEur"] for x in base["rows"] if not x["closed"])
    assert ct._money(live_sum - b["saleEur"]) == data["head"]["sale"]


def test_поле_экономики_в_валюте_без_курса():
    rate = {"EUR": 1.0, "RUB": 0.01}
    assert ct._parse_econ_money("645000|XYZ", rate) == 0.0
    assert ct._parse_econ_money("1000|RUB", rate) == 10.0
    assert ct._econ_cur("645000|XYZ") == "XYZ" and ct._econ_cur("0|XYZ") == "" and ct._econ_cur("") == ""


def test_несоизмеримая_закупка_текстом_и_не_в_итогах():
    stub = fixture.ContractsStub()
    did = stub.deals[1]["ID"]
    # заказы без сумм — закупка из полей экономики, вшестеро больше продажи
    for o in stub.orders:
        if str(o["parentId2"]) == did:
            o["opportunity"] = 0
    stub.deals[1][ct.BUY_ECON_RUB] = f"{float(stub.deals[1]['OPPORTUNITY']) * 6 / 0.01:.0f}|RUB"
    data = _compute(stub)
    r = _row(data, did)
    assert r["buySrc"] == "econ" and r["buySusp"] is True
    assert r["buyLbl"] == "не сходится" and r["marginEur"] is None
    assert data["head"]["nBuySusp"] >= 1
    live = [x for x in data["rows"] if not x["closed"]]
    ожидание = sum(x["buyEur"] for x in live if x["buySrc"] in ("orders", "econ") and not x["buySusp"])
    assert ct._money(ожидание) == data["head"]["buy"]
    # в пределах — число, как раньше
    stub.deals[1][ct.BUY_ECON_RUB] = f"{float(stub.deals[1]['OPPORTUNITY']) * 0.5 / 0.01:.0f}|RUB"
    r = _row(_compute(stub), did)
    assert r["buySusp"] is False and r["buyLbl"].startswith("€") and r["marginEur"] is not None


def test_выручка_не_разносится_больше_суммы_сделки():
    """Заказ без суммы закупки при суммах у других доли не получает: Σ ≤ 100 %."""
    stub = fixture.ContractsStub()
    for d in stub.deals:
        ords = [o for o in stub.orders if str(o["parentId2"]) == d["ID"]]
        if len(ords) >= 2:
            ords[-1]["opportunity"] = 0           # у одного заказа суммы нет
    data = _compute(stub)
    смешанных = 0
    for r in data["rows"]:
        живые = [o for o in r["orders"] if o["live"]]
        if any(o["buyRaw"] > 0 for o in живые) and any(o["buyRaw"] == 0 for o in живые):
            смешанных += 1
        assert sum(o["revEur"] for o in живые) <= r["saleEur"] + len(живые), r["deal"]
    assert смешанных, "корпус должен содержать сделки со смешанными суммами закупки"


def test_итоги_по_открытым_сделкам():
    data = _compute(fixture.ContractsStub())
    live = [r for r in data["rows"] if not r["closed"]]
    assert ct._money(sum(r["saleEur"] for r in live if r["saleKnown"])) == data["head"]["sale"]
    assert data["head"]["sumsOver"] == "live"


def test_надёжность_поставщика_только_к_бюджетной_дате():
    d = dt.date(2026, 10, 1)
    без_бюджета = {"sup": "П-1", "shipFact": d, "shipPlan": d, "prodEnd": None, "delFact": None}
    с_бюджетом = {"sup": "П-2", "shipFact": d, "shipPlan": d, "prodEnd": d - dt.timedelta(days=10), "delFact": None}
    _lead, sup = lifecycle.speeds([без_бюджета, с_бюджетом], dt.date(2026, 10, 19))
    assert "П-1" not in sup, "плановую отгрузку переписывают под факт — с ней поставщик всегда «вовремя»"
    assert sup["П-2"] == [10]


def test_подписи_оплаты_и_удп_говорят_что_известно():
    assert "платёжки клиента в заказе нет" in lifecycle.EXC["unpaid"][1]
    assert "УПД в заказе нет" in lifecycle.EXC["noUtd"][1]


def test_нет_суммы_сделки_не_ноль_евро():
    stub = fixture.ContractsStub()
    stub.deals[2]["OPPORTUNITY"] = "0"
    data = _compute(stub)
    r = _row(data, stub.deals[2]["ID"])
    assert r["saleLbl"] == "суммы нет" and r["marginEur"] is None
    assert data["head"]["nNoSale"] >= 1


def test_маржа_при_неполной_закупке_текстом_и_не_в_итоге():
    stub = fixture.ContractsStub()
    data = _compute(stub)
    did = next(r["deal"] for r in data["rows"] if not r["closed"] and r["buySrc"] == "orders"
               and r["saleEur"] > 1000 and r["norders"] > 0)
    r0 = _row(data, did)
    d = next(x for x in stub.deals if x["ID"] == did)
    # закупка — 15 % продажи: внутри полосы правдоподобия, но маржа 85 % выше порога
    d["OPPORTUNITY"] = str(float(d["OPPORTUNITY"]) * r0["buyEur"] / r0["saleEur"] / 0.15)
    data = _compute(stub)
    r = _row(data, did)
    assert r["grossSusp"] is True and r["marginEur"] is None and r["marginLbl"] == "закупка неполная?"
    live = [x for x in data["rows"] if not x["closed"]]
    assert ct._money(sum(x["marginEur"] for x in live if x["marginEur"] is not None)) == data["head"]["margin"]
    assert data["head"]["grossSusp"] >= 1


ШАБЛОН = Path(__file__).resolve().parent.parent / "templates" / "dashboard_core.html"


def _вкладка() -> tuple[str, str]:
    t = ШАБЛОН.read_text(encoding="utf-8")
    return t, t[t.index("function renderContracts"):t.index("function renderSuppliers")]


def test_пени_не_прикидкой_а_после_чтения_договоров():
    """База прикидки — вся сумма сделки × худшая задержка, ставки «из практики», а не из
    договоров. Битое хуже пустого: блок снят, вместо него — нейтральная строка."""
    t, вкладка = _вкладка()
    for нет in ("Пени по контрактам", "dlPen", "calcPen", "Потенциальные пени", "0,05–0,1%",
                "Пени не начисляются"):
        assert нет not in t, нет
    assert "Пени — после чтения условий договоров." in вкладка
    методика = t[t.index("'ct.cost':"):t.index("'ct.dl':")]
    assert "прикидк" not in методика and "Пени — после чтения условий договоров." in методика
    # остальное в «Цене просрочки» на месте: ставка и пересчёт упущенной доходности
    assert 'id="dlRate"' in вкладка and 'id="dlCostBox"' in вкладка and "drawCost(RATE)" in вкладка


def test_ведёт_уволенный_плитка_фильтр_и_свод_сходятся():
    """Ответственный живого заказа уволен — сделка в «Ведёт уволенный»: свод, очередь,
    плитка и фильтр считают одно и то же."""
    stub = fixture.ContractsStub()
    живой = next(o for o in stub.orders if o["stageId"].endswith(":PREPARATION"))
    живой["assignedById"] = "99"
    data = _compute(stub, people={"99": {"name": "Сотрудник 99", "active": False}})
    live = [r for r in data["rows"] if not r["closed"]]
    флаг = [r for r in live if r["managerGone"] or r["ossGone"] or r["respGone"]]
    assert len(флаг) == 1 and флаг[0]["deal"] == str(живой["parentId2"])
    assert data["gone"]["deals"] == data["gone"]["byResp"] == data["life"]["counts"]["gone"] == 1
    _t, вкладка = _вкладка()
    assert "nGone=live.filter(r=>r.managerGone||r.ossGone||r.respGone)" in вкладка
    assert "r.filter(x=>(x.managerGone||x.ossGone||x.respGone)&&!x.closed)" in вкладка
    # без уволенных — как было: признака нет ни у одной сделки
    assert not any(r["respGone"] for r in _compute(fixture.ContractsStub())["rows"])
