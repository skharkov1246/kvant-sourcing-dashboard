"""Зонд «2025–2026»: что считается заявкой, выданным ТКП, контрактом и как
считаются заказы поставщикам.

Проверяется то, что дало бы НЕВЕРНЫЙ ответ на вопрос владельца 09.10.2026
(«по ощущениям конверсия в Норильске сильно ниже»):
1. Проигранная сделка с файлом нашего КП, но без стадии «выдано» видна — это
   часть знаменателя, которую прежний зонд терял.
2. Возврат в кат. 0 после предпродажи — контракт (прежде повторный вход в стадию
   отбрасывался, и выигрыш становился «открытым»).
3. Строгий контракт: заказ, проигранный по СЕМАНТИКЕ стадии, — не контракт, даже
   если суффикс стадии не «:FAIL».
4. Бюджетный запрос («Мониторинг цен») помечен и не смешан с закупкой.
5. Технические воронки — не заявки.
6. Заказы: отгрузка против плана, доставка против срока, открытые просроченные.
7. В журнал не попадают названия компаний, сделок и суммы.

Корпус придуман (правило 18).
"""
from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kvant_probe_2526", ROOT / "scripts" / "probe_2526.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
pc = m.pc

СЕГОДНЯ = dt.date(2026, 10, 8)
КП = "UF_CRM_1585568303498"


def стадия(name, sem="P", sort=10):
    return {"name": name, "sem": sem, "sort": sort}


МЕТА = {
    "C8:NEW": стадия("New Request"), "C8:REQ": стадия("Requested from Suppliers | Awaiting Response", sort=20),
    "C8:Q": стадия("Quotation Issued to Client | Working with Client on Quotation", sort=30),
    "C8:PRICE": стадия("Не прошли по цене", "F", 90), "C8:BUD": стадия("Бюджетирование / Мониторинг цен", "F", 91),
    "C2:NEW": стадия("Новый запрос"), "C2:BID": стадия("Тендерное предложение выдано | Работаем", sort=30),
    "C6:NEW": стадия("Новый запрос"), "C6:Q": стадия("ТКП отправлено", sort=30),
    "NEW": стадия("Реализация: старт"), "EXECUTING": стадия("Производство", sort=20),
}
for k in МЕТА:
    МЕТА[k]["cat"] = pc.воронка_стадии(k)


def t(d):
    return f"{d}T10:00:00+03:00"


def сделка(i, stage, cat, sem="P", title="Учебная сделка", company="0", created="2025-02-01", **поля):
    return {"ID": str(i), "TITLE": title, "STAGE_ID": stage, "STAGE_SEMANTIC_ID": sem, "CATEGORY_ID": cat,
            "DATE_CREATE": t(created), "COMPANY_ID": company, "OPPORTUNITY": "0", "CURRENCY_ID": "EUR", **поля}


