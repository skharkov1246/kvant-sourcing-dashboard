"""Вкладка «Реализация» (contracts.compute) на придуманном портале.

Формы ответов повторяют Bitrix (crm.deal.list, crm.item.list СП-172,
crm.stagehistory.list), поэтому считает настоящий contracts.compute. Проверено
то, что ломалось: уволенный ответственный был заглушкой «user#N», возраст
стадии при возврате в неё считался от первого визита, стадии заказов других
воронок показывались кодами.
"""
from __future__ import annotations

import datetime as dt
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import contracts as contracts_mod  # noqa: E402

СЕЙЧАС = "2026-10-09T12:00:00+03:00"
DL = contracts_mod.DL_CUSTOMER
OSS = contracts_mod.OSS_F


def _t(days_ago: float) -> str:
    return (dt.datetime.fromisoformat(СЕЙЧАС) - dt.timedelta(days=days_ago)).isoformat()


class Портал:
    """Ровно те методы клиента, которые зовёт contracts.compute."""

    def __init__(self):
        self.users = {"10": "Вера Астахова (KAM)", "11": "Пётр Вьюгин (OSS)"}
        self.deals = [
            # сделка ведёт ушедший ответственный №20; вернулась в «Закупку» после «Поставки»
            {"ID": "1", "TITLE": "901. Насос", "OPPORTUNITY": "100000", "CURRENCY_ID": "EUR",
             "COMPANY_ID": "", "STAGE_ID": "C0:PURCHASE", "STAGE_SEMANTIC_ID": "P", "CATEGORY_ID": "0",
             "DATE_CREATE": _t(200), "ASSIGNED_BY_ID": "20"},
            {"ID": "2", "TITLE": "902. Компрессор", "OPPORTUNITY": "50000", "CURRENCY_ID": "EUR",
             "COMPANY_ID": "", "STAGE_ID": "C0:PURCHASE", "STAGE_SEMANTIC_ID": "P", "CATEGORY_ID": "0",
             "DATE_CREATE": _t(100), "ASSIGNED_BY_ID": "10"},
        ]
        self.orders = [
            # заказ в воронке 30: стадия должна получить имя, а не остаться кодом
            {"id": 501, "title": "901/1. Насос", "companyId": "", "parentId2": 1, "opportunity": 60000,
             "currencyId": "EUR", "stageId": "DT172_30:PREPARATION", "createdTime": _t(150),
             "movedTime": _t(3), DL: _t(40)[:10], OSS: "21"},
            {"id": 502, "title": "902/1. Компрессор", "companyId": "", "parentId2": 2, "opportunity": 30000,
             "currencyId": "EUR", "stageId": "DT172_26:PREPARATION", "createdTime": _t(90),
             "movedTime": _t(10), DL: _t(-30)[:10], OSS: "11"},
        ]
        # история сделки 1: Закупка (120 дн назад) → Поставка (60) → снова Закупка (5)
        self.hist = {
            2: [("1", "C0:PURCHASE", _t(120)), ("1", "C0:DELIVERY", _t(60)), ("1", "C0:PURCHASE", _t(5)),
                ("2", "C0:PURCHASE", _t(50))],
            172: [("501", "DT172_30:NEW", _t(150)), ("501", "DT172_30:PREPARATION", _t(3)),
                  ("502", "DT172_26:PREPARATION", _t(10))],
        }

    def call(self, method, params=None):
        assert method == "crm.currency.list"
        return [{"CURRENCY": "EUR", "AMOUNT": "1", "AMOUNT_CNT": "1", "BASE": "Y", "DATE_UPDATE": "2026-10-01"},
                {"CURRENCY": "RUB", "AMOUNT": "0.01", "AMOUNT_CNT": "1", "DATE_UPDATE": "2026-10-01"},
                {"CURRENCY": "USD", "AMOUNT": "0.9", "AMOUNT_CNT": "1", "DATE_UPDATE": "2026-10-01"}]

    def list_items(self, entity, *, filter=None, select=None, max_items=None):
        assert entity == 172
        return [dict(o) for o in self.orders]

    def companies_by_ids(self, ids):
        return {}

    def list_deals_fast(self, *, filter=None, select=None, **kw):
        return [dict(d) for d in self.deals]

    def deals_by_ids(self, ids, *, select=None):
        return {}

    def deal_stages_cat(self, cid=0):
        return {"C0:PURCHASE": "Закупка", "C0:DELIVERY": "Поставка", "C0:WON": "Успех"}

    def deal_stages_process(self, cid=0):
        return {"C0:PURCHASE": "Закупка", "C0:DELIVERY": "Поставка"}

    def spa_stages(self, entity, cid):
        return {f"DT172_{cid}:NEW": f"Новый ({cid})", f"DT172_{cid}:PREPARATION": f"Подготовка ({cid})"}

    def stage_history(self, entity, *, category_id=None, since=None, last=None):
        out = {}
        for oid, st, t in self.hist[entity]:
            if last is not None:
                last[oid] = (st, t)
            if st not in {s for s, _ in out.get(oid, [])}:
                out.setdefault(oid, []).append((st, t))
        return out

    def user_name(self, uid):
        return self.users.get(str(uid), f"user#{uid}")


