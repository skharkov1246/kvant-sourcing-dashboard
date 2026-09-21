"""Документ разведки цен обязан собираться, а не падать на первой же строке.

Оплачено 18.09.2026: сборка падала с TypeError на колонке остатка. Поле
stock_qty приходит и числом, и строкой («12 pcs»), а код резал его как строку —
срез по числу роняет весь документ. Ошибку никто не ловил: инструмент в гейт не
входил, а запускают его руками и редко.

Здесь закреплено минимальное: каждая колонка таблицы обязана отработать на
строке, где поля имеют разные типы, в том числе пустые и числовые.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))


def rows() -> list[dict]:
    """Корпус придуман, а не взят из базы: так велят правила репозитория."""
    return [
        # остаток числом — именно на этом сборка и падала
        {"pn": "AB-1", "name": "Прокладка", "qty": 4, "usd_lo": 10, "usd_hi": 20,
         "conf": "B", "unit_price_usd": 15.0, "stock_qty": 12, "stock_grade": "in_stock",
         "sellers": [{"seller": "Продавец первый"}]},
        # остаток строкой, продавцов нет вовсе
        {"pn": "CD-2", "name": "Клапан", "qty": 1, "usd_lo": 100, "usd_hi": 200,
         "conf": "C", "unit_price_usd": 350.0, "stock_qty": "12 pcs", "stock_grade": "нет",
         "sellers": []},
        # пустые поля по всей строке
        {"pn": "EF-3", "name": "", "qty": 0, "usd_lo": None, "usd_hi": None,
         "conf": "", "unit_price_usd": None, "stock_qty": None, "sellers": None},
    ]


def test_каждая_колонка_отрабатывает_на_разных_типах_полей():
    import ship_priced as sp

    for r in rows():
        for title, _, fn in sp.COLS:
            fn(r)          # падение здесь и роняло весь документ


def test_таблица_собирается_целиком():
    import ship_priced as sp

    html = sp.table(rows(), {})
    assert "<td>" in html and html.count("<tr") >= 3
    for r in rows():
        assert r["pn"] in html


def test_наименование_и_продавец_не_обрезаны():
    """Обрезка в выгрузке запрещена правилами репозитория."""
    import ship_priced as sp

    long_name = "Наименование длиной заведомо больше ста тридцати символов, " * 4
    r = {"pn": "GH-4", "name": long_name, "qty": 1, "usd_lo": 1, "usd_hi": 2,
         "conf": "A", "unit_price_usd": None, "stock_qty": "", "sellers": [{"seller": "П" * 200}]}
    html = sp.table([r], {})
    assert long_name.strip() in html.replace("&nbsp;", " "), "наименование обрезано"
    assert "П" * 200 in html, "имя продавца обрезано"
