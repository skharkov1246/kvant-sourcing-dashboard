"""Зонд договоров: заполненность полей и счёт файлов. Корпус придуман (правило 18)."""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

from scripts import probe_contracts as зонд  # noqa: E402


def test_заполненность_и_файлы():
    записи = [{"f": [{"id": 1, "url": "x"}, {"id": 2}], "d": "2026-01-01"}, {"f": [], "d": ""}, {"f": {"id": 3}}]
    ряды = {к: (k, f) for к, _, k, f in зонд.заполненность(записи, {"f": "Скан", "d": "Дата"})}
    assert ряды["f"] == (2, 3) and ряды["d"] == (1, 0)
    assert зонд.ТЕМЫ.search("2025 - Разовый договор/Спецификация/ДС") and not зонд.ТЕМЫ.search("Brands")
