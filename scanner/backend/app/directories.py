"""Справочники, синхронизируемые на устройства (Р-03 разд. 6).

Принцип из series.py: «код — это алгоритм подбора, а не база данных
стандартов». Таблицы рядов живут в data/directories/*.json, их расширяет
владелец реестра без правки кода; здесь — только загрузка и валидация.

Правила:
  * затравочные значения в JSON обязаны совпадать с константами series.py —
    за этим следит tests/test_directories.py, поэтому две копии разойтись
    не могут;
  * пустая секция (например, ряд d1 колец) — это явное «данных нет», и
    потребитель обязан вести себя честно (match_oring: «d1 не сверялся»),
    а не подставлять правдоподобное значение.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Sequence

_DIRECTORY_ROOT = Path(__file__).resolve().parent.parent / "data" / "directories"

_REQUIRED_SECTIONS = frozenset({
    "gost6636_ra40_decade",
    "oring_d2_iso3601",
    "oring_d2_as568",
    "oring_d1_iso3601",
    "gasket_sheet_thickness",
    "thread_metric_pitches",
    "thread_inch_tpi",
})


_MATERIALS_SECTIONS = frozenset({
    "steel_structural",
    "steel_stainless",
    "cast_iron",
    "bronze_brass",
    "polymer",
    "elastomer",
    "coating",
})


_SORTAMENT_SECTIONS = frozenset({
    "angle_equal",
    "channel",
    "i_beam",
    "pipe_round",
    "pipe_profile",
})


class DirectoryError(RuntimeError):
    """Справочник отсутствует или сломан — это ошибка развёртывания, не данных."""


def _load_and_check(filename: str, required_sections: frozenset[str],
                    path: Path | None = None) -> dict:
    """Общая загрузка со структурной проверкой.

    Ряды бывают двух видов, и вид обязан быть однородным в секции:
      * числовые (ряды размеров) — строго по возрастанию, без дубликатов;
      * строковые (марки материалов, виды покрытий) — без дубликатов и
        пустых строк; порядок смысловой, сортировка не навязывается.
    """
    p = path or (_DIRECTORY_ROOT / filename)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise DirectoryError(f"Справочник не найден: {p}") from e
    except json.JSONDecodeError as e:
        raise DirectoryError(f"Справочник повреждён: {p}: {e}") from e

    sections = doc.get("sections")
    if not isinstance(sections, dict):
        raise DirectoryError("В справочнике нет объекта sections")
    missing = required_sections - sections.keys()
    if missing:
        raise DirectoryError(f"В справочнике нет секций: {sorted(missing)}")
    for name, section in sections.items():
        values = section.get("values")
        if not isinstance(values, list):
            raise DirectoryError(f"Секция {name}: values должен быть списком")
        numeric = [isinstance(v, (int, float)) and not isinstance(v, bool) for v in values]
        textual = [isinstance(v, str) for v in values]
        if all(numeric):
            if values != sorted(values):
                raise DirectoryError(f"Секция {name}: значения ряда должны идти по возрастанию")
        elif all(textual):
            if any(not v.strip() for v in values):
                raise DirectoryError(f"Секция {name}: пустая строка в ряду")
        else:
            raise DirectoryError(f"Секция {name}: ряд должен быть однородным (числа или строки)")
        if len(set(values)) != len(values):
            raise DirectoryError(f"Секция {name}: в ряду есть дубликаты")
    return doc


@lru_cache(maxsize=1)
def load_series_directory(path: Path | None = None) -> dict:
    """Справочник стандартных рядов (размеры, ГОСТ 6636, кольца, резьбы)."""
    return _load_and_check("series_directory.json", _REQUIRED_SECTIONS, path)


@lru_cache(maxsize=1)
def load_materials_directory(path: Path | None = None) -> dict:
    """Справочник материалов и покрытий (Р-08 разд. 4): оператор выбирает
    марку из списка, а не печатает от руки."""
    return _load_and_check("materials_directory.json", _MATERIALS_SECTIONS, path)


@lru_cache(maxsize=1)
def load_sortament_directory(path: Path | None = None) -> dict:
    """Сортамент проката для сверки элементов сварных узлов (kv_cat_h):
    совпадение с сортаментом = элемент покупной, в РКД — ссылка на стандарт."""
    return _load_and_check("sortament_directory.json", _SORTAMENT_SECTIONS, path)


def series_values(section: str) -> Sequence[float]:
    """Значения ряда по имени секции; пустой список — «данных нет» (это честно)."""
    doc = load_series_directory()
    if section not in doc["sections"]:
        raise DirectoryError(f"Нет такой секции справочника: {section}")
    return tuple(doc["sections"][section]["values"])


def oring_d1_table() -> Sequence[float] | None:
    """Ряд d1 для match_oring: None, пока владелец реестра его не заполнил.

    None вместо пустого кортежа — намеренно: сигнатура match_oring трактует
    отсутствие таблицы как «d1 не сверялся» и пишет это в вывод.
    """
    values = series_values("oring_d1_iso3601")
    return values or None
