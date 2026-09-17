"""Тесты помесячного прогноза «Советов знатока» (advisor.py) — без сети и без Bitrix.

Корпус придуман (правило репозитория: тестовые данные не копируются из базы).
Проверяется главное: месяц закрытия больше не берётся из CLOSEDATE вслепую. В портале
это поле проставляется автоматически и всегда прошедшей датой — раскладка по нему
давала пустой график, и «прогноза выручки» на вкладке фактически не было.
"""
from __future__ import annotations

import datetime as dt
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import advisor   # noqa: E402
from tests import fixture   # noqa: E402

TODAY = fixture.ADV_TODAY


def build():
    return fixture.build_advisor()


def test_помесячный_прогноз_не_пустой_даже_когда_плановых_дат_нет():
    f = build()["forecast"]
    assert f["byMonth"], "раскладка по месяцам обязана быть — иначе график пуст"
    assert sum(m["n"] for m in f["byMonth"]) == 3       # все открытые разложены
    assert f["planN"] == 1 and f["estN"] == 2          # план из карточки только у одной


def test_прошедшая_дата_из_карточки_не_считается_планом():
    """CLOSEDATE в прошлом — автопростановка: карточка уходит в оценку, а не в план."""
    months = {m["m"]: m for m in build()["forecast"]["byMonth"]}
    assert months["2026-11"]["plan"] == 1              # 301 — живая плановая дата
    assert all(m["plan"] == 0 for k, m in months.items() if k != "2026-11")
    assert sum(m["est"] for m in months.values()) == 2


def test_срок_сделки_меряется_по_воронке_а_где_побед_мало_берётся_общая_медиана():
    f = build()["forecast"]
    assert f["ttwMed"] == 60          # медиана всех побед (пять по 60 дней и одна 180)
    assert f["ttwCats"] == 1          # своя медиана только у воронки с пятью победами
    months = {m["m"]: m for m in f["byMonth"]}
    # 302 создана 01.08 в воронке «2» → +60 дн → октябрь
    assert months["2026-10"]["est"] == 1
    assert sum(m["est"] for m in months.values()) == 2


def test_месяц_не_может_оказаться_в_прошлом():
    """Оценка по давно созданной сделке не должна падать в закрытый месяц."""
    old = fixture.adv_deal(305, "2", "C2:EXECUTING", 70_000, "2026-01-05")
    f = advisor.compute(fixture.AdvisorStub(), as_of=TODAY, deals=fixture.ADV_DEALS + [old],
                        orders=[], realize_date=fixture.ADV_REALIZE,
                        deal_stage_names=fixture.ADV_STAGES)["forecast"]
    assert all(m["m"] >= TODAY.strftime("%Y-%m") for m in f["byMonth"])
    assert f["estN"] == 3


def test_общая_сумма_пайплайна_от_раскладки_не_зависит():
    f = build()["forecast"]
    assert f["openN"] == 3
    assert sum(m["raw"] for m in f["byMonth"]) == 600_000   # 300k + 200k + 100k
