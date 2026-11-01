"""Зонд v65 «Подтверждение готовности к передаче в ОРК» (scripts/probe_handover.py): разбор без
портала — метка поля, шаблон названия, привязка задачи, распределения, отдел. Корпус придуман
(CLAUDE.md, правило 18)."""
from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

spec = importlib.util.spec_from_file_location("probe_handover", ROOT / "scripts" / "probe_handover.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


def test_метка_поля_секунды_и_миллисекунды():
    assert P.метка_поля("ufCrm20_1787561355") == dt.date(2026, 8, 24)
    assert P.метка_поля("ufCrm20_1787924508120") == dt.date(2026, 8, 28)
    assert P.метка_поля("ufCrm20_abc") is None and P.метка_поля("") is None


def test_шаблон_названия_без_номера_и_имени():
    assert P.шаблон("Подтверждение готовности передачи поставщику заказа 901/2 «Альфа»") == \
        "Подтверждение готовности передачи поставщику заказа"
    assert P.шаблон("Подтверждение готовности: Бета") == "Подтверждение готовности"


def test_привязка_задачи():
    assert P.привязка(["Tac_7001", "D_900", "CO_5", "мусор"]) == [("Tac", "7001"), ("D", "900"), ("CO", "5")]
    assert P.привязка(None) == [] and P.привязка("Tac_1") == [("Tac", "1")]


def test_распределение_и_корзины():
    assert P.распр([], "р.д.") == "нет данных"
    assert P.распр([1, 2, 3, 10], "р.д.").startswith("n 4 · медиана 2.5 р.д.")
    assert P.корзины([0, 1, 2, 7, 15, 30]) == "≤ 1 — 2 · 2–5 — 1 · 6–10 — 1 · 11–20 — 1 · > 20 — 1"


def test_отдел_исполнителя():
    deps = {"1": "Отдел реализации контрактов", "2": "Отдел поиска поставщиков", "3": "Бухгалтерия"}
    users = {"10": {"depts": ["1"]}, "11": {"depts": ["2"]}, "12": {"depts": ["3"]}}
    assert [P.отдел_по(u, users, deps) for u in ("10", "11", "12", "99")] == ["ОРК", "поиск поставщиков", "иное", "не найден"]


def test_время_без_пояса_москва():
    t = P.вр("2026-10-23T17:59:00")
    assert t.utcoffset() == dt.timedelta(hours=3) and P.вр("") is None