@pytest.fixture(scope="module")
def ct():
    import os
    os.environ["KVANT_NOW"] = СЕЙЧАС
    try:
        люди = {"10": {"name": "Астахова Вера", "active": True},
                "20": {"name": "Донцов Юрий", "active": False},
                "21": {"name": "Ежова Лада", "active": False}}
        return contracts_mod.compute(Портал(), as_of=dt.date(2026, 10, 9), people=люди)
    finally:
        os.environ.pop("KVANT_NOW", None)


def _row(ct, deal):
    return next(r for r in ct["rows"] if r["deal"] == deal)


def test_уволенный_ответственный_и_осс_подписаны_а_не_заглушкой(ct):
    r = _row(ct, "1")
    assert r["manager"] == "Донцов Юрий (уволен)" and r["managerGone"] is True
    assert r["oss"] == "Ежова Лада (уволен)" and r["ossGone"] is True
    assert "user#" not in r["manager"] + r["oss"]
    r2 = _row(ct, "2")
    assert r2["managerGone"] is False and r2["ossGone"] is False


def test_работа_уволенных_собрана_на_переназначение(ct):
    g = ct["gone"]
    assert (g["deals"], g["byManager"], g["byOss"], g["orders"], g["late"], g["people"]) == (1, 1, 1, 1, 1, 2)
    assert g["saleNum"] == 100000


def test_возраст_стадии_от_возврата_а_не_от_первого_визита(ct):
    """Сделка 1 была в «Закупке» 120 дней назад, ушла в «Поставку» и вернулась
    5 дней назад: в стадии она 5 дней, а не 120."""
    assert _row(ct, "1")["stageDays"] == pytest.approx(5, abs=0.1)
    # заказ 501 вошёл в текущую стадию 3 дня назад
    assert _row(ct, "1")["orders"][0]["days"] == pytest.approx(3, abs=0.1)


def test_стадии_заказов_другой_воронки_получают_имя(ct):
    assert _row(ct, "1")["orders"][0]["stageName"] == "Подготовка (30)"


def test_разрез_по_людям_помечает_уволенного_и_называет_сделку(ct):
    by = {m["name"]: m for m in ct["deadlines"]["byOss"]}
    m = by["Ежова Лада (уволен)"]
    assert m["gone"] is True and m["lateOrders"] == 1 and m["lateDeals"] == 1
    assert m["top"]["seq"] == 901 and m["top"]["days"] == 40


def test_окно_бенчмарков_задано(ct):
    assert ct["speed"]["benchDays"] == contracts_mod.BENCH_DAYS
