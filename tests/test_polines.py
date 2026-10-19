"""Линии продукт-оунеров: пресейл по «Категории оборудования» (решение владельца
19.10.2026). Корпус придуман (правило 18) — tests/fixture.ps_deals."""
from __future__ import annotations

from tests import fixture


def _lines():
    pl = fixture.build_presale()["poLines"]
    return pl, {x["cat"]: x for x in pl["lines"]}


def test_сделка_с_двумя_категориями_входит_в_обе_линии():
    pl, L = _lines()
    assert L["Насосы"]["n"] == 4 and L["Компрессоры и ГТУ"]["n"] == 2   # 8002 — в обеих
    assert pl["multi"] == 1 and pl["withCat"] == 8 and pl["deals"] == 9


def test_удалённое_значение_и_пустая_категория_видны_и_пустая_последней():
    pl, L = _lines()
    assert L["значение #77"]["lost"] == 1
    assert pl["lines"][-1]["cat"] == "категория не указана" and pl["lines"][-1]["open"] == 1


def test_стоят_только_подтверждённые_тишиной_в_чате():
    # 8003 (компрессоры) — 20 дн., в чате тихо: стоит; 8004 (насосы) — в чате работа,
    # 8006 (ЗИП) — чат недоступен, 8007 (насосы) — нет чата: не «стоят»
    _, L = _lines()
    assert L["Компрессоры и ГТУ"]["stale"] == 1
    assert L["Насосы"]["stale"] == 0 and L["ЗИП"]["stale"] == 0


def test_продукт_оунеры_и_сделки_без_po():
    _, L = _lines()
    assert sum(p["n"] for p in L["Насосы"]["pos"]) == 2 and L["Насосы"]["noPo"] == 2
    assert len(L["Компрессоры и ГТУ"]["pos"]) == 2 and L["Компрессоры и ГТУ"]["noPo"] == 0


def test_линии_едут_во_вкладку_людей():
    assert fixture.build_people()["poLines"]["lines"]
