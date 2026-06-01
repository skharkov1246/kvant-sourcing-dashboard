"""Разбор отчётного периода и недельные бакеты (Пн–Вс) для дашборда.

Период по умолчанию — от анкора (01.05.2026) до сегодня. Жёсткий пол — 01.01.2026.
Первая неделя обрезается по началу периода (как W1 «1–3.05» в макете).
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from config import DEFAULT_PERIOD_ANCHOR, PERIOD_FLOOR

FLOOR = dt.date.fromisoformat(PERIOD_FLOOR)
ANCHOR = dt.date.fromisoformat(DEFAULT_PERIOD_ANCHOR)


@dataclass
class Week:
    label: str        # "W1"
    days: str         # "1–3.05"
    start: dt.date
    end: dt.date


@dataclass
class Period:
    start: dt.date
    end: dt.date
    weeks: list[Week]

    @property
    def start_iso(self) -> str:
        return self.start.isoformat() + "T00:00:00"

    @property
    def end_iso(self) -> str:
        return self.end.isoformat() + "T23:59:59"

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def label(self) -> str:
        return f"{_d(self.start)} – {_d(self.end)}"

    def week_index(self, when: dt.date) -> int | None:
        for i, w in enumerate(self.weeks):
            if w.start <= when <= w.end:
                return i
        return None


_MON = ["", "янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def _d(d: dt.date) -> str:
    return f"{d.day:02d} {_MON[d.month]} {d.year}"


def _days_label(a: dt.date, b: dt.date) -> str:
    if a.month == b.month:
        return f"{a.day}–{b.day}.{b.month:02d}"
    return f"{a.day}.{a.month:02d}–{b.day}.{b.month:02d}"


def parse_dt(value: str) -> dt.date | None:
    """ISO-строка Bitrix ('2026-05-31T15:40:42+03:00') → date (по локальной части)."""
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _weeks(start: dt.date, end: dt.date) -> list[Week]:
    weeks: list[Week] = []
    cur = start
    i = 1
    while cur <= end:
        sunday = cur + dt.timedelta(days=6 - cur.weekday())  # Пн=0
        w_end = min(sunday, end)
        weeks.append(Week(f"W{i}", _days_label(cur, w_end), cur, w_end))
        cur = w_end + dt.timedelta(days=1)
        i += 1
    return weeks


def parse_period(spec: str | None, *, as_of: dt.date | None = None) -> Period:
    today = as_of or dt.date.today()
    start: dt.date
    end: dt.date

    if not spec or spec.lower() in ("default", "current", "now"):
        start, end = ANCHOR, today
    elif re.fullmatch(r"\d{4}-\d{2}", spec):  # месяц
        y, m = (int(x) for x in spec.split("-"))
        start = dt.date(y, m, 1)
        nxt = dt.date(y + (m == 12), (m % 12) + 1, 1)
        end = min(nxt - dt.timedelta(days=1), today)
    elif ":" in spec:  # диапазон YYYY-MM-DD:YYYY-MM-DD
        a, b = spec.split(":", 1)
        start, end = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    else:  # одиночная дата → от неё до сегодня
        start, end = dt.date.fromisoformat(spec), today

    if start < FLOOR:
        start = FLOOR
    if end < start:
        end = start
    return Period(start=start, end=end, weeks=_weeks(start, end))
