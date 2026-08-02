"""Проверка СИ в приёмке (Р-08: замер неповеренным СИ недействителен).

Двойник офлайн-отсечки устройства (shared/InstrumentDirectory.kt): устройство
не показывает просроченные СИ в форме, сервер проверяет то, что реально
пришло, — телефон мог месяц не синхронизировать справочник. Правило
детерминированное, поэтому его флаг СТРОЖЕ автоприёма: сессия с нарушением
по СИ идёт контролёру даже при идеальном вердикте правил и модели.
"""

from __future__ import annotations

import re
from typing import Any

from .directories import load_si_directory

_DEADLINE = re.compile(r"поверка до (\d{4}-\d{2})")
_INSTRUMENT_KEYS = ("instrument", "stand")


def instrument_flags(state: Any, session_year_month: str) -> list[str]:
    """Флаги по СИ сессии; пусто — нарушений нет.

    Собираются значения полей instrument/stand из всех шагов (и обычных, и
    repeat-строк) и сверяются со справочником: неизвестная строка — флаг,
    просроченная на месяц съёмки поверка — флаг. Дедупликация по значению:
    один прибор в двадцати строках сетки — один флаг.
    """
    known = {
        value: _DEADLINE.search(value)
        for value in load_si_directory()["sections"]["instruments"]["values"]
    }

    used: set[str] = set()
    for result in state.results.values():
        rows = result.data if isinstance(result.data, list) else [result.data]
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key in _INSTRUMENT_KEYS:
                value = row.get(key)
                if isinstance(value, str) and value.strip():
                    used.add(value.strip())

    flags: list[str] = []
    for value in sorted(used):
        if value not in known:
            flags.append(f"си_вне_справочника: {value}")
            continue
        match = known[value]
        if match is None or match.group(1) < session_year_month:
            flags.append(f"си_поверка_просрочена: {value} (сессия {session_year_month})")
    return flags