СДЕЛКИ = [
    # заведена в кат. 0, переведена в воронку клиента, выиграна обратно в кат. 0 (тот же NEW)
    сделка(1, "NEW", "0", title="НН-101 Учебный насос", company="500", OPPORTUNITY="100", **{КП: [{"id": 11}]}),
    # проиграна со стадии запроса поставщикам, но файл нашего КП в сделке есть
    сделка(2, "C8:PRICE", "8", "F", title="НН-102 Учебная задвижка", company="500", OPPORTUNITY="1000",
           **{КП: [{"id": 21}, {"id": 22}]}),
    # проиграна после стадии «выдано»
    сделка(3, "C8:PRICE", "8", "F", company="500", OPPORTUNITY="900", **{КП: {"id": 31}}),
    # бюджетный запрос, закрыт до ТКП
    сделка(4, "C8:BUD", "8", "F", company="500"),
    # тендер другой компании: заказ проигран по семантике, суффикс не FAIL
    сделка(5, "C2:BID", "2", company="600", created="2025-03-01"),
    # техническая воронка
    сделка(6, "C6:Q", "6", company="600"),
]
ИСТОРИЯ = {
    "1": [("NEW", t("2025-02-01")), ("C8:NEW", t("2025-02-02")), ("C8:REQ", t("2025-02-05")),
          ("C8:Q", t("2025-02-20")), ("NEW", t("2025-03-10"))],
    "2": [("C8:NEW", t("2025-02-01")), ("C8:REQ", t("2025-02-03")), ("C8:PRICE", t("2025-03-01"))],
    "3": [("C8:NEW", t("2025-02-01")), ("C8:Q", t("2025-02-10")), ("C8:PRICE", t("2025-04-01"))],
    "4": [("C8:NEW", t("2025-02-01")), ("C8:BUD", t("2025-02-04"))],
    "5": [("C2:NEW", t("2025-03-01")), ("C2:BID", t("2025-03-15"))],
    "6": [("C6:NEW", t("2025-02-01")), ("C6:Q", t("2025-02-03"))],
}
СЕМАНТИКА = {"DT172_26:NEW": "P", "DT172_26:SUCCESS": "S", "DT172_26:UC_X": "F", "DT172_26:FAIL": "F"}
ЗАКАЗЫ = [
    {"id": 50, "parentId2": 1, "stageId": "DT172_26:SUCCESS", "createdTime": t("2025-05-01"), "companyId": 700,
     "opportunity": 800, "currencyId": "EUR", m.SHIP_PLAN: "2025-06-01T03:00:00+03:00", m.SHIP_FACT: t("2025-06-20"),
     m.DL_CUSTOMER: t("2025-09-30"), m.CUST_FACT: t("2025-10-05"), m.SUP_TYPE: "41"},
    {"id": 51, "parentId2": 5, "stageId": "DT172_26:UC_X", "createdTime": t("2025-06-01"), "companyId": 701,
     "opportunity": 300, "currencyId": "EUR"},
    {"id": 52, "parentId2": 1, "stageId": "DT172_26:NEW", "createdTime": t("2025-07-01"), "companyId": 702,
     "opportunity": 200, "currencyId": "EUR", m.DL_CUSTOMER: t("2025-08-01")},
    {"id": 53, "parentId2": 999, "stageId": "DT172_26:NEW", "createdTime": t("2026-02-01"), "companyId": 700,
     "opportunity": 100, "currencyId": "EUR"},
]
КОМПАНИИ = {"500": "АО «Норильский учебный комбинат»", "600": "ООО «Учебный завод Выдумка»"}


def в_евро(сумма, валюта):
    try:
        v = float(сумма or 0)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def собрать():
    import kam
    холдинг = {d["ID"]: kam.client_dir(КОМПАНИИ.get(d["COMPANY_ID"], "")) for d in СДЕЛКИ}
    холдинг["999"] = "Норникель"
    заказы_v4 = [о for о in ЗАКАЗЫ if not о["stageId"].endswith(":FAIL")]
    деньги = {d["ID"]: в_евро(d.get("OPPORTUNITY"), "EUR") for d in СДЕЛКИ}
    строки = pc.классифицировать(СДЕЛКИ, ИСТОРИЯ, МЕТА, заказы_v4, холдинг, СЕГОДНЯ, деньги)
    m.разметить(строки, СДЕЛКИ, ИСТОРИЯ, МЕТА, ЗАКАЗЫ, СЕМАНТИКА, {"8"}, {"1", "2"}, {}, в_евро, СЕГОДНЯ)
    по_id = {r["id"]: r for r in строки}
    все = {d["ID"]: d for d in СДЕЛКИ} | {"999": {"ID": "999", "COMPANY_ID": "500"}}
    заказы = m.разметить_заказы(ЗАКАЗЫ, СЕМАНТИКА, все, холдинг, по_id, {"8"}, в_евро)
    return строки, по_id, заказы


def test_файлы():
    assert m.файлы(None) == set() and m.файлы(False) == set() and m.файлы([]) == set()
    assert m.файлы({"id": 5, "showUrl": "/x"}) == {"5"}
    assert m.файлы([{"id": 1}, {"id": 2}, {"id": 1}]) == {"1", "2"}
    assert m.файлы([{"downloadUrl": "/d?f=1"}]) == {"/d?f=1"}


