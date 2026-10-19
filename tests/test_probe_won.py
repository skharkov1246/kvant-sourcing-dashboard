"""Зонд «выиграна, но не переведена»: какие стадии — победа и как раскладываются сделки.

Корпус придуман (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import datetime as dt
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

from scripts import probe_won as зонд  # noqa: E402

МЕТА = {
    "C32:WON": {"name": "Сделка выиграна", "sem": "S", "cat": "32"},
    "C32:SPEC": {"name": "Согласование финальной спецификации", "sem": "P", "cat": "32"},
    "C32:LOSE": {"name": "Не выиграли", "sem": "F", "cat": "32"},
    "C4:TENDER": {"name": "Тендер выигран", "sem": "P", "cat": "4"},
    "C4:NOTWON": {"name": "Не выиграли тендер", "sem": "P", "cat": "4"},
    "C24:WON": {"name": "Успех", "sem": "S", "cat": "24"},
    "WON": {"name": "Сделка успешна", "sem": "S", "cat": "0"},
}


def test_стадии_победы_только_предпродажные():
    вид = зонд.стадии_победы(МЕТА, frozenset({"24"}))
    assert вид == {"C32:WON": "S", "C32:SPEC": "W", "C4:TENDER": "W"}


def test_разложение_по_группам_и_двойник():
    вид = {"C32:WON": "S", "C4:TENDER": "W"}
    сделки = [
        {"ID": 1, "STAGE_ID": "C32:WON", "TITLE": "Насос", "MOVED_TIME": "2026-09-01T10:00:00+03:00", "COMPANY_ID": "7",
         "OPPORTUNITY": "0"},
        {"ID": 2, "STAGE_ID": "C32:WON", "TITLE": "871. Насос", "MOVED_TIME": "2026-03-01T10:00:00+03:00", "COMPANY_ID": "8",
         "OPPORTUNITY": "10"},
        {"ID": 3, "STAGE_ID": "C4:TENDER", "TITLE": "Клапан", "MOVED_TIME": "2025-01-10T10:00:00+03:00", "COMPANY_ID": "9",
         "OPPORTUNITY": "5"},
        {"ID": 4, "STAGE_ID": "C32:OTHER", "TITLE": "x", "MOVED_TIME": "2026-01-01", "COMPANY_ID": "7"},
    ]
    карточки0 = [("7", "2026-09-20"), ("9", "2024-01-01")]
    р = зонд.разложить(сделки, вид, {"3"}, карточки0, dt.date(2026, 10, 13), {"1": "Норникель"})
    by = {r["id"]: r for r in р}
    assert set(by) == {"1", "2", "3"}
    assert (by["1"]["group"], by["2"]["group"], by["3"]["group"]) == ("A", "B", "C")
    assert by["1"]["twin"] and not by["3"]["twin"], "двойник — карточка воронки 0 у той же компании рядом по времени"
    assert by["1"]["nn"] and by["1"]["age"] == 42 and not by["1"]["amount"]
    с = зонд.свод(р)
    assert с[("S", "A")]["n"] == 1 and с[("W", "C")]["n"] == 1 and с[("S", "B")]["year"]["2026"] == 1
    assert зонд.корзина(400) == "больше года" and зонд.корзина(30) == "до 30 дн."
