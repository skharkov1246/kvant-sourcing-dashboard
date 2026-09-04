"""Расчёт модуля и смещения зубчатого венца — каталог docs/14, строка F.

Оператор снимает то, что можно померить штангенциркулем и нормалемером:
число зубьев z, диаметр вершин da, длину общей нормали W по n_w зубьям.
Модуль и смещение СЧИТАЕТ сервер и показывает у детали — грубая ошибка
промера (перепутано число зубьев в обхвате, съеден зуб) ловится на месте,
а не через неделю у конструктора.

Формулы — классика эвольвентного зацепления (α = 20°):
    m ≈ da / (z + 2)                       — прямозубое некорригированное
    W₀ = m·cosα·(π·(n_w − 0.5) + z·invα)   — нормаль при x = 0
    x  = (W − W₀) / (2·m·sinα)             — смещение из фактической нормали

Честные ограничения: оценка по da верна для НЕкорригированного прямозубого
венца; у б/у колеса вершины изношены — da занижен, поэтому на границе рядов
берётся БОЛЬШИЙ модуль (то же правило, что для сечений колец, Р-10 п.5.2).
Косозубые требуют пересчёта через торцевой модуль — это отмечается в note,
решение за конструктором.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ГОСТ 9563, 1-й ряд (затравка; полный ряд — справочником, Р-03 разд. 6)
GOST9563_MODULES: tuple[float, ...] = (
    0.5, 0.6, 0.8, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0,
    5.0, 6.0, 8.0, 10.0, 12.0, 16.0, 20.0, 25.0,
)

_ALPHA = math.radians(20.0)
_INV_ALPHA = math.tan(_ALPHA) - _ALPHA


@dataclass(frozen=True)
class GearGuess:
    z: int
    da_mm: float
    module_est: float
    module_std: float
    module_deviation: float
    is_standard_module: bool
    x_shift: float | None
    note: str


def analyze_gear(
    z: int,
    da_mm: float,
    w_mm: float | None = None,
    n_w: int | None = None,
    used: bool = True,
    module_table: tuple[float, ...] = GOST9563_MODULES,
    tol_fraction: float = 0.04,
) -> GearGuess:
    """Оценка модуля по (z, da) и смещения по длине общей нормали."""
    if z < 5:
        raise ValueError("Число зубьев меньше 5 — проверьте подсчёт (венец повреждён?)")
    if da_mm <= 0:
        raise ValueError("Диаметр вершин должен быть положительным")

    m_est = da_mm / (z + 2)
    if used:
        # Вершины б/у венца изношены → da и оценка модуля ЗАНИЖЕНЫ:
        # на границе рядов берётся больший модуль.
        above = [m for m in module_table if m >= m_est - 1e-9]
        below = [m for m in module_table if m < m_est]
        candidates = ([above[0]] if above else []) + ([below[-1]] if below else [])
        m_std = min(candidates, key=lambda m: (abs(m - m_est), -m))
        wear_note = "вершины б/у венца изношены — на границе рядов принят больший модуль"
    else:
        m_std = min(module_table, key=lambda m: abs(m - m_est))
        wear_note = "новый венец — ближайший модуль ряда"

    deviation = round(m_std - m_est, 4)
    is_std = abs(deviation) <= tol_fraction * m_std

    x_shift: float | None = None
    if w_mm is not None and n_w is not None:
        if n_w < 2:
            raise ValueError("Нормаль меряется минимум по 2 зубьям")
        w_zero = m_std * math.cos(_ALPHA) * (math.pi * (n_w - 0.5) + z * _INV_ALPHA)
        x_shift = round((w_mm - w_zero) / (2 * m_std * math.sin(_ALPHA)), 3)

    note = (
        f"m ≈ {m_est:.3f} по da/(z+2); принят m = {m_std:g} (ГОСТ 9563, {wear_note}). "
        + ("Модуль в ряду — венец похож на стандартный некорригированный. "
           if is_std else
           f"Отклонение {deviation:+g} мм больше допуска — возможна коррекция, "
           f"износ или косозубый венец (пересчёт через торцевой модуль — у конструктора). ")
        + (f"Смещение x ≈ {x_shift:+.3f} по общей нормали." if x_shift is not None
           else "Смещение не оценено: нет длины общей нормали.")
    )

    return GearGuess(
        z=z, da_mm=da_mm,
        module_est=round(m_est, 4),
        module_std=m_std,
        module_deviation=deviation,
        is_standard_module=is_std,
        x_shift=x_shift,
        note=note,
    )
