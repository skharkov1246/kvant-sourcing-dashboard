"""Честные метрики (19.10.2026): заказ проигран — по семантике стадии, а не по суффиксу
«:FAIL»; доставленный заказ — не просрочка; маржа — только по контрактам с внесённой
закупкой; «Просрочено» Пульса — по сроку клиента в заказе, а не по CLOSEDATE; год не
зашит. Корпус придуман (правило 18)."""
from __future__ import annotations

import datetime as dt

import company as company_mod
import kam as kam_mod
import people as people_mod
import reps as reps_mod
from bitrix_client import BitrixClient, drop_lost, lost_stage
from tests import fixture

DL = people_mod.DL_CUSTOMER
DEL = people_mod.DEL_FACT


def _client(meta: dict[int, dict] | Exception) -> BitrixClient:
    """Настоящий BitrixClient без сети: справочник стадий воронки подменён."""
    c = BitrixClient.__new__(BitrixClient)
    c.reads = []

    def spa_stage_meta(entity, cid):
        c.reads.append((entity, cid))
        if isinstance(meta, Exception):
            raise meta
        return meta.get(cid, {})
    c.spa_stage_meta = spa_stage_meta
    return c


ORDERS = [{"id": 1, "stageId": "DT172_26:UC_9"},      # своя стадия провала (семантика F)
          {"id": 2, "stageId": "DT172_26:UC_1"},      # в работе
          {"id": 3, "stageId": "DT172_26:FAIL"},
          {"id": 4, "stageId": "DT172_30:NEW"},
          {"id": 5, "stageId": "DT172_30:UC_X"}]      # вне справочника — по суффиксу: жив
META = {26: {"DT172_26:UC_9": {"name": "Отказ клиента", "sem": "F"},
             "DT172_26:UC_1": {"name": "Производство", "sem": "P"},
             "DT172_26:FAIL": {"name": "Провал", "sem": "F"}},
        30: {"DT172_30:NEW": {"name": "Размещение", "sem": "P"}}}


def test_провал_заказа_по_семантике_стадии_а_не_по_суффиксу():
    живые = [o["id"] for o in drop_lost(_client(META), 172, ORDERS)]
    assert живые == [2, 4, 5]


def test_справочник_читается_один_раз_на_воронку():
    c = _client(META)
    drop_lost(c, 172, ORDERS)
    drop_lost(c, 172, ORDERS)
    assert sorted(c.reads) == [(172, 26), (172, 30)]


def test_справочник_не_прочитан_остаётся_суффикс():
    живые = [o["id"] for o in drop_lost(_client(RuntimeError("ACCESS_DENIED")), 172, ORDERS)]
    assert живые == [1, 2, 4, 5]


def test_клиент_без_справочника_суффикс_как_раньше():
    lost = lost_stage(fixture.PeopleStub(), 172, ORDERS)
    assert [lost(o["stageId"]) for o in ORDERS] == [False, False, True, False, False]


# ------------------------------------------------------------------ доставлен — не просрочка
def _orders_delivered():
    return [dict(o, **{DEL: "2026-08-20"}) if o["id"] == 900 else o for o in fixture.ORDERS]


def test_доставленный_заказ_не_просрочка_у_коммерсанта():
    r = reps_mod.compute(fixture.PeopleStub(), as_of=fixture.PEOPLE_TODAY,
                         created=fixture.CREATED, orders_src=_orders_delivered())
    kam = next(x for x in r["reps"] if x["uid"] == "1")
    assert kam["kpis"]["overdue"] == 0


def test_доставленный_заказ_не_просрочка_в_составе():
    r = people_mod.compute(fixture.PeopleStub(), as_of=fixture.PEOPLE_TODAY, open_deals=fixture.OPEN_DEALS,
                           created=fixture.CREATED, orders=_orders_delivered())
    kam = {p["uid"]: p for p in r["roles"]["kam"]["people"]}
    assert kam["1"]["late"] == 0


# ------------------------------------------------------------------ маржа без закупки
def test_маржа_коммерсанта_только_по_сделкам_с_закупкой():
    # в реализации 102 (продажа 500K, закупка 400K), 201 (300K) и 203 (220K) — у двух
    # последних заказа нет: раньше маржа = 1 020K − 400K = 620K (61 %), теперь 100K (20 %)
    r = reps_mod.compute(fixture.PeopleStub(), as_of=fixture.PEOPLE_TODAY,
                         created=fixture.CREATED, orders_src=fixture.ORDERS)
    k = next(x for x in r["reps"] if x["uid"] == "1")["kpis"]
    assert k["salesRaw"] == 1_020_000 and k["marginRaw"] == 100_000
    assert k["marginPct"] == 20 and k["noBuy"] == 2


def test_маржа_кам_только_по_контрактам_с_закупкой():
    groups = {str(d["ID"]): "Северный синтез" for d in fixture.CREATED}
    # main отдаёт заказы уже без проигранных; контракты 102 (500K / 400K), 103 (80K / 50K)
    # и 203 (220K, заказа нет): маржа 580K − 450K = 130K (22 %), а не 800K − 450K (44 %)
    orders = drop_lost(fixture.PeopleStub(), 172, fixture.ORDERS)
    r = kam_mod.compute_set(fixture.PeopleStub(), None, as_of=fixture.PEOPLE_TODAY,
                            created=fixture.CREATED, orders=orders, deal_group=groups)
    t = r["totals"]
    assert t["contracts"] == 3 and t["noBuy"] == 1 and t["marginPct"] == 22
    маржа = next(k for k in r["kpis"] if k["lbl"] == "Маржа")
    assert "без закупки 1" in маржа["meta"]


# ------------------------------------------------------------------ «Просрочено» Пульса
class CompanyStub(fixture.PeopleStub):
    def count(self, *a, **kw):
        return 0

    def stage_first_entry(self, *a, **kw):
        return {}


def test_просрочено_пульса_по_сроку_клиента_а_не_по_closedate():
    # 102 — заказ со сроком клиента 01.08 не доставлен: просрочена; 101 и 103 —
    # CLOSEDATE в прошлом у 103, но срок клиента не сорван: не просрочены
    r = company_mod.compute(CompanyStub(), as_of=fixture.PEOPLE_TODAY,
                            created=fixture.CREATED, orders=fixture.ORDERS)
    assert sorted(d["id"] for d in r["deals"] if d["ovd"]) == ["102"]
    r2 = company_mod.compute(CompanyStub(), as_of=fixture.PEOPLE_TODAY,
                             created=fixture.CREATED, orders=_orders_delivered())
    assert not any(d["ovd"] for d in r2["deals"])


def test_год_не_зашит():
    год = str(dt.date.today().year)
    for m in (company_mod, kam_mod, reps_mod, people_mod):
        assert m.YEAR_START.startswith(год + "-01-01"), m.__name__
    assert company_mod.COHORT_START.startswith(str(int(год) - 1))
