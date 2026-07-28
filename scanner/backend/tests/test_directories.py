"""Справочник рядов как данные (Р-03 разд. 6).

Смысл набора: таблицы стандартов живут в JSON-справочнике, а не в коде, и две
копии (затравка в series.py и справочник) не могут разойтись незаметно —
расхождение роняет тест, а не тихо меняет категорию детали.
"""

import json
from pathlib import Path

import pytest

from app import series
from app.directories import (
    DirectoryError,
    load_series_directory,
    oring_d1_table,
    series_values,
)

DIRECTORY = Path(__file__).resolve().parent.parent / "data" / "directories" / "series_directory.json"


class TestConsistencyWithCode:
    """Затравочные таблицы в series.py и справочник — одно и то же."""

    def test_ra40_decade_matches(self):
        assert tuple(series_values("gost6636_ra40_decade")) == series._RA40_DECADE

    def test_iso3601_d2_matches(self):
        assert tuple(series_values("oring_d2_iso3601")) == series.ISO3601_D2

    def test_as568_d2_matches(self):
        assert tuple(series_values("oring_d2_as568")) == series.AS568_D2

    def test_sheet_thickness_matches(self):
        assert tuple(series_values("gasket_sheet_thickness")) == series.SHEET_THICKNESS_MM

    def test_metric_pitches_match(self):
        assert tuple(series_values("thread_metric_pitches")) == series.METRIC_PITCHES_MM

    def test_inch_tpi_matches(self):
        """TPI в справочнике по возрастанию, в коде по убыванию — сверяем множества
        и то, что оба отсортированы каждый в свою сторону."""
        assert sorted(series_values("thread_inch_tpi")) == sorted(series.INCH_TPI)


class TestDirectoryStructure:
    def test_loads_and_has_version(self):
        doc = load_series_directory()
        assert doc["directory"] == "standard_series"
        assert isinstance(doc["version"], int) and doc["version"] >= 1

    def test_unknown_section_is_an_error(self):
        with pytest.raises(DirectoryError, match="Нет такой секции"):
            series_values("ряд_которого_нет")

    def test_rows_are_sorted_and_unique(self):
        """Правило зашито в загрузчик; здесь проверяем сам файл напрямую,
        чтобы будущая правка загрузчика не спрятала сломанный справочник."""
        doc = json.loads(DIRECTORY.read_text(encoding="utf-8"))
        for name, section in doc["sections"].items():
            values = section["values"]
            assert values == sorted(values), name
            assert len(set(values)) == len(values), name


class TestHonestEmptyD1:
    def test_d1_pending_returns_none(self):
        """Пока владелец реестра не заполнил ряд d1 — None, а не пустой список:
        match_oring по None пишет «d1 не сверялся», это регламентно честно."""
        assert oring_d1_table() is None

    def test_match_oring_with_directory_d1(self):
        match = series.match_oring(d1_mm=24.9, d2_mm=3.4, used=True,
                                   d1_table=oring_d1_table())
        assert match.d1_matched is False
        assert match.d1_deviation_mm is None
        assert "не сверялся" in match.note
        assert match.is_standard is False