def test_разметка_определений():
    _, р, _ = собрать()
    assert р["1"]["cls"] == "contract" and р["1"]["signals"]["кат0"], "возврат в кат. 0 — контракт"
    assert р["1"]["strict"] and р["1"]["origin"] == "8"
    assert р["2"]["o2"] and not р["2"]["o1"] and р["2"]["cls"] == "lost" and р["2"]["files"] == 2
    assert р["3"]["o1"] and р["3"]["o2"] and р["3"]["cls"] == "lost"
    assert р["4"]["budget"] and not р["4"]["o1"] and not р["4"]["o2"]
    assert р["5"]["cls"] == "contract" and not р["5"]["strict"], "заказ проигран по семантике — не строгий контракт"
    assert not р["6"]["o0"] and р["6"]["tech"]
    assert [р[i]["seg_funnel"] for i in "123456"] == [True, True, True, True, False, False]
    assert [р[i]["seg_nn"] for i in "1234"] == [True, True, False, False]
    assert р["1"]["seg_company"] and not р["5"]["seg_company"]
    assert р["1"]["seg_kam"] and not р["3"]["seg_kam"]


def test_свод_определений_видит_скрытый_знаменатель():
    строки, _, _ = собрать()
    с = m.свод_определений([r for r in строки if r["seg_funnel"]])
    assert с["O0"]["n"] == 4 and с["O0"]["c"] == 1
    assert с["O1"]["n"] == 2 and с["O1"]["conv"] == 50
    assert с["O12"]["n"] == 3 and с["O12"]["conv"] == 33, "файл без стадии расширяет знаменатель"
    assert с["O2only"]["n"] == 1 and с["O2only"]["lost"] == 1
    assert с["lost"] == 3 and с["lost_after"] == 1 and с["lost_file_only"] == 1 and с["lost_before"] == 1
    assert с["lost_before_budget"] == 1 and с["budget"] == 1
    assert с["lost_before_reasons"] == [("C8:BUD", 1)]
    все = m.свод_определений(строки)
    assert все["v4_not_strict"] == 1 and все["only_signal"]["заказ"] == 1


def test_проигрыш_после_переторжки_не_до_ткп():
    мета = dict(МЕТА, **{"C8:RE": dict(стадия("Переторжка | Торги", sort=40), cat="8")})
    сделки = [сделка(7, "C8:PRICE", "8", "F", company="500")]
    история = {"7": [("C8:NEW", t("2025-02-01")), ("C8:RE", t("2025-02-10")), ("C8:PRICE", t("2025-03-01"))]}
    строки = pc.классифицировать(сделки, история, мета, [], {"7": "Норникель"}, СЕГОДНЯ, {})
    m.разметить(строки, сделки, история, мета, [], {}, {"8"}, set(), {}, в_евро, СЕГОДНЯ)
    с = m.свод_определений(строки)
    assert с["lost_undated"] == 1 and с["lost_before"] == 0


def test_возврат_в_предпродажу_после_кат0_не_выигрыш():
    сделки = [сделка(8, "C8:REQ", "8", company="500")]
    история = {"8": [("C8:NEW", t("2025-02-01")), ("C8:Q", t("2025-02-05")), ("NEW", t("2025-02-10")),
                     ("C8:REQ", t("2025-02-12"))]}
    строки = pc.классифицировать(сделки, история, МЕТА, [], {}, СЕГОДНЯ, {})
    assert строки[0]["cls"] == "open" and not строки[0]["signals"]["кат0"]


def test_первый_заказ_по_всем_годам():
    import copy
    заказы = copy.deepcopy(ЗАКАЗЫ) + [{"id": 54, "parentId2": 1, "stageId": "DT172_26:NEW", "createdTime": t("2026-03-01"),
                                       "companyId": 700, "opportunity": 50, "currencyId": "EUR"}]
    строки, по_id, _ = собрать()
    все = {d["ID"]: d for d in СДЕЛКИ} | {"999": {"ID": "999", "COMPANY_ID": "500"}}
    разм = m.разметить_заказы(заказы, СЕМАНТИКА, все, {"1": "Норникель"}, по_id, {"8"}, в_евро)
    г26 = [о for о in разм if о["year"] == "2026"]
    з = m.свод_заказов(г26, г26, СЕГОДНЯ)
    assert з["offer_to_order"] is None and з["kat0_to_order"] is None, "первый заказ сделки 1 — в 2025"


