"""Лист решений: у каждой доли назван знаменатель.

Оплачено собственным отчётом владельцу 18.09.2026. Всю ночь я докладывал
«перепроверкой закрыто 79,3 % экспозиции», и это верно ровно в одном смысле:
экспозиция считается только по тем строкам заявки, у которых у нас ЕСТЬ вилка,
а их 857 из 1 642. У остальных 785 оценки нет вовсе, и в знаменатель они не
входят. То есть «79 % экспозиции» и «79 % заявки» — разные утверждения, а в
листе стояло первое без оговорки, и читалось оно как второе.

Здесь закреплено: где лист печатает долю экспозиции, там же он обязан сказать,
по скольким строкам эта экспозиция посчитана. И второе: пункт «что я делаю
дальше» не имеет права отчитываться нулём («осталось 0 строк на 0 USD») — если
рубеж закрыт, лист обязан назвать следующий.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "gt/docs/РЕШЕНИЯ-ЛУКОЙЛ.html"
SUMMARY = ROOT / "gt/data/ship_lukoil.json"
sys.path.insert(0, str(ROOT / "gt/tools"))


def text() -> str:
    if not DOC.exists():
        pytest.skip("листа решений нет")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", DOC.read_text(encoding="utf-8")))


def test_доля_экспозиции_названа_вместе_со_знаменателем():
    t = text()
    if "% экспозиции" not in t:
        pytest.skip("лист не печатает долю экспозиции")
    assert "вилки нет вовсе" in t or "оценённой части" in t or "не входят" in t, (
        "лист печатает долю экспозиции, не сказав, что экспозиция есть не у всех строк "
        "заявки: так «79 % экспозиции» читается как «79 % заявки»")


def test_число_строк_с_вилкой_в_листе_совпадает_с_данными():
    """Оговорка обязана быть замером, а не фразой: числа сверяются со сводкой."""
    t = text()
    if "вилки нет вовсе" not in t:
        pytest.skip("оговорки в листе нет")
    rows = json.loads(SUMMARY.read_text(encoding="utf-8"))["rows"]
    priced = sum(1 for r in rows
                 if r.get("usd_lo") not in (None, "") and r.get("usd_hi") not in (None, ""))
    for n in (priced, len(rows), len(rows) - priced):
        assert f"{n:,}".replace(",", " ") in t or str(n) in t, (
            f"в оговорке нет числа {n}: значит она написана словами, а не посчитана")


def test_что_делаю_дальше_не_отчитывается_нулём():
    t = text()
    i = t.find("Что я делаю дальше")
    if i < 0:
        pytest.skip("раздела нет")
    seg = t[i:i + 900]
    assert "осталось 0 строк" not in seg, (
        "пункт «что делаю дальше» отчитался нулём: если рубеж закрыт, лист обязан назвать "
        "следующий, а не сообщать, что делать нечего")


def test_следующий_рубеж_считается_из_данных():
    import ship_decisions as sd

    rows = json.loads(SUMMARY.read_text(encoding="utf-8"))["rows"]
    rv = json.loads((ROOT / "gt/data/ship_reverify.json").read_text(encoding="utf-8"))["rows"]
    left_rows, left_usd = sd.next_frontier(rows, rv)
    priced = [r for r in rows if sd.expo(r) > 0]
    assert 0 <= left_rows <= len(priced)
    # рубеж — это НЕразобранное: вместе с разобранным он даёт всю оценённую часть
    done = len(priced) - left_rows
    assert done > 0, "перепроверка закрыла ноль оценённых строк — это не может быть правдой"
    assert left_usd <= sum(map(sd.expo, priced)) + 1.0
