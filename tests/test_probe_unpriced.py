"""Зонд v62 «Не расценено через месяц»: когорты карточек и заявок. Корпус придуман (правило 18)."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import probe_unpriced as pu  # noqa: E402
import stages  # noqa: E402

NOW = dt.datetime(2026, 10, 30, 12, tzinfo=pu.MSK)
KP = set(stages.KP_STAGES)


def card(cid, created, stage="DT166_24:PREPARATION", deal="1", offer=None):
    return {"id": cid, "createdTime": created, "stageId": stage, "parentId2": deal, "OFF": offer}


def test_months_back_and_kp_stage_ids():
    m = pu.months_back(dt.date(2026, 10, 30), 3)
    assert m == [(dt.date(2026, 8, 1), dt.date(2026, 9, 1)), (dt.date(2026, 9, 1), dt.date(2026, 10, 1)),
                 (dt.date(2026, 10, 1), dt.date(2026, 11, 1))]
    assert pu.months_back(dt.date(2026, 1, 5), 2)[0] == (dt.date(2025, 12, 1), dt.date(2026, 1, 1))
    ids = pu.kp_stage_ids(24, KP, stages.classify_stage)
    assert set(ids) == {"DT166_24:UC_H49RUE", "DT166_24:SUCCESS", "DT166_24:FAIL", "DT166_24:1",
                        "DT166_24:4", "DT166_24:5"}


def test_cohort_unpriced_at_30_days_and_young_cut():
    cards = [card(1, "2026-08-01T10:00:00+03:00"),                         # КП на 10-й день
             card(2, "2026-08-01T10:00:00+03:00", "DT166_24:2"),           # отказ, КП нет
             card(3, "2026-08-01T10:00:00+03:00", "DT166_24:SUCCESS"),     # КП на 40-й день — позже месяца
             card(4, "2026-10-20T10:00:00+03:00", offer=["f"])]            # молодая — отсечена
    kp = {"1": pu.ts("2026-08-11T10:00:00+03:00"), "3": pu.ts("2026-09-10T10:00:00+03:00")}
    s = pu.cohort_stats(cards, kp, NOW, stages.classify_stage, KP, "OFF")
    assert s["зрелых"] == 3 and s["не_расценено"] == 2 and s["расценено"] == 1 and s["отсечено_молодых"] == 1
    assert s["расценено_позже"] == 1 and s["корзины_нерасц"]["refused"] == 1
    # сверка: в стадии КП сейчас, а входа в КП в истории нет
    s2 = pu.cohort_stats([card(5, "2026-08-01T10:00:00+03:00", "DT166_24:UC_H49RUE")], {}, NOW,
                         stages.classify_stage, KP, "OFF")
    assert s2["кп_сейчас_без_истории"] == 1 and s2["не_расценено"] == 1


def test_deal_priced_if_any_card_within_month_of_first():
    cards = [card(1, "2026-08-01T10:00:00+03:00", deal="7"), card(2, "2026-08-20T10:00:00+03:00", deal="7"),
             card(3, "2026-08-05T10:00:00+03:00", deal="8"), card(4, "2026-10-25T10:00:00+03:00", deal="9")]
    kp = {"2": pu.ts("2026-08-25T10:00:00+03:00"),          # 24 дня от первой карточки сделки 7
          "3": pu.ts("2026-09-20T10:00:00+03:00")}          # 46 дней — позже месяца
    d = pu.deal_stats(cards, kp, NOW)
    assert d == {"7": ("2026-08", True), "8": ("2026-08", False)}   # сделка 9 — незрелая
