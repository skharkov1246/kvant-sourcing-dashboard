"""Производственный календарь РФ (data/workcal_ru.json) и period.workdays_ru: нормы
регламента ОРК — в рабочих днях. Без сети; даты — придуманные отрезки на настоящем
календаре (правило 18: корпус свой, а не выгрузка портала)."""
from __future__ import annotations

import datetime as dt
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import period as period_mod       # noqa: E402

D = dt.date
# число рабочих дней по году — сверено с двумя источниками при составлении файла
# (xmlcalendar.ru и consultant.ru, 26.10.2026); здесь — третий раз, руками
ГОД = {2024: 248, 2025: 247, 2026: 247, 2027: 247}


def test_годы_и_итоги_года():
    assert period_mod.workcal_years() == tuple(sorted(ГОД))
    for y, n in ГОД.items():
        assert period_mod.workdays_ru(D(y - 1, 12, 31), D(y, 12, 31)) == n


def test_праздник_не_рабочий():
    # 23.02.2026 — понедельник, праздник: пт 20.02 → вт 24.02 — один рабочий день
    assert period_mod.workdays_ru(D(2026, 2, 20), D(2026, 2, 24)) == 1
    assert period_mod.is_workday_ru(D(2026, 2, 23)) is False
    # новогодние каникулы: 31.12.2025 (перенос) … 11.01.2026 — ни одного рабочего
    assert period_mod.workdays_ru(D(2025, 12, 30), D(2026, 1, 11)) == 0


def test_рабочая_суббота():
    # 01.11.2025 — рабочая суббота (перенос на пн 03.11)
    assert period_mod.is_workday_ru(D(2025, 11, 1)) is True
    assert period_mod.workdays_ru(D(2025, 10, 31), D(2025, 11, 1)) == 1
    assert period_mod.workdays_ru(D(2025, 10, 31), D(2025, 11, 5)) == 2      # сб 1, ср 5
    assert period_mod.workdays(D(2025, 11, 1), D(2025, 11, 1)) == 0          # прежняя мера — Пн–Пт


def test_перенос_выходного():
    # 09.05.2026 — суббота, выходной перенесён на пн 11.05
    assert period_mod.is_workday_ru(D(2026, 5, 11)) is False
    assert period_mod.workdays_ru(D(2026, 5, 8), D(2026, 5, 12)) == 1
    # 2024: сб 27.04 рабочая, пн 29.04 и вт 30.04 — выходные
    assert period_mod.workdays_ru(D(2024, 4, 26), D(2024, 5, 2)) == 2       # сб 27, чт 2


def test_тот_же_день_и_обратный_порядок():
    assert period_mod.workdays_ru(D(2026, 3, 10), D(2026, 3, 10)) == 0
    assert period_mod.workdays_ru(D(2026, 3, 10), D(2026, 3, 11)) == 1
    assert period_mod.workdays_ru(D(2026, 3, 11), D(2026, 3, 10)) == -1
    assert period_mod.workdays_ru(D(2026, 3, 16), D(2026, 3, 9)) == -5       # 9.03 — перенос


def test_граница_года():
    # 30.12.2026 (ср) → 11.01.2027 (пн): рабочий только 11.01; 31.12.2026 — перенос
    assert period_mod.workdays_ru(D(2026, 12, 30), D(2027, 1, 11)) == 1
    assert period_mod.add_workdays_ru(D(2026, 12, 30), 1) == D(2027, 1, 11)


def test_года_нет_в_календаре_ответа_нет():
    assert period_mod.workdays_ru(D(2027, 12, 30), D(2028, 1, 11)) is None
    assert period_mod.workdays_ru(D(2023, 12, 28), D(2024, 1, 10)) is None   # 29–31.12.2023
    assert period_mod.workdays_ru(D(2028, 1, 11), D(2027, 12, 30)) is None
    assert period_mod.is_workday_ru(D(2030, 6, 3)) is None
    assert period_mod.add_workdays_ru(D(2027, 12, 29), 5) is None
    # отрезок (31.12.2023, …] начинается в покрытом году — ответ есть
    assert period_mod.workdays_ru(D(2023, 12, 31), D(2024, 1, 9)) == 1


def test_даты_по_москве():
    utc = dt.timezone.utc
    # 20:30 UTC 22.02.2026 — уже 23.02 по Москве
    t = dt.datetime(2026, 2, 22, 21, 30, tzinfo=utc)
    assert period_mod.workdays_ru(t, D(2026, 2, 24)) == 1
    assert period_mod.workdays_ru(D(2026, 2, 20), t) == 0


def test_добавить_рабочие_дни_обратно_к_счёту():
    for a in (D(2024, 4, 25), D(2025, 4, 30), D(2025, 12, 29), D(2026, 6, 10), D(2027, 2, 19)):
        for n in (-12, -3, -1, 0, 1, 2, 10, 11):
            d = period_mod.add_workdays_ru(a, n)
            assert d is not None and period_mod.workdays_ru(a, d) == n, (a, n, d)
    # «10 р.д. от доставки»: пт 24.04.2026 → вт 12.05.2026 (май: 1 и 11 — выходные)
    assert period_mod.add_workdays_ru(D(2026, 4, 24), 10) == D(2026, 5, 12)


def test_файл_сверен_со_вторым_источником(tmp_path):
    """Сверка при загрузке: счёт по спискам дней = числа второго источника. Испорченный
    список (выкинут праздник) не загружается — сборка падает, а не считает криво."""
    raw = json.loads(period_mod.WORKCAL_PATH.read_text(encoding="utf-8"))
    for y, v in raw["years"].items():
        assert sum(v["months"]) == v["total"] and len(v["months"]) == 12
        assert v["decree"].startswith("Постановление Правительства РФ")
    raw["years"]["2026"]["off"].remove("2026-02-23")
    p = tmp_path / "workcal.json"
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        period_mod._workcal(str(p))
    assert period_mod._workcal(str(tmp_path / "нет.json")) == {}
