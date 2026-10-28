"""Разбор отчётного периода и недельные бакеты (Пн–Вс) для дашборда.

Период по умолчанию — от анкора (01.05.2026) до сегодня. Жёсткий пол — 01.01.2026.
Первая неделя обрезается по началу периода (как W1 «1–3.05» в макете).
"""
from __future__ import annotations

import datetime as dt
import functools
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from config import DEFAULT_PERIOD_ANCHOR, PERIOD_FLOOR

FLOOR = dt.date.fromisoformat(PERIOD_FLOOR)
ANCHOR = dt.date.fromisoformat(DEFAULT_PERIOD_ANCHOR)


def now(tz: dt.tzinfo | None = None) -> dt.datetime:
    """Текущий момент. KVANT_NOW (ISO с часовым поясом) замораживает его.

    Нужно живой сверке (.github/workflows/live-check.yml): сборки прода и правки
    идут одна за другой с разницей в минуты, и срок «сколько дней на стадии»,
    посчитанный до разных «сейчас», расходился бы сам по себе — это выглядело бы
    эффектом правки. В обычной сборке переменная не задана, и это datetime.now."""
    v = (os.getenv("KVANT_NOW") or "").strip()
    if not v:
        return dt.datetime.now(tz)
    t = dt.datetime.fromisoformat(v)
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(tz) if tz else t.astimezone().replace(tzinfo=None)


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


def workdays(a: dt.date, b: dt.date) -> int:
    """Рабочих дней (Пн–Пт) в отрезке [a, b] включительно; праздники не вычитаются.

    Темп человека меряется по ЕГО рабочим дням в окне, а не по календарю всего
    периода: иначе принятый в августе делит свои запросы на дни с мая и выглядит
    вдвое слабее, а ушедший — делит на дни, когда его уже не было."""
    if b < a:
        return 0
    full, rest = divmod((b - a).days + 1, 7)
    return full * 5 + sum(1 for i in range(rest) if (a.weekday() + i) % 7 < 5)


# --- производственный календарь РФ (нормы регламента ОРК — в рабочих днях) ---------------
# data/workcal_ru.json: по годам — нерабочие Пн–Пт (праздники и переносы) и рабочие Сб/Вс;
# числа рабочих дней по месяцам — из второго источника, сверяются со счётом при загрузке.
# Года нет в файле — ответа нет (None): подмена на Пн–Пт дала бы «в норме» там, где
# праздник съел срок, и наоборот (ворота доверия «год без календаря», ork.GATES).
WORKCAL_PATH = Path(__file__).resolve().parent / "data" / "workcal_ru.json"
MSK = dt.timezone(dt.timedelta(hours=3))


@functools.lru_cache(maxsize=4)
def _workcal(path: str = str(WORKCAL_PATH)) -> dict[int, tuple[frozenset, frozenset]]:
    """{год: (нерабочие Пн–Пт, рабочие Сб/Вс)}. Файла нет — пусто: все ответы None.
    Счёт по спискам разошёлся с числами второго источника — ValueError: календарь
    с ошибкой хуже отсутствующего, сборка должна упасть, а не считать сроки криво."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[int, tuple[frozenset, frozenset]] = {}
    for y, v in (raw.get("years") or {}).items():
        y = int(y)
        off = frozenset(dt.date.fromisoformat(s) for s in v.get("off") or ())
        work = frozenset(dt.date.fromisoformat(s) for s in v.get("work") or ())
        if any(d.year != y or d.weekday() >= 5 for d in off) or \
                any(d.year != y or d.weekday() < 5 for d in work):
            raise ValueError(f"workcal_ru: {y}: день не того года или не того вида")
        months = [0] * 12
        d = dt.date(y, 1, 1)
        while d.year == y:
            if (d.weekday() < 5 and d not in off) or d in work:
                months[d.month - 1] += 1
            d += dt.timedelta(days=1)
        if months != list(v.get("months") or ()) or sum(months) != v.get("total"):
            raise ValueError(f"workcal_ru: {y}: счёт по спискам не сошёлся со вторым источником")
        out[y] = (off, work)
    return out


def workcal_years() -> tuple[int, ...]:
    """Годы, покрытые производственным календарём, по возрастанию."""
    return tuple(sorted(_workcal()))


def _msk_date(x) -> dt.date:
    """Дата по Москве: datetime с поясом переводится в МСК, без пояса и date — как есть."""
    if isinstance(x, dt.datetime):
        return (x.astimezone(MSK) if x.tzinfo else x).date()
    return x


def is_workday_ru(d) -> bool | None:
    """Рабочий ли день по производственному календарю РФ; None — года нет в календаре."""
    d = _msk_date(d)
    cal = _workcal().get(d.year)
    if cal is None:
        return None
    off, work = cal
    return d in work or (d.weekday() < 5 and d not in off)


def workdays_ru(a, b) -> int | None:
    """Рабочих дней d по календарю РФ при a < d ≤ b (даты по МСК): тот же день — 0,
    «следующий рабочий» — 1; норма «1 р.д.» выполнена при результате ≤ 1.
    a > b — то же число со знаком минус (порядок меток нарушен — видно по знаку).
    None, если хоть один год из (a, b] не покрыт календарём — без подмены на Пн–Пт.
    period.workdays (Пн–Пт на [a, b] включительно) — другая мера, её не трогаем."""
    a, b = _msk_date(a), _msk_date(b)
    if a == b:
        return 0
    if a > b:
        n = workdays_ru(b, a)
        return -n if n is not None else None
    cal = _workcal()
    if any(y not in cal for y in range((a + dt.timedelta(days=1)).year, b.year + 1)):
        return None
    full, rest = divmod((b - a).days, 7)
    n = full * 5 + sum(1 for i in range(1, rest + 1) if (a.weekday() + i) % 7 < 5)
    for y in range((a + dt.timedelta(days=1)).year, b.year + 1):
        off, work = cal[y]
        n -= sum(1 for d in off if a < d <= b)
        n += sum(1 for d in work if a < d <= b)
    return n


def add_workdays_ru(a, n: int) -> dt.date | None:
    """Дата d, для которой workdays_ru(a, d) == n: n > 0 — n-й рабочий день после a
    (срок «10 р.д. от доставки» — add_workdays_ru(доставка, 10)); n == 0 — сама a;
    n < 0 — день перед |n|-м рабочим днём, считая назад от a. None — дошли до года
    без календаря."""
    d = _msk_date(a)
    step = 1 if n > 0 else -1
    left = abs(n)
    while left:
        if step < 0:
            w = is_workday_ru(d)
            if w is None:
                return None
            left -= w
            d -= dt.timedelta(days=1)
        else:
            d += dt.timedelta(days=1)
            w = is_workday_ru(d)
            if w is None:
                return None
            left -= w
    return d


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


def year_start(d: dt.date, back: int = 0) -> str:
    """Начало года даты отчёта (as_of), а не часов машины: отчёт за прошлый период
    должен брать свой год. back=1 — начало прошлого года."""
    return f"{d.year - back}-01-01T00:00:00"
