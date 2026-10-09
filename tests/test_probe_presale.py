"""Зонд пресейла: что печатается в публичный журнал и как узнаётся воронка."""
from __future__ import annotations

import datetime as dt
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

from scripts import probe_presale as зонд  # noqa: E402

СЕГОДНЯ = dt.date(2026, 10, 9)


def test_пресейл_узнаётся_в_разных_написаниях():
    for имя in ("Пресейл", "ПРЕ-СЕЙЛ", "Presale", "Pre-sale", "Pre sale", "Предпродажная подготовка"):
        assert зонд.пресейл(имя), имя
    for имя in ("Реализация", "Тендеры", "Сервис"):
        assert not зонд.пресейл(имя), имя


def test_название_старой_воронки_не_печатается():
    """В названиях клиентских воронок стоят имена клиентов, а журнал публичен."""
    старая = dt.date(2025, 1, 1)
    assert зонд.подпись("14", "Клиент А", старая, СЕГОДНЯ) == "#14"
    assert "Пресейл" in зонд.подпись("40", "Пресейл", старая, СЕГОДНЯ)
    assert "Реализация" in зонд.подпись("0", "Реализация", старая, СЕГОДНЯ)
    новая = СЕГОДНЯ - dt.timedelta(days=30)
    assert зонд.подпись("41", "Новая воронка", новая, СЕГОДНЯ).endswith("(новая)")


def test_роль_записи():
    service, dept = {"234"}, {"76"}
    assert зонд.роль("234", service, dept) == "служебная запись"
    assert зонд.роль(["76"], service, dept) == "отдел поиска поставщиков"
    assert зонд.роль("5", service, dept) == "прочие"
    assert зонд.роль("", service, dept) == "пусто"


def test_медиана_и_заполненность():
    assert зонд.медиана([3, 1, 2]) == 2 and зонд.медиана([1, 2, 3, 4]) == 2.5 and зонд.медиана([]) is None
    assert зонд.заполнено(["", "7"]) and not зонд.заполнено([]) and not зонд.заполнено("0")