def test_по_сумме_мало_контрактов_не_печатается():
    строки, _, _ = собрать()
    д = m.свод_суммы([r for r in строки if r["seg_funnel"]], "O12")
    assert д["conv"] is None, "меньше 5 контрактов — доля по сумме скрыта"
    assert д["with"] == 3 and д["fill_l"] == 100


def test_история_все_входы():
    class Портал:
        def call_envelope(self, method, params):
            return {"result": {"items": [
                {"OWNER_ID": 1, "STAGE_ID": "NEW", "CREATED_TIME": "2025-01-01T10:00:00+03:00"},
                {"OWNER_ID": 1, "STAGE_ID": "C8:NEW", "CREATED_TIME": "2025-01-02T10:00:00+03:00"},
                {"OWNER_ID": 1, "STAGE_ID": "NEW", "CREATED_TIME": "2025-02-01T10:00:00+03:00"},
                {"OWNER_ID": 1, "STAGE_ID": "NEW", "CREATED_TIME": "2025-02-01T10:00:00+03:00"}]}, "total": 4}
    история, ошибка = pc.читать_историю(Портал(), ["1"])
    assert ошибка is None
    assert [s for s, _ in история["1"]] == ["NEW", "C8:NEW", "NEW"], "повторный вход хранится, дубль записи — нет"


def test_заказы_сроки_и_просрочка():
    _, _, заказы = собрать()
    год = [о for о in заказы if о["year"] == "2025"]
    з = m.свод_заказов(год, год, СЕГОДНЯ, {"41": "Производитель"})
    assert (з["n"], з["S"], з["P"], з["F"]) == (3, 1, 1, 1)
    assert з["ship_n"] == 1 and з["ship_med"] == 19 and з["ship_late"] == 100
    assert з["cust_n"] == 1 and з["cust_med"] == 5 and з["overdue_open"] == 1
    assert з["types"] == [("Производитель", 1)]
    assert з["share_purchase"] == 100 and з["suppliers"] == 2
    assert з["offer_to_order"] == 70 and з["kat0_to_order"] == 52
    н = m.свод_заказов([о for о in год if о["seg_company"]], год, СЕГОДНЯ)
    assert н["share_purchase"] == 100 and н["deals"] == 1, "живая закупка года — только по заказам сделки 1"
    вне = [о for о in заказы if о["year"] == "2026"]
    assert вне[0]["outside"] and вне[0]["seg_company"]


def test_тип_поставщика_без_брендов():
    assert m.тип_общий("Официальный дистрибьютор")
    assert m.тип_общий("Производитель")
    assert m.тип_общий("Трейдер / посредник") and m.тип_общий("Official distributor")
    assert not m.тип_общий("Дилер Vydumka")
    assert not m.тип_общий("Дилер Stockham") and not m.тип_общий("Трейдер Складпромснаб")
    assert not m.тип_общий("Прямой Прямотех")
    assert not m.тип_общий("")


def test_журнал_без_названий_и_сумм(capsys):
    строки, _, заказы = собрать()
    m.отчёт(строки, заказы, МЕТА, СЕГОДНЯ, {"41": "Производитель Vydumka"}, {"8"})
    out = capsys.readouterr().out
    for запрет in ("Норильский", "Учебный", "Выдумка", "Vydumka", "НН-101", "насос", "800 ", "Мониторинг цен", "экономика"):
        assert запрет not in out, запрет
    assert "КОНВЕРСИЯ ПРИ РАЗНЫХ ОПРЕДЕЛЕНИЯХ" in out and "ЗАКАЗЫ ПОСТАВЩИКАМ" in out
    assert "тип #41" in out


def test_технические_воронки_и_прогон():
    import people
    assert set(people.TECH_CATS) == set(m.TECH_CATS)
    wf = yaml.safe_load((ROOT / ".github/workflows/probe.yml").read_text(encoding="utf-8"))
    вход = (wf.get("on") or wf[True])["workflow_dispatch"]["inputs"]["script"]
    assert "probe_2526.py" in вход["options"]
    assert wf["concurrency"]["group"] == "bitrix-portal"
