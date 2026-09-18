"""Названная поимённо подставная цена обязана исчезнуть из сводки.

gt/data/ship_price_substitutions.json перечисляет номера, у которых в поле цены
стоит не цена этой детали, а нижняя граница витринной вилки НА КЛАСС изделий,
делённая на фасовку. Такое значение выглядит как цена и считается как цена, не
будучи ею.

Набор существовал с 18.09.2026, но ни на один счёт не влиял: он ОПИСЫВАЛ ошибку,
а сводка продолжала её содержать. Здесь закреплено, что описание и исправление
не расходятся: если номер назван в наборе подстановок, цены у него в сводке быть
не должно.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUB = ROOT / "gt/data/ship_price_substitutions.json"
MERGED = ROOT / "gt/data/ship_lukoil.json"


def key(pn: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(pn or "").split("(")[0].upper())


def test_подставная_цена_снята_из_сводки():
    if not SUB.exists() or not MERGED.exists():
        pytest.skip("наборов нет")
    named = {key(i.get("pn")) for i in json.loads(SUB.read_text(encoding="utf-8"))["items"]}
    assert named, "набор подстановок пуст — проверять нечего"
    bad = []
    for r in json.loads(MERGED.read_text(encoding="utf-8"))["rows"]:
        if key(r.get("pn")) not in named:
            continue
        for f in ("price", "price_usd", "unit_price_usd"):
            if isinstance(r.get(f), (int, float)) and r[f]:
                bad.append((r.get("pn"), f, r[f]))
    assert not bad, f"подставная цена осталась в сводке: {bad}"


def test_в_строке_записано_почему_цена_снята():
    """Без причины снятие неотличимо от потери данных."""
    if not SUB.exists() or not MERGED.exists():
        pytest.skip("наборов нет")
    named = {key(i.get("pn")) for i in json.loads(SUB.read_text(encoding="utf-8"))["items"]}
    for r in json.loads(MERGED.read_text(encoding="utf-8"))["rows"]:
        if key(r.get("pn")) in named:
            assert "ЦЕНА СНЯТА" in str(r.get("note") or ""), r.get("pn")
