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
