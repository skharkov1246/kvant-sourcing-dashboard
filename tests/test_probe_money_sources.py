"""Зонд v59 «Источники денег реализации»: что дало бы неверный вывод или утечку.

1. Журнал публичный: ни номеров сделок, компаний, заказов и счетов, ни сумм — ни в
   каком виде («9876543», «9 876 543», «€9877K», «€9.88M»), ни текста исключения.
2. ЦБ недоступен — первой строкой, колонки «по ЦБ» не считаются, 1:1 не подставляется.
3. Корзины — симметричны по логарифму; ноль и минус — отдельно.
4. Объяснители: НДС по дате отгрузки, курс — окном дат и только валют сделки,
   «неоднозначно», когда подошло несколько; ×1000; часть; больше.
5. Дата курса — каскад владельца 25.10 по Москве.
6. Ряды ЦБ: курс раньше первой записи — None; номинал учтён; код ряда — ParentCode.
7. Сверка итогов: нынешние правила дают невязку; тень сверяется по всем своим строкам.
8. Ревью v59: закупка бюджета со знаком минус — по модулю; выигрыш у сделки, заведённой
   в кат. 0, — не первый вход; бюджет — и по номеру заказа; близнецы — в окне дат и в
   смете; счета СП-137 — оплаченные отдельно, всё или ничего, на дату заведения; валюта
   money-поля — только кодом; воронка 16 — номером; зеркало ЦБ — когда cbr.ru молчит.

Корпус придуман (правило 18): числа и номера нарочно особые.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import io
import random
import re
import zipfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kvant_probe_money_sources", ROOT / "scripts" / "probe_money_sources.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
fx = m.fx
ct = m.ct

СЕГОДНЯ = dt.date(2026, 10, 25)
D = dt.date

# ── придуманный портал ──────────────────────────────────────────────────────

СУММЫ = (9876543.21, 2408913.03, 8642097.53, 7083686.5, 3141592.65, 2718281.83, 1618033.99,
         4669201.61, 1570796.33, 1414213.56, 2236067.97, 1732050.81, 5772156.65, 6180339.89,
         7071067.81, 1202056.9, 3333331.07, 2645751.31)
НОМЕРА = ("7654321", "7654322", "7654323", "7654324", "7654325", "7654326", "7654327", "7654390",
          "7654391", "7654399", "7650001", "7650002", "7650003", "7650101", "7655001", "7655002",
          "7655003", "7655004", "7655005", "7656001", "7656002", "7657001", "7657002", "7657003",
          "7657004", "1239876543")                                              # придуманный ИНН с верной суммой


def t(s):
    return s + "T12:00:00+03:00"


МЕТА = {
    "C2:NEW": {"name": "Новый запрос", "sem": "P", "sort": 10, "cat": "2"},
    "C2:UC_TKP": {"name": "ТКП отправлено", "sem": "P", "sort": 30, "cat": "2"},
    "NEW": {"name": "Старт реализации", "sem": "P", "sort": 10, "cat": "0"},
    "UC_PAID": {"name": "Оплата получена | Закрытие сделки", "sem": "P", "sort": 80, "cat": "0"},
    "WON": {"name": "Сделка успешна", "sem": "S", "sort": 90, "cat": "0"},
    "LOSE": {"name": "Проигрыш", "sem": "F", "sort": 99, "cat": "0"},
}


def сделка(i, stage, sem, cur, opp, company, **к):
    d = {"ID": i, "TITLE": к.pop("title", "Учебная поставка"), "CATEGORY_ID": к.pop("cat", "0"),
         "STAGE_ID": stage, "STAGE_SEMANTIC_ID": sem, "CURRENCY_ID": cur, "OPPORTUNITY": opp,
         "COMPANY_ID": company, "DATE_CREATE": t(к.pop("created", "2025-01-10")),
         "MOVED_TIME": t(к.pop("moved", "2026-09-01")), "CLOSEDATE": к.pop("closedate", "2026-12-31"),
         "IS_MANUAL_OPPORTUNITY": "Y", "TAX_VALUE": 0, "OPPORTUNITY_ACCOUNT": opp, "ACCOUNT_CURRENCY_ID": "EUR"}
    d.update(к)
    return d


СДЕЛКИ = [
    # U1, EUR, «Сумма» — число в AED (≈ 4,1 × спецификации)
    сделка("7654321", "NEW", "P", "EUR", "9876543.21", "7650001", **{m.TKP_FINAL: "2025-05-20T23:30:00+00:00",
                                                                      ct.BUY_ECON_RUB: "7083686.5|RUB"}),
    # U1, RUB, «Сумма» с НДС 22 % от бюджета
    сделка("7654322", "NEW", "P", "RUB", "8642097.53", "7650001", title="901. Учебная поставка",
           **{m.TKP_FINAL: t("2026-02-10")}),
    # U2 — «Оплата получена», без «ТКП согласован»: дата — последнее КП до кат. 0
    # заведена в кат. 0 (вход в день создания), переведена в пресейл, выиграна обратно (ревью v59)
    сделка("7654323", "UC_PAID", "P", "USD", "2718281.83", "7650003", created="2024-12-01"),
    # U3 — успех полгода назад
    # «ТКП согласован» в день создания — счётчик «похоже на умолчание»
    сделка("7654324", "WON", "S", "EUR", "1618033.99", "7650003", moved="2026-06-01", created="2026-02-28",
           **{m.TKP_FINAL: t("2026-03-01")}),
    # успех давно — только счётчик
    сделка("7654325", "WON", "S", "EUR", "1414213.56", "7650003", moved="2024-01-10"),
    # Basra: компания воронки 16
    сделка("7654326", "NEW", "P", "EUR", "2236067.97", "7650002", **{m.TKP_FINAL: t("2025-04-01")}),
    # карточка только реализации: КП у близнеца в пресейле
    сделка("7654327", "NEW", "P", "EUR", "4669201.61", "7650004", created="2025-10-01"),
]
ПРЕСЕЙЛ = [
    сделка("7654390", "C2:UC_TKP", "P", "EUR", "1732050.81", "7650004", cat="2", created="2025-08-01"),
    сделка("7654391", "C16:NEW", "P", "EUR", "5772156.65", "7650002", cat="16"),
    сделка("7654399", "C22:NEW", "P", "EUR", "6180339.89", "7650003", cat="22"),
]
ИСТОРИЯ = [
    {"ID": 0, "OWNER_ID": "7654323", "STAGE_ID": "NEW", "CREATED_TIME": t("2024-12-01")},
    {"ID": 1, "OWNER_ID": "7654323", "STAGE_ID": "C2:UC_TKP", "CREATED_TIME": t("2024-12-20")},
    {"ID": 2, "OWNER_ID": "7654323", "STAGE_ID": "C2:UC_TKP", "CREATED_TIME": t("2025-01-15")},
    {"ID": 3, "OWNER_ID": "7654323", "STAGE_ID": "NEW", "CREATED_TIME": t("2025-02-01")},
    {"ID": 4, "OWNER_ID": "7654323", "STAGE_ID": "C2:UC_TKP", "CREATED_TIME": t("2025-03-01")},
    {"ID": 5, "OWNER_ID": "7654324", "STAGE_ID": "WON", "CREATED_TIME": t("2026-06-01")},
    {"ID": 6, "OWNER_ID": "7654321", "STAGE_ID": "C2:UC_TKP", "CREATED_TIME": t("2025-04-01")},
    {"ID": 7, "OWNER_ID": "7654390", "STAGE_ID": "C2:UC_TKP", "CREATED_TIME": t("2025-09-01")},
    {"ID": 8, "OWNER_ID": "7654327", "STAGE_ID": "NEW", "CREATED_TIME": t("2025-10-01")},
]
ЗАКАЗЫ = [
    {"id": 7655001, "title": "901/1. Учебный заказ", "parentId2": 7654322, m.ORDER_DEAL_ID: "7654322", "opportunity": 3141592.65, "currencyId": "RUB",
     "stageId": "DT172_26:NEW", "createdTime": t("2026-03-01"), "companyId": 7650101, ct.SUP_PAID: 50,
     ct.DL_CUSTOMER: "2026-05-01"},
    {"id": 7655002, "title": "Учебный заказ", "parentId2": 7654321, m.ORDER_DEAL_ID: "7654321", "opportunity": 1202056.9, "currencyId": "AED",
     "stageId": "DT172_26:NEW", "createdTime": t("2025-06-01"), "companyId": 7650101},
    {"id": 7655003, "parentId2": 7654321, m.ORDER_DEAL_ID: "7654321", "opportunity": 0, "currencyId": "EUR",
     "stageId": "DT172_26:NEW", "createdTime": t("2025-06-02"), "companyId": 7650101},
    {"id": 7655004, "parentId2": 0, m.ORDER_DEAL_ID: "7654321", "opportunity": 3333331.07, "currencyId": "EUR",
     "stageId": "DT172_26:NEW", "createdTime": t("2025-06-03"), "companyId": 7650101},
    {"id": 7655005, "parentId2": 7654399, m.ORDER_DEAL_ID: "7654399", "opportunity": 2645751.31, "currencyId": "EUR",
     "stageId": "DT172_26:NEW", "createdTime": t("2025-06-04"), "companyId": 7650101},
]
СПЕЦ = {
    1174: [{"id": 7656001, "parentId2": 7654321, "parentId172": 0, "companyId": 7650001, "opportunity": 2408913.03,
            "currencyId": "EUR", "ufCrm80_1778582123612": 11, "stageId": "DT1174_108:NEW"}],
    132: [{"id": 7656002, "parentId2": 7654322, "parentId172": 7655001, "companyId": 7650101, "opportunity": 0,
           "currencyId": "", m.SPEC132_TYPE: 22, m.SPEC132_SUM: 3141592.65, "stageId": "DT132_10:NEW"}],
}
ПОЛЯ = {
    1174: {"ufCrm80_1778582123612": {"type": "enumeration", "title": "Тип документа",
                                     "items": [{"ID": 11, "VALUE": "Продажа покупателю"}, {"ID": 12, "VALUE": "Закупка у поставщика"}]}},
    132: {m.SPEC132_TYPE: {"type": "enumeration", "title": " Contract type",
                           "items": [{"ID": 21, "VALUE": "Customer"}, {"ID": 22, "VALUE": "Supplier"}]},
          m.SPEC132_SUM: {"type": "double", "title": "Summ of Specification"}},
    137: {m.INV_RUB: {"type": "double", "title": "Сумма в рублях"}, m.INV_AMOUNT: {"type": "double", "title": "Сумма"},
          m.INV_DATE: {"type": "date", "title": "Дата счета"},
          m.INV_PAYER: {"type": "enumeration", "title": "Юр. лицо", "items": [{"ID": 1, "VALUE": "ООО «Квант»"}]},
          m.INV_PAYEE: {"type": "string", "title": "Кому платим"},
          m.INV_TYPE: {"type": "enumeration", "title": "Тип оплаты", "items": [{"ID": 5, "VALUE": "Оплата поставщику"}]},
          m.INV_WHAT: {"type": "enumeration", "title": "За что", "items": [{"ID": 6, "VALUE": "Товар"}]},
          m.INV_DEALS: {"type": "crm", "title": "Сделки"}},
    1074: {"ufCrm1_1": {"type": "double", "title": "Курс отчета"}},
    1136: {},
}
СЧЕТА = [
    {"id": 7657001, "parentId2": 7654322, "stageId": "DT137_34:SUCCESS", "createdTime": t("2026-03-05"),
     m.INV_RUB: 1570796.33, m.INV_AMOUNT: 1570796.33, m.INV_DATE: "2026-03-05", m.INV_PAYER: 1,
     m.INV_PAYEE: "ООО Поставщик Альфа", m.INV_TYPE: 5, m.INV_WHAT: 6},
    {"id": 7657002, "parentId2": 7654322, "stageId": "DT137_34:SUCCESS", "createdTime": t("2026-03-06"),
     m.INV_RUB: 7071067.81, m.INV_DATE: "2026-03-06", m.INV_PAYER: 1, m.INV_PAYEE: "Квант ООО"},
    {"id": 7657003, "parentId2": 0, "parentId172": 7655001, "stageId": "DT137_34:NEW", "createdTime": t("2026-03-07"),
     m.INV_RUB: 2236067.97, m.INV_DATE: "2026-03-07", m.INV_PAYER: 1, m.INV_PAYEE: "ООО Бета",
     m.INV_DEALS: ["D_7654322", "D_7654321"]},
    # в работе: в «оплаченные и в работе», не в «оплаченные»; «Дата счета» далеко от заведения
    {"id": 7657004, "parentId2": 7654322, "stageId": "DT137_34:NEW", "createdTime": t("2026-03-08"),
     m.INV_RUB: 1732050.81, m.INV_DATE: "2026-05-08", m.INV_PAYER: 1, m.INV_PAYEE: "ООО Поставщик Альфа"},
]
# закупка в книге — расход со знаком минус, как в data/budget_snapshot.json (233 из 236 книг)
БЮДЖЕТЫ = ({901: {"revenue": 8642097.53, "revenue_net": 8642097.53 / 1.22, "purchase": -3141592.65,
                  "purchase_net": -3141592.65 / 1.22, "drive_id": "книга-1", "modified": "2026-01-01"}}, {}, "2026-08-06")


class Фейк:
    """Портал из словарей: только методы, которыми пользуется зонд."""

    def __init__(self, падать_на=None):
        self.падать_на = падать_на
        self.фильтры_сделок: list[dict] = []
        self.истории: list[dict] = []

    def _сделки(self, f):
        out = []
        for d in СДЕЛКИ + ПРЕСЕЙЛ:
            if "CATEGORY_ID" in f and str(d["CATEGORY_ID"]) != str(f["CATEGORY_ID"]):
                continue
            if "!CATEGORY_ID" in f and str(d["CATEGORY_ID"]) == str(f["!CATEGORY_ID"]):
                continue
            if "!STAGE_SEMANTIC_ID" in f and d["STAGE_SEMANTIC_ID"] == f["!STAGE_SEMANTIC_ID"]:
                continue
            if "@COMPANY_ID" in f and int(d["COMPANY_ID"]) not in f["@COMPANY_ID"]:
                continue
            if "@ID" in f and int(d["ID"]) not in f["@ID"]:
                continue
            if ">ID" in f and int(d["ID"]) <= int(f[">ID"]):
                continue
            if ">=DATE_CREATE" in f and d["DATE_CREATE"][:10] < f[">=DATE_CREATE"][:10]:
                continue
            if "<=DATE_CREATE" in f and d["DATE_CREATE"][:10] > f["<=DATE_CREATE"][:10]:
                continue
            out.append(d)
        return out

    def _items(self, ent, f):
        src = {172: ЗАКАЗЫ, 137: СЧЕТА, 31: [], 1074: [], 1136: []}.get(ent) or СПЕЦ.get(ent, [])
        out = []
        for it in src:
            if "@parentId2" in f and int(it.get("parentId2") or 0) not in f["@parentId2"]:
                continue
            if ">id" in f and int(it["id"]) <= int(f[">id"]):
                continue
            out.append(it)
        return out

    def call(self, method, params=None):
        params = params or {}
        if method == self.падать_на:
            raise ValueError("сделка 7654321 на 9876543.21 € — текст ошибки не должен попасть в журнал")
        if method == "crm.currency.list":
            return [{"CURRENCY": "EUR", "BASE": "Y", "AMOUNT": 1, "AMOUNT_CNT": 1, "DATE_UPDATE": "2025-03-26"},
                    {"CURRENCY": "RUB", "AMOUNT": "0.0099", "AMOUNT_CNT": 1, "DATE_UPDATE": "2025-03-26"},
                    {"CURRENCY": "USD", "AMOUNT": "0.9", "AMOUNT_CNT": 1, "DATE_UPDATE": "2025-03-26"},
                    {"CURRENCY": "AED", "AMOUNT": "0.25", "AMOUNT_CNT": 1, "DATE_UPDATE": "2023-12-29"}]
        if method == "crm.item.fields":
            return {"fields": ПОЛЯ.get(params["entityTypeId"], {})}
        if method == "batch":
            рез, всего = {}, {}
            for k in params["cmd"]:
                if k.startswith("d"):
                    рез[k] = ([{"PRICE": 122.0, "PRICE_EXCLUSIVE": 100.0, "QUANTITY": 3, "TAX_RATE": 22,
                                "TAX_INCLUDED": "Y"}] if k == "d7654322" else [])
                else:
                    рез[k] = {"productRows": [{"id": 1, "price": 122.0, "priceExclusive": 100.0, "quantity": 2,
                                               "taxRate": 22, "taxIncluded": "Y"}]} if k == "o7655001" else {"productRows": []}
                    всего[k] = len(рез[k]["productRows"])
            return {"result": рез, "result_error": {}, "result_total": всего}
        if method == "crm.requisite.preset.list":
            return [{"ID": "1", "COUNTRY_ID": "1"}, {"ID": "5", "COUNTRY_ID": "122"}]
        if method == "crm.item.payment.list":
            return []
        if method == "crm.requisite.list":
            f = params["filter"]
            r = [{"ID": "41", "ENTITY_ID": "7650001", "PRESET_ID": "1", "RQ_INN": "1239876543"},
                 {"ID": "42", "ENTITY_ID": "7650003", "PRESET_ID": "5", "RQ_INN": ""}]
            return [x for x in r if int(x["ENTITY_ID"]) in f["@ENTITY_ID"] and int(x["ID"]) > int(f[">ID"])]
        if method == "crm.deal.list":
            return self._сделки(params.get("filter") or {})
        raise AssertionError(f"неожиданный метод {method}")

    def call_envelope(self, method, params=None):
        params = params or {}
        if method == self.падать_на:
            raise ValueError("сделка 7654321 — текст ошибки не должен попасть в журнал")
        f = params.get("filter") or {}
        if method == "crm.deal.list":
            r = self._сделки(f)
            return {"result": r, "total": len(r)}
        if method == "crm.item.list":
            r = self._items(params["entityTypeId"], f)
            return {"result": {"items": r}, "total": len(r)}
        if method == "crm.stagehistory.list":
            self.истории.append(f)
            r = [x for x in ИСТОРИЯ if int(x["OWNER_ID"]) in f["OWNER_ID"]
                 and ("STAGE_ID" not in f or x["STAGE_ID"] in f["STAGE_ID"])
                 and ("CATEGORY_ID" not in f or m.pc.воронка_стадии(x["STAGE_ID"]) == str(f["CATEGORY_ID"]))]
            return {"result": {"items": r}, "total": len(r)}
        if method == "crm.quote.list":
            return {"result": [], "total": 0}
        if method == "crm.invoice.list":
            raise m.bc.BitrixError("crm.invoice.list: ERROR_METHOD_NOT_FOUND")
        raise AssertionError(f"неожиданный метод {method}")

    def list_deals_fast(self, *, filter=None, select=None, max_items=None):
        self.фильтры_сделок.append(dict(filter or {}, max_items=max_items))
        r = self._сделки(filter or {})
        return r[:max_items] if max_items else r

    def list_items(self, entity, *, filter=None, select=None):
        return self._items(entity, filter or {})

    def list_paged(self, method, params=None):
        return self.call_envelope(method, params).get("result") or []

    def deal_stage_meta(self):
        return МЕТА

    def deals_by_ids(self, ids, *, select=None):
        return {str(d["ID"]): d for d in self._сделки({"@ID": [int(x) for x in ids]})}

    def spa_semantics(self, entity, items):
        return {str(it.get("stageId")): ("S" if str(it.get("stageId")).endswith("SUCCESS") else "P") for it in items}


РЯДЫ = {"EUR": [(D(2023, 1, 1), 100.0), (D(2026, 1, 1), 100.0)],
        "AED": [(D(2023, 1, 1), 100 / 4.1), (D(2026, 1, 1), 100 / 4.1)],
        "USD": [(D(2023, 1, 1), 100 / 1.15), (D(2026, 1, 1), 100 / 1.15)]}


@pytest.fixture
def портал(monkeypatch):
    monkeypatch.setattr(fx, "cbr_codes", lambda timeout=60: {"EUR": "R01239", "AED": "R01230", "USD": "R01235"})
    коды = {"R01239": "EUR", "R01230": "AED", "R01235": "USD"}
    monkeypatch.setattr(fx, "cbr_series", lambda sid, d1, d2, timeout=60: list(РЯДЫ[коды[sid]]))
    monkeypatch.setattr(fx, "ecb_history", lambda timeout=60: {"USD": [(D(2026, 1, 5), 1.15)]})

    def зеркала_нет(timeout=30, fetch=None):
        raise ConnectionError("https://www.cbr-xml-daily.ru — 7654321")
    monkeypatch.setattr(fx, "cbr_mirror_codes", зеркала_нет)
    monkeypatch.setattr(ct, "_load_budget_plans", lambda: БЮДЖЕТЫ)
    monkeypatch.delenv("PROBE_PARTS", raising=False)


def формы(x: float) -> list[str]:
    n = int(x)
    return [str(n), f"{n:,}", f"{n:,}".replace(",", " "), f"{n:,}".replace(",", " "), f"{x:.2f}",
            f"{x:.2f}".replace(".", ","), f"€{round(x / 1000)}K", f"€{x / 1e6:.2f}M", f"{round(x / 1000)}K"]


def без_утечек(out: str) -> None:
    for x in СУММЫ:
        for s in формы(x):
            assert s not in out, (x, s)
    for n in НОМЕРА:
        assert n not in out, n
    for слово in ("Учебн", "Альфа", "Бета", "Квант", "Омега", "Customer", "покупателю", "текст ошибки", "Basra",
                  "cbr.ru", "cbr-xml", "http"):
        assert слово not in out, слово


# ── журнал целиком ──────────────────────────────────────────────────────────

def test_журнал_без_номеров_и_сумм(портал, capsys):
    код = m.main(Фейк(), СЕГОДНЯ)
    out = capsys.readouterr().out
    без_утечек(out)
    строки = out.splitlines()
    assert строки[0].startswith("ЦБ РФ доступен")
    assert строки[-1].startswith("портал: запросов")          # сводка_нагрузки() — последней
    assert код == 0
    assert "частей прочитано 5 из 5" in out
    assert "U1 3 · U2 1 · U3 1" in out                         # Basra и старый успех — вне выборки
    assert "воронка 16 и её компании (отдельный сегмент, вне сравнений): U1 1" in out
    # дата курса: поле, последнее КП до кат. 0, близнец
    assert "ТКП согласован 3 · последнее КП до выигрыша 1 · КП близнеца в пресейле 1" in out
    # гипотеза владельца: число в AED под валютой EUR — одна сделка, по спецификации
    assert "в полосе курса AED/EUR на окне дат ТКП: 1 из 1 (спецификация 1)" in out
    # «Сумма» RUB-сделки — с НДС бюджета; покупатель — из реквизитов
    assert "RUB · покупатель РФ: ≈ с НДС 1" in out
    # счета СП-137: внутригрупповой и общий — не в сумме
    assert "внутригрупповые (не в сумме) 1" in out and "общие на несколько сделок (не в сумме) 1" in out
    # заказы: сирота с «ID сделки», родитель в тестовой воронке, заказ без суммы
    assert "сирот без parentId2 1, из них с «ID сделки» 1 (на сделку выборки 1)" in out
    assert "тестовые 22/26/28 1" in out
    assert "неполные (есть живой без суммы) 1" in out
    assert "старые счета crm.invoice: недоступно (BitrixError)" in out
    # тень каскада: «Сумма» в AED уходит из итога «под сомнением»; одно множество — без невязки
    assert "продажа: Битрикс по ЦБ 2 · под сомнением 1" in out
    # сверка тени — по всем её строкам: продажа без закупки даёт невязку, а не ноль по построению
    assert ("сверка итогов тени (те же множества): в Σ продажи 2, в Σ закупки 1, в Σ маржи 1; "
            "продажа∩закупка 1, во всех трёх 1; невязка +98.2 %") in out
    # закупка бюджета — по модулю: пара заказы/бюджет не в «ноль/минус», разрезы C21/D23 не пусты
    assert "BO_kp–BBud_gross (заказы против бюджета с НДС): обе есть 1 (U1 1) · ≤5 % 1" in out
    assert "закупка в книге со знаком: минус 1 (берётся по модулю" in out
    assert "по валюте заказов (C21): RUB: n 1" in out
    # счета СП-137: оплаченные и в работе — к заказам, только оплаченные — к доле «Оплачено»
    assert "BPaid–BO (счета СП-137 оплаченные и в работе против заказов): обе есть 1 (U1 1) · 5–10 % 1" in out
    assert "BPaid_done–BOpaid (оплаченные счета СП-137 против заказов × «Оплачено поставщику, %»): обе есть 1" in out
    assert "оплачен (S) 1" in out and "в работе (P) 1" in out and "«Дата счета» дальше 7 дн. от заведения 1" in out
    assert "из них в день создания сделки (±1 дн., похоже на умолчание) 1 из 3" in out
    assert "по воронке заказов (D23): воронка 26: n 1" in out
    assert "НАГРУЗКА ПО БЛОКАМ" in out


def test_цб_недоступен_первой_строкой(портал, monkeypatch, capsys):
    def нет(timeout=60):
        raise ConnectionError("https://www.cbr.ru — 7654321")
    monkeypatch.setattr(fx, "cbr_codes", нет)
    m.main(Фейк(), СЕГОДНЯ)
    out = capsys.readouterr().out
    без_утечек(out)
    assert out.splitlines()[0].startswith("ЦБ РФ недоступен с раннера (ConnectionError)")
    assert "1:1 не подставляется" in out.splitlines()[0]
    assert "SB–SC" not in out and "SC0–SC" not in out           # колонок «по ЦБ» нет
    assert "курс Битрикса (ЦБ недоступен)" in out


def test_сбой_части_печатает_только_тип_и_место(портал, capsys):
    код = m.main(Фейк(падать_на="batch"), СЕГОДНЯ)
    out = capsys.readouterr().out
    без_утечек(out)
    assert "сбой ValueError" in out and "probe_money_sources.py:" in out
    assert "частей прочитано 0 из 5" in out
    assert "изменения Σ и сверка итогов не печатаются" in out
    assert код == 1


# ── чистые функции ──────────────────────────────────────────────────────────

def test_корзины_симметричны_по_логарифму():
    assert m.корзина_пары(105, 100) == m.корзина_пары(100, 105) == "≤5 %"
    assert m.корзина_пары(108, 100) == "5–10 %"
    assert m.корзина_пары(100, 115) == "10–20 %"
    assert m.корзина_пары(130, 100) == ">20 %"
    assert m.корзина_пары(0, 100) == m.корзина_пары(-5, 100) == m.корзина_пары(100, 0) == "ноль/минус"
    assert m.корзина_пары(None, 100) is None


def test_объяснители():
    без = {}
    assert m.объяснить(1.20, без, (1.20,)) == "НДС"
    assert m.объяснить(1 / 1.22, без, (1.22,)) == "НДС"
    assert m.объяснить(1.22, без, (1.20,)) == "прочее"           # ставка не по дате отгрузки
    aed = {"AED/EUR": (4.0, 4.2)}
    assert m.объяснить(4.1, aed, (1.20,)) == "курс"
    assert m.объяснить(1 / 4.1, aed, (1.20,)) == "курс"
    assert m.объяснить(4.1 * 1.20, aed, (1.20,)) == "курс × НДС"
    assert m.объяснить(4.1, без, (1.20,)) == "больше"            # курс чужой валюты не объясняет
    assert m.объяснить(1000.5, без, (1.20,)) == "×1000"
    assert m.объяснить(0.5, без, (1.20,)) == "часть"
    assert m.объяснить(0.09, без, (1.20,)) == "прочее"
    assert m.объяснить(3.0, без, (1.20,)) == "больше"
    assert m.объяснить(20.0, без, (1.20,)) == "прочее"
    # USD около 1,20 неотличим от НДС (критика C22)
    assert m.объяснить(1.20, {"EUR/USD": (1.19, 1.21)}, (1.20,)) == "неоднозначно"


def test_ставка_ндс_по_дате_отгрузки():
    assert m.ставки_ндс(None) == (1.20, 1.22)
    assert m.ставки_ндс(D(2025, 12, 31)) == (1.20,)
    assert m.ставки_ндс(D(2026, 1, 1)) == (1.22,)


def test_дата_курса_каскад_владельца():
    с_полем = {m.TKP_FINAL: "2025-05-20T23:30:00+00:00", "DATE_CREATE": t("2025-01-01")}
    assert m.дата_ткп(с_полем, [], None, None) == (D(2025, 5, 21), "ТКП согласован")   # по Москве
    без = {"DATE_CREATE": t("2025-01-01")}
    кп = [D(2025, 1, 10), D(2025, 2, 1), D(2025, 4, 1)]
    assert m.дата_ткп(без, кп, [D(2025, 3, 1), D(2025, 5, 1)], None) == (D(2025, 2, 1), "последнее КП до выигрыша")
    assert m.дата_ткп(без, [], None, D(2024, 12, 1)) == (D(2024, 12, 1), "КП близнеца в пресейле")
    assert m.дата_ткп(без, [], [D(2025, 3, 1), D(2025, 6, 1)], None) == (D(2025, 3, 1), "вход в кат. 0")
    assert m.дата_ткп(без, [], None, None) == (D(2025, 1, 1), "создание сделки")


def test_выигрыш_у_сделки_заведённой_в_кат0():
    """Заведена в кат. 0, ушла в пресейл, выиграна обратно: первый вход в кат. 0 — создание,
    а не выигрыш; КП до настоящего выигрыша не отбрасываются (ревью v59)."""
    без = {"DATE_CREATE": t("2024-12-01")}
    кп = [D(2024, 12, 20), D(2025, 1, 15), D(2025, 3, 1)]          # 03-01 — ревизия после выигрыша
    входы = [D(2024, 12, 1), D(2025, 2, 1), D(2025, 2, 10)]
    assert m.выигрыш(кп, входы) == D(2025, 2, 1)
    assert m.дата_ткп(без, кп, входы, None) == (D(2025, 1, 15), "последнее КП до выигрыша")
    assert m.выигрыш([], входы) == D(2025, 2, 10)
    assert m.выигрыш(кп, None) is None
    # все входы в кат. 0 раньше первого КП — выигрыш последний вход, КП после него не берутся
    assert m.дата_ткп(без, [D(2025, 4, 1)], [D(2025, 1, 1), D(2025, 2, 1)], None) == (D(2025, 1, 1), "вход в кат. 0")
    assert m.окно_дат(D(2025, 5, 1), [D(2025, 1, 10), D(2025, 6, 1)], D(2025, 2, 1)) == (D(2025, 1, 10), D(2025, 5, 1))


def test_ряды_цб_и_окна():
    r = fx.CbrRates({"EUR": [(D(2024, 1, 10), 100.0), (D(2024, 2, 1), 110.0)], "AED": [(D(2024, 1, 10), 25.0)]})
    assert r.value("EUR", D(2024, 1, 9)) is None                  # раньше первой записи — курса нет
    assert r.value("EUR", D(2024, 1, 31)) == 100.0
    assert r.value("RUB", D(2020, 1, 1)) == 1.0
    assert r.cross("AED", "EUR", D(2024, 1, 15)) == pytest.approx(4.0)
    assert r.to_eur(400, "AED", D(2024, 1, 15)) == pytest.approx(100)
    assert r.cross_range("AED", "EUR", D(2024, 1, 15), D(2024, 3, 1)) == pytest.approx((4.0, 4.4))
    assert r.to_eur(1, "XYZ", D(2024, 1, 15)) is None
    assert m.окна_курсов(r, {"AED"}, D(2024, 1, 15), D(2024, 3, 1)) == {"AED/EUR": pytest.approx((4.0, 4.4))}


def test_разбор_xml_цб():
    lib = ('<?xml version="1.0" encoding="windows-1251"?><Valuta>'
           '<Item ID="R01804"><ISO_Char_Code>RSD</ISO_Char_Code><ParentCode>R01804    </ParentCode></Item>'
           '<Item ID="R01805F"><ISO_Char_Code>RSD</ISO_Char_Code><ParentCode>R01804    </ParentCode></Item>'
           '<Item ID="R01239"><ISO_Char_Code>EUR</ISO_Char_Code><ParentCode>R01239    </ParentCode></Item>'
           '</Valuta>').encode("windows-1251")
    assert fx.parse_cbr_codes(lib) == {"RSD": "R01804", "EUR": "R01239"}
    dyn = ('<?xml version="1.0" encoding="windows-1251"?><ValCurs>'
           '<Record Date="01.10.2026" Id="R01805F"><Nominal>100</Nominal><Value>80,6032</Value></Record>'
           '<Record Date="02.10.2026" Id="R01805F"><Nominal>100</Nominal><Value>80,1</Value>'
           '<VunitRate>0,801</VunitRate></Record></ValCurs>').encode("windows-1251")
    assert fx.parse_cbr_series(dyn) == [(D(2026, 10, 1), pytest.approx(0.806032)), (D(2026, 10, 2), 0.801)]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("eurofxref-hist.csv", "Date,USD,RUB,\n2026-10-23,1.16,N/A,\n2026-10-22,1.15,N/A,\n")
    assert fx.parse_ecb_history(buf.getvalue()) == {"USD": [(D(2026, 10, 22), 1.15), (D(2026, 10, 23), 1.16)]}


def test_прежний_fx_не_тронут(tmp_path):
    import sqlite3
    con = sqlite3.connect(tmp_path / "fx.db")
    fx.ensure(con)
    con.executemany("INSERT INTO fx_rates VALUES (?,?,?)", [("2025-01-10", "EUR", 100.0), ("2025-01-10", "USD", 90.0),
                                                            ("2025-01-10", "RUB", 1.0)])
    fx._MEM.clear()
    assert fx.to_eur(con, 100, "USD", "2025-01-10") == pytest.approx(90.0)
    assert fx.to_eur(con, 100, None, "2025-01-10") is None


def test_свои_юрлица_и_стороны():
    свои = {m._имя_лица("ООО «Квант»")}
    assert m.свой_получатель("Квант ООО", свои)
    assert not m.свой_получатель("ООО Поставщик Альфа", свои)
    assert not m.свой_получатель("", свои)
    подписи = {"11": "Продажа покупателю", "12": "Закупка у поставщика"}
    assert m.сторона_спецификации({"t": 11}, "t", подписи, "1") == ("заказчик", "тип")
    assert m.сторона_спецификации({"t": 12}, "t", подписи, "1") == ("поставщик", "тип")
    assert m.сторона_спецификации({"parentId172": 5}, None, {}, "1") == ("поставщик", "связь с заказом")
    assert m.сторона_спецификации({"companyId": 1}, None, {}, "1") == ("заказчик", "компания сделки")
    assert m.сторона_спецификации({"companyId": 2}, None, {}, "1") == ("?", "не определена")
    assert m.сумма_спецификации({"opportunity": 0, m.SPEC132_SUM: 10}, 132, "RUB") == (10.0, "RUB", "Summ of Specification, валюта сделки")
    assert m.вид_платежа("Оплата таможни") == "таможня"


def test_страна_покупателя_по_реквизитам():
    пресеты = {"1": "1", "5": "122"}
    р = {7: [{"PRESET_ID": "5", "RQ_INN": ""}, {"PRESET_ID": "1", "RQ_INN": ""}],   # хоть один российский
         8: [{"PRESET_ID": "5", "RQ_INN": "1239876543"}],                          # шаблон другой страны
         9: [{"PRESET_ID": "", "RQ_INN": "1239876543"}],                           # без шаблона — по ИНН
         10: [{"PRESET_ID": "", "RQ_INN": "1239876540"}]}                          # ИНН с неверной суммой
    assert m.страна_по_реквизитам(р, пресеты) == {"7": "РФ", "8": "не РФ", "9": "РФ", "10": "не РФ"}


def test_дубли_и_строки():
    з = [{"id": 1, "companyId": 9, "opportunity": 100.0}, {"id": 2, "companyId": 9, "opportunity": 100.5},
         {"id": 3, "companyId": 8, "opportunity": 100.0}]
    assert m.дубли_заказов(з) == {"1", "2"}
    с = m.свод_строк([{"PRICE": 122, "PRICE_EXCLUSIVE": 100, "QUANTITY": 2, "TAX_RATE": 22, "TAX_INCLUDED": "Y"}])
    assert (с["net"], с["gross"], с["incl"]) == (200, 244, 1)
    assert m.свод_строк([]) is None


def test_фон_случайных_и_малые_выборки():
    assert m.фон_случайных([(1, 2, {}, (1.2,))] * 4, random.Random(1)) is None
    ряды = [(1.2 * b, b, {}, (1.2,)) for b in (10, 20, 40, 80, 160, 320)]
    фон = m.фон_случайных(ряды, random.Random(1))
    assert фон is not None and 0 <= фон <= 1


def test_сверка_итогов():
    # нынешние правила: продажа без закупки в Σ продажи, закупка без продажи в Σ закупки
    строки = {"a": {"sale": 100.0, "buy": 60.0, "src": "orders", "susp": False, "margin": 40.0},
              "b": {"sale": 50.0, "buy": 0.0, "src": "none", "susp": False, "margin": None},
              "c": {"sale": 0.0, "buy": 30.0, "src": "orders", "susp": False, "margin": None}}
    с = m.сверка_итогов(строки)
    assert (с["S"], с["B"], с["M"], с["SB"], с["SBM"]) == (2, 2, 1, 1, 1)
    assert с["невязка"] == pytest.approx((150 - 90 - 40) / 150 * 100)
    один = {"a": строки["a"]}
    assert m.сверка_итогов(один)["невязка"] == pytest.approx(0)


def test_сдвиг_показанной_продажи():
    assert m.корзина_сдвига(100.5, 100) == "≤1 %"
    assert m.корзина_сдвига(130, 100) == "10–50 % вверх"
    assert m.корзина_сдвига(25, 100) == ">50 % вниз"


def test_история_проверяет_фильтр_стадий():
    class Портал:
        def call_envelope(self, method, params):
            return {"result": {"items": [{"OWNER_ID": "1", "STAGE_ID": "C2:NEW", "CREATED_TIME": t("2025-01-01")}]},
                    "total": 1}
    _ист, ош = m.pc.читать_историю(Портал(), ["1"], стадии={"C2:UC_TKP"})
    assert ош and "STAGE_ID" in ош
    _ист, ош = m.pc.читать_историю(Портал(), ["1"], категория=0)
    assert ош and "CATEGORY_ID" in ош
    ист, ош = m.pc.читать_историю(Портал(), ["1"])
    assert ош is None and ист == {"1": [("C2:NEW", t("2025-01-01"))]}


def test_части_и_прогон():
    wf = yaml.safe_load((ROOT / ".github/workflows/probe.yml").read_text(encoding="utf-8"))
    вход = (wf.get("on") or wf[True])["workflow_dispatch"]["inputs"]
    assert "probe_money_sources.py" in вход["script"]["options"]
    assert set(вход["parts"]["options"]) >= {"10", "25", "50"}
    шаг = next(s for s in wf["jobs"]["probe"]["steps"] if s.get("name") == "Зонд")
    assert "PROBE_PARTS" in шаг["env"] and шаг["env"]["BITRIX_RPS"] == "1.0"
    assert wf["concurrency"]["group"] == "bitrix-portal"


def test_части_не_меньше_десяти(monkeypatch, capsys):
    monkeypatch.setenv("PROBE_PARTS", "3")
    assert m._частей() == 10
    monkeypatch.setenv("PROBE_PARTS", "25")
    assert m._частей() == 25
    assert re.search(r"меньше 10", capsys.readouterr().out)


# ── ревью v59 ───────────────────────────────────────────────────────────────

def test_закупка_бюджета_по_модулю_и_оплаты_всё_или_ничего():
    assert m.закупка_бюджета({"purchase_net": -1000.0}, "purchase_net") == 1000.0
    assert m.закупка_бюджета({"purchase": 0}, "purchase") is None
    assert m.закупка_бюджета(None, "purchase") is None
    К = fx.CbrRates({"EUR": [(D(2026, 1, 1), 100.0)]})
    assert m.сумма_оплат(К, [(1000.0, D(2026, 2, 1), "S"), (500.0, D(2026, 3, 1), "P")]) == pytest.approx(15.0)
    # курса нет хоть у одного — суммы нет, а не тихий недосчёт
    assert m.сумма_оплат(К, [(1000.0, D(2026, 2, 1), "S"), (500.0, D(2025, 3, 1), "P")]) is None
    assert m.сумма_оплат(К, [(1000.0, None, "S")]) is None
    assert m.сумма_оплат(К, []) is None


def test_бюджет_и_по_номеру_первого_заказа(monkeypatch):
    """Номер только в названии заказа — бюджет привязан, как в contracts.compute (:377)."""
    книга = {"revenue": 10.0, "purchase": -5.0, "drive_id": "к"}
    monkeypatch.setattr(ct, "_load_budget_plans", lambda: ({902: книга}, {}, "2026-08-06"))
    п = m.Прогон(None, СЕГОДНЯ)
    п.прочитанные = {"1", "2"}
    п.сделки = {"1": {"TITLE": "Поставка без номера"}, "2": {"TITLE": "Ещё без номера"}}
    п.ctx["заказы"]["1"] = [{"title": "902/1. Заказ"}, {"title": "777/1. Чужой"}]
    m.шаг_бюджеты(п)
    assert п.ctx["бюджет"] == {"1": книга}
    assert п.знак_закупки == {"минус": 1}


def test_близнецы_в_окне_дат_и_в_смете(портал, monkeypatch, capsys):
    ф = Фейк()
    m.main(ф, СЕГОДНЯ)
    capsys.readouterr()
    f = next(x for x in ф.фильтры_сделок if "@COMPANY_ID" in x)
    # карточка без КП создана 2025-10-01: пресейл не раньше 2 × TWIN_DAYS до неё и не позже её
    assert f[">=DATE_CREATE"] == (D(2025, 10, 1) - dt.timedelta(days=2 * m.pc.TWIN_DAYS)).isoformat()
    assert f["<=DATE_CREATE"] == "2025-10-02" and f["max_items"] == m.БЛИЗНЕЦОВ_МАКС
    assert any(x.get("OWNER_ID") == [7654390] for x in ф.истории)      # история близнеца читалась
    # смета не пускает историю близнецов — история пресейла не читается вовсе
    monkeypatch.setattr(m.Учёт, "можно", lambda self, имя, оценка: имя != "история близнецов")
    ф = Фейк()
    m.main(ф, СЕГОДНЯ)
    out = capsys.readouterr().out
    assert not any(x.get("OWNER_ID") == [7654390] for x in ф.истории)
    assert "близнец найден 0" in out and "КП близнеца в пресейле" not in out


def test_счета_137_на_дату_заведения(портал, capsys):
    """«₽ по ЦБ» посчитан на дату заведения процесса — делится на EUR той же даты, а не
    «Дата счета»; семантика сохраняется для «только оплаченных» (ревью v59)."""
    п = m.Прогон(Фейк(), СЕГОДНЯ)
    п.заказы_все, п.ждём_137, п.прочитанные = ЗАКАЗЫ, len(СЧЕТА), {"7654322"}
    m.шаг_счета_137(п)
    capsys.readouterr()
    assert sorted(п.ctx["оплаты"]["7654322"]) == [(1570796.33, D(2026, 3, 5), "S"), (1732050.81, D(2026, 3, 8), "P")]


def test_валюта_поля_только_кодом(портал, monkeypatch, capsys):
    """Текст после «|» в money-поле не уходит в журнал — только счётчик (ревью приватности v59)."""
    monkeypatch.setitem(СДЕЛКИ[1], ct.BUY_ECON_VAL, "123.0|ООО Омега 7654321")
    monkeypatch.setitem(СПЕЦ[1174][0], "currencyId", "ооо омега")
    m.main(Фейк(), СЕГОДНЯ)
    out = capsys.readouterr().out
    без_утечек(out)
    assert "ОМЕГА" not in out.upper()
    assert "нестандартных кодов валют в суммах выборки: 2" in out
    assert m.код_валюты(" eur ") == "EUR" and m.код_валюты("EURO") is None and m.код_валюты("Е1R") is None
    assert m.вал_подпись("") == "пусто" and m.вал_подпись("ООО 1") == "нестандартный код"


def _ответ_зеркала(q: dt.date) -> bytes:
    import json
    return json.dumps({"Date": f"{q.isoformat()}T11:30:00+03:00", "Valute": {
        "EUR": {"CharCode": "EUR", "Nominal": 1, "Value": 100.0},
        "USD": {"CharCode": "USD", "Nominal": 1, "Value": 100 / 1.15},
        "AED": {"CharCode": "AED", "Nominal": 10, "Value": 10 * 100 / 4.1}}}).encode()


def _зеркало(url: str):
    if url == fx.CBR_MIRROR_LATEST:
        return 200, _ответ_зеркала(СЕГОДНЯ)
    y, mo, d = (int(x) for x in re.search(r"archive/(\d{4})/(\d{2})/(\d{2})/", url).groups())
    q = dt.date(y, mo, d)
    return (404, b"") if q.weekday() >= 5 else (200, _ответ_зеркала(q))


def test_зеркало_цб_шаг_назад_номинал_и_предел(monkeypatch):
    adr = fx.parse_cbr_mirror_day(_ответ_зеркала(D(2026, 10, 23)))
    assert adr[0] == D(2026, 10, 23) and adr[1]["AED"] == pytest.approx(100 / 4.1)   # номинал 10 учтён
    запросы = []

    def get(url):
        запросы.append(url)
        return _зеркало(url)
    # воскресенье 25.10 → 404, суббота 24.10 → 404, пятница 23.10 — курс; повтор пятницы — из кеша
    ряды, стат = fx.cbr_mirror_series({D(2026, 10, 25), D(2026, 10, 23)}, {"EUR", "AED", "RUB"}, pause=0, fetch=get)
    assert стат == {"запросов": 3, "дат": 2, "без курса": 0, "обрезано": 0, "ошибок": 0} and len(запросы) == 3
    К = fx.CbrRates(ряды)
    assert К.cross("AED", "EUR", D(2026, 10, 25)) == pytest.approx(4.1)
    assert К.days("EUR") == [D(2026, 10, 23)]
    # предел запросов: старые даты остаются без курса, счётчиком
    _р, стат = fx.cbr_mirror_series({D(2026, 10, 21), D(2026, 10, 22), D(2026, 10, 23)}, {"EUR"},
                                     max_requests=2, pause=0, fetch=_зеркало)
    assert (стат["запросов"], стат["обрезано"]) == (2, 1)
    # 429 — подождать и повторить, а не потерять всё зеркало
    ответы = iter([(429, b""), (200, _ответ_зеркала(D(2026, 10, 23)))])
    monkeypatch.setattr(fx.time, "sleep", lambda s: None)
    ряды, стат = fx.cbr_mirror_series({D(2026, 10, 23)}, {"EUR"}, pause=0, fetch=lambda u: next(ответы))
    assert стат["запросов"] == 2 and ряды["EUR"] == [(D(2026, 10, 23), 100.0)]
    assert fx.cbr_mirror_codes(fetch=_зеркало) >= {"EUR", "AED", "RUB"}
    with pytest.raises(ConnectionError):
        fx.cbr_mirror_codes(fetch=lambda u: (403, b""))


def test_цб_недоступен_зеркало_доступно(портал, monkeypatch, capsys):
    def нет(timeout=60):
        raise ConnectionError("https://www.cbr.ru — 7654321")
    monkeypatch.setattr(fx, "cbr_codes", нет)
    monkeypatch.setattr(fx, "cbr_mirror_codes", lambda timeout=30, fetch=None: {"EUR", "USD", "AED", "RUB"})
    monkeypatch.setattr(fx, "_mirror_get", lambda url, timeout: _зеркало(url))
    monkeypatch.setattr(fx.time, "sleep", lambda s: None)
    код = m.main(Фейк(), СЕГОДНЯ)
    out = capsys.readouterr().out
    без_утечек(out)
    assert out.splitlines()[0].startswith("ЦБ РФ недоступен с раннера (ConnectionError) — курсы берутся с зеркала")
    assert "1:1 не подставляется" in out.splitlines()[0]
    assert "курсы ЦБ по зеркалу: нужных дат" in out and "€ — ЦБ на дату ТКП (зеркало)" in out
    assert "SB–SC (Сумма: курс Битрикса против ЦБ на дату ТКП): обе есть 5" in out
    assert код == 0
    # справочник cbr.ru ответил, а ряд EUR — нет: тоже зеркало, а не 1:1
    monkeypatch.setattr(fx, "cbr_codes", lambda timeout=60: {"EUR": "R01239", "AED": "R01230", "USD": "R01235"})

    def ряда_нет(sid, d1, d2, timeout=60):
        raise ConnectionError("7654321")
    monkeypatch.setattr(fx, "cbr_series", ряда_нет)
    m.main(Фейк(), СЕГОДНЯ)
    out = capsys.readouterr().out
    без_утечек(out)
    assert "ряд ЦБ EUR не получен (AED — ConnectionError" in out and "€ — ЦБ на дату ТКП (зеркало)" in out
