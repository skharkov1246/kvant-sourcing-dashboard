"""Приведение замеров к стандартным рядам — Р-08 п. 3.3 и Р-10 пп. 5.2–5.3.

Регламент требует не «записать что намерили», а привести результат к ряду:

  * б/у металл (Р-08 п. 3.3): округление к ряду ГОСТ 6636 **с учётом направления
    износа** — изношенный вал вверх, изношенное отверстие вниз; плюс проверка,
    не дюймовая ли деталь («не округлять в метрику без анализа»);
  * O-ring (Р-10 п. 5.2): сверка пары d1×d2 с ГОСТ 9833 / ISO 3601 / AS568;
    у б/у кольца сечение занижено остаточной деформацией, поэтому при попадании
    между размерами ряда берётся **больший** d2;
  * плоские прокладки (Р-10 п. 5.3): б/у прокладка обжата, толщина новой больше
    измеренной — округление **вверх** к ряду толщин листа.

Смысл модуля: снять с человека арифметику и решение «стандартное или нет».
Совпадение с рядом означает, что деталь стандартная → категория A → маршрут
«подбор», и полный реверс-инжиниринг не нужен (Р-05 п. 2). Это самая дорогая
развилка процесса, и ошибаться в ней вручную незачем.

Таблицы рядов здесь — только затравка. Полные таблицы ведутся как справочник
владельцем реестра (Р-03 разд. 6) и синхронизируются на устройство; код —
это алгоритм подбора, а не база данных стандартов.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

# ─────────────────────────────────────────────────────────────────────────────
# ГОСТ 6636 — ряды нормальных линейных размеров
# ─────────────────────────────────────────────────────────────────────────────

# Ra40 — базовый ряд одной декады. Ra20/Ra10/Ra5 — его подмножества с шагом
# 2/4/8, как и устроен сам стандарт.
_RA40_DECADE: tuple[float, ...] = (
    1.0, 1.05, 1.1, 1.15, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7,
    1.8, 1.9, 2.0, 2.1, 2.2, 2.4, 2.5, 2.6, 2.8, 3.0,
    3.2, 3.4, 3.6, 3.8, 4.0, 4.2, 4.5, 4.8, 5.0, 5.3,
    5.6, 6.0, 6.3, 6.7, 7.1, 7.5, 8.0, 8.5, 9.0, 9.5,
)

SeriesName = Literal["Ra5", "Ra10", "Ra20", "Ra40"]
_STEP: dict[str, int] = {"Ra40": 1, "Ra20": 2, "Ra10": 4, "Ra5": 8}


def gost6636_series(name: SeriesName = "Ra20", lo: float = 1.0, hi: float = 1000.0) -> list[float]:
    """Значения ряда ГОСТ 6636 в диапазоне [lo, hi] мм."""
    if name not in _STEP:
        raise ValueError(f"Неизвестный ряд: {name}")
    step = _STEP[name]
    base = _RA40_DECADE[::step]
    values: list[float] = []
    decade = 0.01
    while decade <= hi:
        for v in base:
            x = round(v * decade, 4)
            if lo <= x <= hi:
                values.append(x)
        decade *= 10
    return sorted(set(values))


WearDirection = Literal["shaft", "bore", "none"]


@dataclass(frozen=True)
class NominalGuess:
    """Предположение о номинале, восстановленном по б/у образцу."""

    measured_mm: float
    nominal_mm: float
    series: SeriesName
    direction: WearDirection
    deviation_mm: float
    rationale: str
    """Текст для обязательного поля «Обоснование номинала» (Р-08 п. 3.2)."""

    inch_candidate_mm: float | None = None
    """Заполняется, если размер подозрительно близок к дюймовому ряду."""


def restore_nominal(
    measured_mm: float,
    direction: WearDirection,
    series: SeriesName = "Ra20",
) -> NominalGuess:
    """Восстановление номинала по б/у размеру (Р-08 п. 3.3).

    ``direction``:
      * ``shaft`` — изношенный вал: реальный размер был БОЛЬШЕ измеренного,
        номинал ищется вверх по ряду;
      * ``bore``  — изношенное отверстие: разбито эксплуатацией, номинал вниз;
      * ``none``  — поверхность без износа, обычное округление к ближайшему.

    Это предположение, а не решение. Решение принимает конструктор — поэтому
    результат несёт готовое обоснование для протокола.
    """
    if measured_mm <= 0:
        raise ValueError("Размер должен быть положительным")

    values = gost6636_series(series, lo=measured_mm * 0.5, hi=measured_mm * 2.0)
    if not values:
        raise ValueError(f"Ряд {series} не покрывает размер {measured_mm} мм")

    if direction == "shaft":
        candidates = [v for v in values if v >= measured_mm] or [values[-1]]
        nominal = candidates[0]
        why = "изношенный вал — номинал принят вверх по ряду"
    elif direction == "bore":
        candidates = [v for v in values if v <= measured_mm] or [values[0]]
        nominal = candidates[-1]
        why = "изношенное отверстие — номинал принят вниз по ряду"
    else:
        nominal = min(values, key=lambda v: abs(v - measured_mm))
        why = "поверхность без следов износа — округление к ближайшему значению ряда"

    inch = _inch_candidate(measured_mm)
    rationale = (
        f"Измерено {measured_mm:g} мм по б/у образцу. Принят номинал {nominal:g} мм "
        f"({series} ГОСТ 6636): {why}."
    )
    if inch is not None:
        rationale += (
            f" Внимание: размер близок к дюймовому ряду ({inch:g} мм = "
            f"{inch / 25.4:g}\"). Проверить происхождение оборудования — "
            f"Р-08 п. 3.3.3 запрещает округлять дюймовые размеры в метрику без анализа."
        )

    return NominalGuess(
        measured_mm=measured_mm,
        nominal_mm=nominal,
        series=series,
        direction=direction,
        deviation_mm=round(nominal - measured_mm, 4),
        rationale=rationale,
        inch_candidate_mm=inch,
    )


_INCH_FRACTIONS: tuple[float, ...] = tuple(n / 16 for n in range(1, 16 * 8 + 1))


def _inch_candidate(measured_mm: float, tol_mm: float = 0.12) -> float | None:
    """Ближайший дюймовый размер, если он ближе допуска (Р-08 п. 3.3.3)."""
    best: float | None = None
    best_delta = tol_mm
    for frac in _INCH_FRACTIONS:
        mm = frac * 25.4
        delta = abs(mm - measured_mm)
        if delta < best_delta:
            best_delta, best = delta, round(mm, 4)
    return best


# ─────────────────────────────────────────────────────────────────────────────
# Кольца круглого сечения — ГОСТ 9833 / ISO 3601 / AS568 (Р-10 п. 5.2)
# ─────────────────────────────────────────────────────────────────────────────

# Затравочная выборка сечений. Полные таблицы приходят справочником с сервера.
ISO3601_D2: tuple[float, ...] = (1.02, 1.60, 1.78, 1.80, 2.40, 2.62, 3.00, 3.53, 5.00, 5.33, 5.70, 6.99, 8.40)
AS568_D2: tuple[float, ...] = (1.02, 1.78, 2.62, 3.53, 5.33, 6.99)


@dataclass(frozen=True)
class ORingMatch:
    d1_mm: float
    d2_mm: float
    standard: str
    d2_deviation_mm: float
    is_standard: bool
    note: str
    d1_deviation_mm: float | None = None
    """None — ряд внутренних диаметров не загружен, d1 не сверялся."""

    d1_matched: bool = False


def oring_d1_from_perimeter(perimeter_mm: float, d2_mm: float) -> float:
    """Внутренний диаметр через периметр: d1 = P/π − d2 (Р-10 п. 5.2).

    Способ для случая, когда кольцо нельзя натянуть на калибр без растяжения —
    его обкатывают по линейке и считают.
    """
    if perimeter_mm <= 0 or d2_mm <= 0:
        raise ValueError("Периметр и сечение должны быть положительными")
    import math

    return round(perimeter_mm / math.pi - d2_mm, 3)


def match_oring(
    d1_mm: float,
    d2_mm: float,
    used: bool = True,
    d1_table: Sequence[float] | None = None,
    d2_table: Sequence[float] = ISO3601_D2,
    standard: str = "ISO 3601 / ГОСТ 9833",
    tol_d2_mm: float = 0.25,
    tol_d1_mm: float = 0.60,
) -> ORingMatch:
    """Сверка пары d1×d2 со стандартным рядом.

    Правило б/у из Р-10 п. 5.2: сечение кольца с остаточной деформацией
    ЗАНИЖЕНО, поэтому при попадании между значениями ряда берётся больший d2.
    Для нового кольца берётся ближайший.
    """
    if used:
        above = [v for v in d2_table if v >= d2_mm - 1e-9]
        d2_hit = above[0] if above else max(d2_table)
        d2_note = "б/у: сечение занижено остаточной деформацией — принят больший размер ряда"
    else:
        d2_hit = min(d2_table, key=lambda v: abs(v - d2_mm))
        d2_note = "новое кольцо — принят ближайший размер ряда"

    dev_d2 = round(d2_hit - d2_mm, 3)
    d2_ok = abs(dev_d2) <= tol_d2_mm

    # Ряд внутренних диаметров не выводится из ряда линейных размеров: у колец
    # свои таблицы. Пока справочник не загружен, честнее сказать «не сверено»,
    # чем подставить правдоподобное, но выдуманное значение.
    if not d1_table:
        note = (
            f"{d2_note}. Сечение "
            + ("совпало" if d2_ok else "НЕ совпало")
            + f" с рядом {standard} (d2 {dev_d2:+g} мм). "
            f"Внутренний диаметр не сверялся: ряд d1 не загружен на устройство. "
            f"Вывод предварительный — подтвердить по справочнику при синхронизации."
        )
        return ORingMatch(
            d1_mm=d1_mm,
            d2_mm=d2_hit,
            standard=standard,
            d2_deviation_mm=dev_d2,
            is_standard=False,
            note=note,
            d1_deviation_mm=None,
            d1_matched=False,
        )

    d1_hit = min(d1_table, key=lambda v: abs(v - d1_mm))
    dev_d1 = round(d1_hit - d1_mm, 3)
    is_standard = abs(dev_d1) <= tol_d1_mm and d2_ok

    if is_standard:
        note = (
            f"{d2_note}. Совпадение с рядом {standard} → кольцо стандартное, "
            f"категория A (Р-05): маршрут «подбор», полный РИ не требуется."
        )
    else:
        note = (
            f"{d2_note}. Отклонение от ряда {standard} больше допустимого "
            f"(d1 {dev_d1:+g} мм, d2 {dev_d2:+g} мм) → кольцо нестандартное, "
            f"категория G (Р-05), нужен полный обмер и профиль сечения."
        )

    return ORingMatch(
        d1_mm=d1_hit,
        d2_mm=d2_hit,
        standard=standard,
        d2_deviation_mm=dev_d2,
        is_standard=is_standard,
        note=note,
        d1_deviation_mm=dev_d1,
        d1_matched=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Плоские прокладки — толщина листа (Р-10 п. 5.3)
# ─────────────────────────────────────────────────────────────────────────────

SHEET_THICKNESS_MM: tuple[float, ...] = (0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0)


def gasket_thickness(measured_mm: float, used: bool = True) -> tuple[float, str]:
    """Толщина новой прокладки по обмеру б/у (Р-10 п. 5.3).

    Б/у прокладка обжата фланцем: фактическая толщина новой БОЛЬШЕ измеренной,
    поэтому округление строго вверх.
    """
    if measured_mm <= 0:
        raise ValueError("Толщина должна быть положительной")
    if used:
        above = [t for t in SHEET_THICKNESS_MM if t >= measured_mm - 1e-9]
        value = above[0] if above else max(SHEET_THICKNESS_MM)
        why = (
            f"Измерено {measured_mm:g} мм по обжатой б/у прокладке. "
            f"Принято {value:g} мм — округление вверх к ряду толщин листа (Р-10 п. 5.3); "
            f"толщину подтвердить по стандарту фланца."
        )
    else:
        value = min(SHEET_THICKNESS_MM, key=lambda t: abs(t - measured_mm))
        why = f"Измерено {measured_mm:g} мм на новой прокладке. Принято {value:g} мм — ближайший размер ряда."
    return value, why


# ─────────────────────────────────────────────────────────────────────────────
# Статистика посадочной поверхности — Р-08 п. 3.2, Р-10 п. 4.1
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SurfaceStats:
    """Итог схемы «3 сечения × 2 направления» по одной поверхности."""

    readings: tuple[float, ...]
    min_mm: float
    max_mm: float
    ovality_mm: float
    """Максимальная разница между двумя направлениями в одном сечении."""

    conicity_mm: float
    """Разброс между сечениями по длине."""

    base_mm: float
    """Размер, принимаемый за базу восстановления номинала."""

    heavy_wear: bool
    """Овальность/конусность больше половины предполагаемого допуска."""

    advice: str


def analyze_surface(
    sections: Sequence[Sequence[float]],
    surface: Literal["shaft", "bore"] = "shaft",
    expected_tolerance_mm: float = 0.05,
) -> SurfaceStats:
    """Разбор замеров посадочной поверхности б/у детали.

    ``sections`` — список сечений, в каждом два взаимно перпендикулярных
    замера. Порядок сечений — по длине: у первого края, в середине, у второго
    края (Р-10 п. 4.1).

    Базы разные и это принципиально:
      * вал — максимальный размер, обычно у краёв вне дорожки трения;
      * отверстие — наименьший стабильный размер в глубине: у кромки отверстие
        разбито «раструбом» (Р-10 п. 4.2).

    Порог «значительного износа» взят из Р-10 п. 4.1: овальность или конусность
    больше половины предполагаемого допуска.
    """
    if len(sections) < 3:
        raise ValueError("Регламент требует не менее 3 сечений (Р-10 п. 4.1)")
    if any(len(s) < 2 for s in sections):
        raise ValueError("В каждом сечении нужны 2 взаимно перпендикулярных замера")

    flat = tuple(float(v) for s in sections for v in s)
    ovality = max(max(s) - min(s) for s in sections)
    section_means = [sum(s) / len(s) for s in sections]
    conicity = max(section_means) - min(section_means)

    if surface == "shaft":
        base = max(flat)
        base_note = "за базу принят максимальный размер (края ступени вне дорожки трения)"
    else:
        # Середина списка — глубина отверстия; у кромок износ максимален.
        deep = sections[len(sections) // 2]
        base = min(deep)
        base_note = "за базу принят наименьший стабильный размер в глубине отверстия"

    heavy = ovality > expected_tolerance_mm / 2 or conicity > expected_tolerance_mm / 2
    advice = f"База замера: {base:g} мм — {base_note}."
    if heavy:
        advice += (
            f" Овальность {ovality:.3f} мм и конусность {conicity:.3f} мм превышают половину "
            f"предполагаемого допуска ({expected_tolerance_mm:g} мм) — признак значительного износа. "
            f"Р-10 п. 4.1: найти неизношенный поясок или обмерить сопрягаемую деталь."
        )

    return SurfaceStats(
        readings=flat,
        min_mm=min(flat),
        max_mm=max(flat),
        ovality_mm=round(ovality, 4),
        conicity_mm=round(conicity, 4),
        base_mm=base,
        heavy_wear=heavy,
        advice=advice,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Резьбы — Р-10 п. 4.4
# ─────────────────────────────────────────────────────────────────────────────

METRIC_PITCHES_MM: tuple[float, ...] = (
    0.25, 0.35, 0.4, 0.45, 0.5, 0.7, 0.75, 0.8, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5, 4.0,
)
INCH_TPI: tuple[int, ...] = (28, 24, 20, 18, 16, 14, 13, 12, 11, 10, 9, 8, 7, 6)


@dataclass(frozen=True)
class ThreadGuess:
    pitch_mm: float
    metric_pitch_mm: float | None
    inch_tpi: int | None
    verdict: Literal["metric", "inch", "ambiguous"]
    note: str


def classify_thread(
    span_mm: float,
    turns: int,
    tol_mm: float = 0.06,
) -> ThreadGuess:
    """Определение шага по замеру на нескольких витках.

    Р-10 п. 4.4 требует мерить на длине ≥ 5 неповреждённых витков и при сомнении
    проверять кратность обоим рядам, а не «округлять в метрику».
    """
    if turns < 5:
        raise ValueError("Р-10 п. 4.4: шаг определяется на длине не менее 5 неповреждённых витков")
    pitch = span_mm / turns

    metric = min(METRIC_PITCHES_MM, key=lambda p: abs(p - pitch))
    metric_ok = abs(metric - pitch) <= tol_mm

    tpi = min(INCH_TPI, key=lambda t: abs(25.4 / t - pitch))
    inch_ok = abs(25.4 / tpi - pitch) <= tol_mm

    if metric_ok and inch_ok:
        verdict: Literal["metric", "inch", "ambiguous"] = "ambiguous"
        note = (
            f"Шаг {pitch:.3f} мм подходит и метрическому ряду ({metric:g} мм), и дюймовому "
            f"({tpi} ниток/дюйм). Р-10 п. 4.4: определить резьбомером и калибрами, "
            f"учесть происхождение оборудования."
        )
    elif metric_ok:
        verdict = "metric"
        note = f"Шаг {pitch:.3f} мм → метрическая резьба с шагом {metric:g} мм."
    elif inch_ok:
        verdict = "inch"
        note = (
            f"Шаг {pitch:.3f} мм → дюймовая резьба {tpi} ниток/дюйм. "
            f"Не переводить в метрику (Р-08 п. 3.3.3)."
        )
    else:
        verdict = "ambiguous"
        note = (
            f"Шаг {pitch:.3f} мм не попадает ни в метрический, ни в дюймовый ряд в пределах "
            f"{tol_mm:g} мм. Возможна спецрезьба или повреждённые витки — перемерить "
            f"на другом участке, при подтверждении завести как категорию F (Р-05)."
        )

    return ThreadGuess(
        pitch_mm=round(pitch, 4),
        metric_pitch_mm=metric if metric_ok else None,
        inch_tpi=tpi if inch_ok else None,
        verdict=verdict,
        note=note,
    )
