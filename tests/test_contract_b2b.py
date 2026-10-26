"""Сверка «один к одному»: условия заказчика против условий поставщика.
Корпус придуман (CLAUDE.md, правило 18)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import contract_b2b as b2b  # noqa: E402


def коды(fl):
    return {f["code"]: f.get("days") for f in fl}


def test_все_разрывы_разом():
    заказчик = {"side": "customer", "deliveryDays": 120, "incoterms": "DDP", "prepayPct": 0, "payDaysAfter": 60,
                "penaltyPctDay": 0.1, "penaltyCapPct": None, "warrantyMonths": 24, "warrantyFrom": "ввод в эксплуатацию",
                "acceptance": "по качеству при шефмонтаже", "documents": ["ТР ТС", "перевод паспорта"], "fxFixed": True}
    поставщик = {"side": "supplier", "deliveryDays": 110, "incoterms": "EXW", "prepayPct": 30, "penaltyPctDay": 0.05,
                 "warrantyMonths": 12, "warrantyFrom": "отгрузка", "claimDays": 30, "documents": ["ТР ТС"],
                 "currency": "CNY", "cisgExcluded": False}
    к = коды(b2b.flags(заказчик, поставщик, logistics_days=19))
    assert к["lateByDesign"] == 9
    assert {"basisGap", "cashGap", "penaltyUncovered", "penaltyNoCap", "warrantyGap", "warrantyStart",
            "claimGap", "docsGap", "fxGap", "cisg"} <= set(к)
    assert к["cashGap"] == 60 and к["warrantyGap"] == 365


def test_чистая_пара_без_флагов_и_тонкий_запас():
    заказчик = {"deliveryDays": 150, "incoterms": "FCA", "prepayPct": 30, "penaltyPctDay": 0.05, "penaltyCapPct": 10,
                "warrantyMonths": 12, "warrantyFrom": "поставка", "documents": ["паспорт"]}
    поставщик = {"deliveryDays": 120, "incoterms": "FCA", "prepayPct": 30, "penaltyPctDay": 0.05, "warrantyMonths": 18,
                 "warrantyFrom": "поставка", "documents": ["паспорт"], "cisgExcluded": True}
    assert b2b.flags(заказчик, поставщик, logistics_days=19) == []
    assert коды(b2b.flags({"deliveryDays": 140}, {"deliveryDays": 120}, logistics_days=19)) == {"thinBuffer": 1}
    # пустые условия — ни одного выдуманного флага
    assert b2b.flags({}, {}) == []


# --------------------------------------------------------------------------- флаги, которые не ставились (25.10.2026)
# Живой прогон ct-40206159351-1: lateByDesign, thinBuffer, claimGap, fxGap — по 0 при 69
# сделках с парой. Причины в правилах: срок датой не сравнивался вовсе, «шеф-монтаж»
# через дефис не совпадал с «шефмонтаж», рублёвая цена без оговорки о курсе не давала
# fxFixed. Корпус придуман.
def test_срок_датой_сравнивается_с_датой():
    заказчик = {"deliveryDate": "2026-12-01"}
    assert коды(b2b.flags(заказчик, {"deliveryDate": "2026-11-20"}, logistics_days=19)) == {"lateByDesign": 8}
    assert коды(b2b.flags(заказчик, {"deliveryDate": "2026-11-08"}, logistics_days=19)) == {"thinBuffer": 4}
    assert b2b.flags(заказчик, {"deliveryDate": "2026-10-01"}, logistics_days=19) == []
    # дни против даты без даты отсчёта не сравниваются — запас не выдумывается
    assert b2b.flags({"deliveryDays": 30}, {"deliveryDate": "2026-11-20"}) == []
    assert b2b.запас_по_сроку({"deliveryDays": 30}, {"deliveryDate": "2026-11-20"}, 19) == (None, "дни против даты")
    assert b2b.запас_по_сроку({}, {"deliveryDays": 10}, 19) == (None, "нет срока")
    # дни у обеих — главнее дат
    assert b2b.запас_по_сроку({"deliveryDays": 120, "deliveryDate": "2026-01-01"},
                              {"deliveryDays": 110, "deliveryDate": "2026-12-31"}, 19) == (-9, "дни")
    # не дата — не срок
    assert b2b.flags({"deliveryDate": "до конца года"}, {"deliveryDate": "2026-11-20"}) == []


def test_поздняя_приёмка_в_любом_написании_и_срок_претензий():
    поставщик = {"claimDays": 30}
    for приёмка in ("по качеству при шеф-монтаже", "Шеф монтаж оборудования", "после пуско-наладочных работ",
                    "при вводе в эксплуатацию", "по качеству при шефмонтаже"):
        assert коды(b2b.flags({"acceptance": приёмка}, поставщик)) == {"claimGap": 30}, приёмка
    assert b2b.flags({"acceptance": "по количеству мест при поставке"}, поставщик) == []
    assert b2b.flags({"acceptance": "при шеф-монтаже"}, {}) == [], "без срока претензий поставщику флага нет"
    # отрицание рядом с признаком и голое английское слово — не поздняя приёмка
    for приёмка in ("без шеф-монтажа", "шеф-монтаж не входит в поставку", "installation not included",
                    "acceptance before installation", "до ввода в эксплуатацию", "excluding commissioning"):
        assert b2b.flags({"acceptance": приёмка}, поставщик) == [], приёмка
    for приёмка in ("acceptance upon commissioning", "after successful installation", "при монтаже"):
        assert коды(b2b.flags({"acceptance": приёмка}, поставщик)) == {"claimGap": 30}, приёмка
    # срок претензий заказчика к нам кончается позже нашего к поставщику: срок заказчика —
    # от поставки ему, на путь позже поставки нам (как у lateByDesign)
    assert коды(b2b.flags({"claimDays": 90}, {"claimDays": 30}, logistics_days=19)) == {"claimGap": 79}
    assert коды(b2b.flags({"claimDays": 30}, {"claimDays": 30}, logistics_days=19)) == {"claimGap": 19}
    assert b2b.flags({"claimDays": 30}, {"claimDays": 30}, logistics_days=0) == []
    assert b2b.flags({"claimDays": 10}, {"claimDays": 30}, logistics_days=19) == []


def test_рублёвая_цена_заказчика_при_валюте_поставщика():
    assert коды(b2b.flags({"currency": "RUB"}, {"currency": "EUR"})) == {"fxGap": None}
    assert коды(b2b.flags({"fxFixed": True}, {"currency": "CNY"})) == {"fxGap": None}
    # прямо сказано «курс не фиксирован» — флага нет; у поставщика рубли или валюты нет — нет
    assert b2b.flags({"currency": "RUB", "fxFixed": False}, {"currency": "EUR"}) == []
    assert b2b.flags({"currency": "RUB"}, {"currency": "RUB"}) == []
    assert b2b.flags({"currency": "RUB"}, {}) == []
    assert b2b.flags({"currency": "EUR"}, {"currency": "EUR"}) == []
