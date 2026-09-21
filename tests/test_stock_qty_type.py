"""Остаток приходит и числом, и строкой — и это роняло два инструмента насмерть.

Оплачено 18.09.2026 дважды за один час. Поле stock_qty в сводке заявки у одних
продавцов записано числом (12), у других строкой («12 pcs»). Инструменты
обращались с ним как со строкой — срез, .strip() — и падали с TypeError либо
AttributeError на ПЕРВОЙ же такой строке. Документ при этом не собирался вовсе:
gt/tools/ship_priced.py и gt/tools/ship_rfq.py в гейт не входят, запускают их
руками и редко, поэтому поломку никто не видел.

Правило: перед строковой операцией поле приводится к строке явно. Здесь это
проверяется на придуманном корпусе со всеми тремя видами значения — числом,
строкой и пустотой.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))


def rows() -> list[dict]:
    base = {"pn": "AB-1", "name": "Прокладка", "qty": 4, "usd_lo": 10, "usd_hi": 20,
            "conf": "B", "unit_price_usd": 15.0, "stock_grade": "in_stock",
            "seller": "Продавец", "sellers": [{"seller": "Продавец"}], "lead_time": "3 дня",
            "covers_qty": "full", "verdict": "in_stock", "man": "Изготовитель",
            "sheet": "Лист", "cat": "прокладки", "model": "Машина", "unit": "ШТ"}
    out = []
    for i, stock in enumerate((12, "12 pcs", None, "", 0)):
        r = dict(base)
        r["pn"] = f"AB-{i}"
        r["stock_qty"] = stock
        out.append(r)
    return out


def test_запросы_собираются_при_остатке_числом():
    import ship_rfq

    # тело письма принимает ПАРЫ «строка заявки, карточка продавца»
    for r in rows():
        pair = [(r, {"url": "https://example.com/item", "seller": "Продавец"})]
        ship_rfq.body_en("Продавец", pair)
        ship_rfq.body_ru("Продавец", pair)


def test_наименование_в_письме_не_обрезано():
    """По наименованию продавец и подбирает деталь: конец строки несёт размер."""
    import ship_rfq

    long_name = ("Клапан шаровой в сборе, пневматический, 2,0 дюйма, класс давления 300, "
                 "корпус нержавеющая сталь, уплотнение фторопласт, исполнение под привод")
    r = dict(rows()[0])
    r["name"] = long_name
    pair = [(r, {"url": "https://example.com/item", "seller": "Продавец"})]
    for body in (ship_rfq.body_en("Продавец", pair), ship_rfq.body_ru("Продавец", pair)):
        assert long_name in body, "наименование в письме обрезано"


def test_разведка_цен_собирается_при_остатке_числом():
    import ship_priced

    for r in rows():
        for _, _, fn in ship_priced.COLS:
            fn(r)


def test_выкладка_по_маркам_собирается_при_остатке_числом():
    import ship_brand

    for r in rows():
        for col in ship_brand.COLS:
            col[2](r)
