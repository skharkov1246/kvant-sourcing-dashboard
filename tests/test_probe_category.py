"""Зонд линий продукт-оунеров: заполненность «Категории оборудования». Корпус придуман
(правило 18)."""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

from scripts import probe_category as зонд  # noqa: E402

К, PO = зонд.КАТЕГОРИЯ, зонд.PO


def test_значения_списка():
    assert зонд.значения(["11", "", "0", 12]) == ["11", "12"]
    assert зонд.значения("13") == ["13"] and зонд.значения(None) == [] and зонд.значения("0") == []


def test_сводка_считает_заполненность_несколько_значений_и_po_без_имён():
    сделки = [{К: ["1"], PO: "7"}, {К: ["1", "2"], PO: "8"}, {К: [], PO: "7"}, {К: None}]
    out = "\n".join(зонд.сводка(сделки, {"1": "Компрессоры", "2": "Насосы"}))
    assert "категория: 2 из 4 (50 %); несколько значений: 1" in out
    assert "Product leader: 3 из 4 (75 %); из сделок с категорией — 2 из 2 (100 %)" in out
    assert "«Компрессоры»: 2 · разных PO 2" in out and "«Насосы»: 1 · разных PO 1" in out
