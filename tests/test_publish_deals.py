"""Снимок сделок, запросов и заказов поставщикам (deals:v1): что попадёт на сайт.

Проверяется то, что даёт НЕВЕРНЫЙ или ОПАСНЫЙ отчёт:
1. ДЕНЬГИ — ТОЛЬКО В РАЗДЕЛЕ money. Воркер закрывает его правом suppliers_fin;
   сумма, оставшаяся в строке сделки или заказа, ушла бы человеку без права.
2. ПОСТАВЩИК ЗАПРОСА — ссылка «Supplier» прежде companyId (как в main.py).
3. НОМЕР «0» — не номер: карточка без сделки не должна ссылаться на сделку 0.
4. СТРОКИ ЗАКАЗОВ — пакетом, с дочитыванием заказа длиннее 50 строк; ключи
   фильтра в пакете закодированы (иначе портал режет «=ownerType» не там).
5. СТРОКИ И ЦЕНЫ ИДУТ ОДНИМ ПОРЯДКОМ: lines[i] и money.lines[i] — одна строка.

Корпус придуман (правило 18).
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib import parse

import pytest

ROOT = Path(__file__).resolve().parents[1]


def модуль():
    spec = importlib.util.spec_from_file_location("kvant_publish_deals", ROOT / "scripts" / "publish_deals.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m = модуль()
ПОЛЯ_КП = ["ufCrm18_1"]


def вход(**над):
    база = dict(
        сделки={
            "10": {"ID": "10", "TITLE": "Сделка А", "CATEGORY_ID": "0", "STAGE_ID": "EXECUTING",
                   "STAGE_SEMANTIC_ID": "P", "COMPANY_ID": "500", "DATE_CREATE": "2025-03-01T10:00:00+03:00",
                   "OPPORTUNITY": "1000.5", "CURRENCY_ID": "EUR",
                   m.ECON_PAID: "400|EUR", m.ECON_REST: "600.5|EUR"},
            "11": {"ID": "11", "TITLE": "Сделка Б", "CATEGORY_ID": "2", "STAGE_ID": "C2:LOSE",
                   "STAGE_SEMANTIC_ID": "F", "COMPANY_ID": "0"},
        },
        запросы=[
            {"id": 1, "parentId2": 10, "ufCrm18Supplier": ["CO_700", "C_5"], "companyId": 701,
             "stageId": "DT166_24:SUCCESS", "createdTime": "2025-04-01T09:00:00+03:00", "ufCrm18_1": [{"id": 1}]},
            {"id": 2, "parentId2": 0, "companyId": 702, "stageId": "DT166_24:NEW", "createdTime": "2025-04-02"},
            {"id": 3, "parentId2": 11, "companyId": None, "stageId": "DT166_24:FAIL"},
        ],
        заказы=[
            {"id": 50, "title": "PO-1", "parentId2": 10, "companyId": 700, "opportunity": 800,
             "currencyId": "USD", "stageId": "DT172_26:PREPARATION", "createdTime": "2025-05-01T00:00:00+03:00",
             m.DL_CUSTOMER: "2025-09-30T03:00:00+03:00"},
            {"id": 51, "title": "PO-2", "parentId2": 99, "companyId": 703, "opportunity": None,
             "stageId": "DT172_26:FAIL"},
        ],
        строки={"50": [{"productName": " Подшипник 6205 ", "quantity": 2, "measureName": "шт",
                        "priceExclusive": 100, "price": 120, "taxRate": 20},
                       {"productName": "Уплотнение", "quantity": "1", "priceExclusive": "50.5", "price": 50.5}],
                "51": []},
        компании={"500": "Заказчик", "700": "Поставщик А", "701": "Лишний", "702": "Поставщик Б", "703": "Поставщик В"},
        стадии={"deal": {}, "rfq": {}, "order": {}},
        курсы={"EUR": 1.0, "USD": 0.9},
        база_валюты="EUR",
        поля_кп=ПОЛЯ_КП,
        сейчас=datetime(2026, 10, 2, tzinfo=timezone.utc),
    )
    база.update(над)
    return база


def test_деньги_только_в_разделе_money():
    с = m.собрать(**вход())
    без_денег = {k: v for k, v in с.items() if k != "money"}
    текст = json.dumps(без_денег, ensure_ascii=False)
    for сумма in ("1000.5", "600.5", "800", "120", "50.5"):
        assert сумма not in текст, сумма
    assert с["money"]["deals"]["10"] == [1000.5, "EUR", 400.0, 600.5, "EUR"]
    assert с["money"]["orders"] == {"50": [800.0, "USD"]}       # пустая сумма не выдумывается


def test_поставщик_запроса_и_нулевой_номер():
    с = m.собрать(**вход())
    по_id = {r["id"]: r for r in с["rfq"]}
    assert по_id["1"]["supplier"] == "700"      # ссылка Supplier прежде companyId
    assert по_id["2"]["supplier"] == "702"
    assert по_id["2"]["deal"] is None           # «0» — не сделка
    assert по_id["3"]["supplier"] is None
    assert по_id["1"]["quote"] is True and по_id["2"]["quote"] is False
    assert [по_id[i]["outcome"] for i in "123"] == ["S", "P", "F"]
    assert "701" not in с["companies"]          # компания, на которую никто не ссылается, не едет
    assert с["deals"][0]["customer"] == "500" and с["deals"][1]["customer"] is None


def test_заказы_и_потерянная_сделка():
    с = m.собрать(**вход())
    o = {x["id"]: x for x in с["orders"]}
    assert o["50"]["deadline"] == "2025-09-30" and o["51"]["outcome"] == "F"
    assert с["totals"]["orders_live"] == 1
    assert с["totals"]["lost_deal_links"] == 1   # заказ 51 → сделка 99, которой нет в выборке
    assert {d["id"] for d in с["deals"]} == {"10", "11"}


def test_строки_и_цены_одним_порядком():
    с = m.собрать(**вход())
    assert len(с["lines"]) == len(с["money"]["lines"]) == 2
    assert с["lines"][0] == {"order": "50", "name": "Подшипник 6205", "qty": 2.0, "unit": "шт"}
    assert с["money"]["lines"][0] == [100.0, 120.0, 20.0, "USD"]
    assert с["money"]["lines"][1][0] == 50.5
    assert с["totals"]["orders_with_lines"] == 1


@pytest.mark.parametrize("v,ждём", [("0", None), ("", None), (None, None), ("012", None), ("15", "15"), (15, "15")])
def test_номер(v, ждём):
    assert m.номер(v) == ждём


def test_число_и_валюта_денег():
    assert m.число("1000.50|EUR") == 1000.5
    assert m.число("abc") is None and m.число("") is None
    assert m.валюта_денег("5|USD") == "USD"
    assert m.валюта_денег("5", "EUR") == "EUR"


class Портал:
    def __init__(self, рядов):
        self.рядов = рядов
        self.вызовы = []

    def call(self, method, params=None):
        self.вызовы.append((method, params))
        if method == "batch":
            res, total = {}, {}
            for k, q in params["cmd"].items():
                метод, строка = q.split("?", 1)
                assert метод == "crm.item.productrow.list"
                ф = dict(parse.parse_qsl(строка))
                assert ф["filter[=ownerType]"] == "Tac"     # 172 → «T» + «ac»
                n = ф["filter[=ownerId]"]
                все = self.рядов.get(n, [])
                res[k] = {"productRows": все[:50]}
                total[k] = len(все)
            return {"result": res, "result_error": {}, "result_total": total}
        if method == "crm.item.productrow.list":
            n = str(params["filter"]["=ownerId"])
            после = params["filter"][">id"]
            return {"productRows": [r for r in self.рядов[n] if r["id"] > после][:50]}
        raise AssertionError(method)


def test_строки_заказов_пакетом_и_дочитывание():
    рядов = {"7": [{"id": i} for i in range(1, 121)], "8": [{"id": 500}]}
    номера = [str(i) for i in range(100, 159)] + ["7", "8"]
    п = Портал(рядов)
    out = m.строки_заказов(п, номера)
    assert len(out["7"]) == 120 and [r["id"] for r in out["7"]] == list(range(1, 121))
    assert out["8"] == [{"id": 500}] and out["100"] == []
    пакетов = sum(1 for x in п.вызовы if x[0] == "batch")
    assert пакетов == 2                          # 61 заказ → два пакета по 50
    assert all(len(x[1]["cmd"]) <= 50 for x in п.вызовы if x[0] == "batch")


def test_прогоны_в_общей_очереди_и_ночью_после_пополнения():
    import yaml
    руч = yaml.safe_load((ROOT / ".github/workflows/deals-publish.yml").read_text(encoding="utf-8"))
    assert руч["concurrency"]["group"] == "bitrix-portal"
    assert (руч.get("on") or руч[True])["workflow_dispatch"]["inputs"]["apply"]["default"] is False, "по умолчанию вхолостую"
    ноч = yaml.safe_load((ROOT / ".github/workflows/library-daily.yml").read_text(encoding="utf-8"))
    j = ноч["jobs"]["deals"]
    assert j["needs"] == "daily" and j["if"] == "always()"
    assert j["env"]["DEALS_NIGHTLY"] in ("dry", "apply", "off")
    assert j["env"]["BITRIX_PARALLEL"] == "1"
