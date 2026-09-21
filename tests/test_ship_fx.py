"""Цены заявки ЛУКОЙЛ живут в девяти валютах — складывать их как доллары нельзя.

Ошибка, из-за которой написан этот файл (13.09.2026): unit_price() не смотрел на
валюту вовсе. В сумму закупки шли 12 642 CZK как 12 642 USD и 7 422 RUB как
7 422 USD. Из 85 твёрдых строк 35 были не в долларах, итог завышался на 44 %:
207 480 USD вместо 144 445. Заодно price_gap объявлял «завышено в 197 раз» там,
где рублёвая цена сравнивалась с долларовой вилкой, — на деле 2,3 раза.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gt/tools"))

DATA = ROOT / "gt/data/ship_lukoil.json"
FX = ROOT / "gt/data/fx_rates.json"


def rows() -> list[dict]:
    if not DATA.exists():
        pytest.skip("gt/data/ship_lukoil.json не собран")
    doc = json.loads(DATA.read_text())
    return doc["rows"] if isinstance(doc, dict) else doc


def test_курсы_есть_и_датированы():
    """Без таблицы курсов пересчёт превращается в выдумку."""
    if not FX.exists():
        pytest.skip("gt/data/fx_rates.json отсутствует")
    fx = json.loads(FX.read_text())
    assert fx["base"] == "USD"
    assert fx.get("fetched"), "у курса обязана стоять дата: он устаревает"
    assert fx["rates"]["USD"] == 1.0
    assert len(fx["rates"]) >= 8


def test_у_каждой_цены_есть_цена_в_долларах():
    """Строка с ценой в известной валюте обязана нести price_usd."""
    if not FX.exists():
        pytest.skip("gt/data/fx_rates.json отсутствует")
    known = set(json.loads(FX.read_text())["rates"])
    missing = [r["pn"] for r in rows()
               if r.get("price") and (r.get("currency") or "USD") in known
               and r.get("price_usd") in (None, "")]
    assert not missing, f"цена есть, price_usd нет: {missing[:5]}"


def test_пересчёт_совпадает_с_курсом():
    """price_usd должен получаться делением на курс, а не копированием price."""
    if not FX.exists():
        pytest.skip("gt/data/fx_rates.json отсутствует")
    rates = json.loads(FX.read_text())["rates"]
    checked = 0
    for r in rows():
        cur = (r.get("currency") or "USD").upper()
        if not r.get("price") or cur not in rates or r.get("price_usd") in (None, ""):
            continue
        assert abs(float(r["price_usd"]) - float(r["price"]) / rates[cur]) < 0.01, r["pn"]
        checked += 1
    assert checked, "не нашлось ни одной строки с ценой — проверка ничего не доказала"


def test_недолларовая_цена_не_равна_своей_сумме():
    """Главная защита: цена в кронах и её долларовый эквивалент обязаны различаться.

    Именно равенство этих двух чисел и было ошибкой — валюту просто игнорировали.
    """
    same = [r["pn"] for r in rows()
            if r.get("price") and (r.get("currency") or "USD").upper() not in ("USD", "")
            and r.get("price_usd") not in (None, "")
            and abs(float(r["price_usd"]) - float(r["price"])) < 0.01]
    assert not same, f"цена не в долларах, а price_usd равен ей: {same[:5]}"


def test_сумма_закупки_считается_по_долларам():
    """ship_map.line_value обязан отдавать доллары, а не валюту продавца."""
    import ship_map

    for r in rows():
        cur = (r.get("currency") or "USD").upper()
        if (r.get("stock_grade") == "твёрдый" and r.get("covers_qty") == "full"
                and r.get("price") and cur not in ("USD", "")):
            val = ship_map.line_value(r)
            naive = float(r["price"]) / float(r.get("pack_qty") or 1) * (r.get("qty") or 0)
            assert abs(val - naive) > 0.01, (
                f"{r['pn']}: строка в {cur} посчитана как долларовая")
            return
    pytest.skip("в датасете нет твёрдых строк с ценой не в долларах")


def test_ноль_не_является_ценой():
    """На брокерских витринах «$0.00» — заглушка карточки-заявки, а не цена.

    Разбор 17.09.2026: таких строк в датасете 176, и все они попадали в раздел
    «рынок ниже нашей оценки», раздувая его с 58 строк до 234. Отсутствие цены
    честнее нуля: по нулю нельзя ни сравнить с вилкой, ни посчитать запас на
    снижение — запас вышел бы равным всей цене.
    """
    bad = [r["pn"] for r in rows()
           if r.get("price") not in (None, "") and float(r["price"]) <= 0
           and r.get("price_usd") not in (None, "")]
    assert not bad, f"нулевая цена пересчитана в доллары: {bad[:5]}"


def test_нулевая_цена_не_попадает_в_сравнение_с_вилкой():
    import ship_priced

    for r in rows():
        if r.get("price") in (None, "") or float(r["price"]) > 0:
            continue
        assert ship_priced.unit(r) is None, f"{r['pn']}: ноль принят за цену"
        assert ship_priced.band_state(r) == "", f"{r['pn']}: ноль сравнён с вилкой"
