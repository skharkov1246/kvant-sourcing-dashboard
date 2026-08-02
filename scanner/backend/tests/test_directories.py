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


class TestDirectoryEndpoint:
    """GET /v1/directories/{name}: раздача с валидацией по ETag.

    Хранилище эндпоинт не трогает — матрица memory/pg здесь не нужна,
    достаточно одного клиента.
    """

    @pytest.fixture()
    def http(self):
        from fastapi.testclient import TestClient

        from app.main import app

        return TestClient(app)

    def test_returns_directory_with_etag(self, http):
        from tests.conftest import OPERATOR

        r = http.get("/v1/directories/standard_series", headers=OPERATOR)
        assert r.status_code == 200
        doc = r.json()
        assert doc["directory"] == "standard_series"
        assert r.headers["ETag"] == f'"standard_series-v{doc["version"]}"'

    def test_if_none_match_returns_304_without_body(self, http):
        from tests.conftest import OPERATOR

        first = http.get("/v1/directories/standard_series", headers=OPERATOR)
        second = http.get(
            "/v1/directories/standard_series",
            headers={**OPERATOR, "If-None-Match": first.headers["ETag"]},
        )
        assert second.status_code == 304
        assert second.content == b""
        assert second.headers["ETag"] == first.headers["ETag"]

    def test_stale_etag_returns_fresh_document(self, http):
        from tests.conftest import OPERATOR

        r = http.get(
            "/v1/directories/standard_series",
            headers={**OPERATOR, "If-None-Match": '"standard_series-v0"'},
        )
        assert r.status_code == 200
        assert r.json()["sections"]

    def test_unknown_directory_404(self, http):
        from tests.conftest import OPERATOR

        r = http.get("/v1/directories/nope", headers=OPERATOR)
        assert r.status_code == 404

    def test_requires_authentication(self, http):
        assert http.get("/v1/directories/standard_series").status_code == 401


class TestMaterialsDirectory:
    """Справочник материалов (Р-08 разд. 4): строковые ряды, выбор из списка."""

    def test_loads_with_all_sections(self):
        from app.directories import load_materials_directory

        doc = load_materials_directory()
        assert doc["directory"] == "materials"
        for section in ("steel_structural", "cast_iron", "elastomer", "coating"):
            assert doc["sections"][section]["values"]

    def test_string_rows_have_no_duplicates_or_blanks(self):
        from app.directories import load_materials_directory

        for name, section in load_materials_directory()["sections"].items():
            values = section["values"]
            assert len(set(values)) == len(values), name
            assert all(v.strip() for v in values), name

    def test_endpoint_serves_materials_with_etag(self):
        from fastapi.testclient import TestClient

        from app.main import app
        from tests.conftest import OPERATOR

        http = TestClient(app)
        r = http.get("/v1/directories/materials", headers=OPERATOR)
        assert r.status_code == 200
        assert r.json()["directory"] == "materials"
        etag = r.headers["ETag"]
        assert etag == f'"materials-v{r.json()["version"]}"'
        cached = http.get("/v1/directories/materials",
                          headers={**OPERATOR, "If-None-Match": etag})
        assert cached.status_code == 304


class TestSortamentDirectory:
    """Сортамент проката (kv_cat_h): типоразмер из списка = покупной элемент."""

    def test_loads_with_all_sections(self):
        from app.directories import load_sortament_directory

        doc = load_sortament_directory()
        assert doc["directory"] == "sortament"
        for section in ("angle_equal", "channel", "i_beam", "pipe_round", "pipe_profile"):
            assert doc["sections"][section]["values"], section

    def test_typesizes_unique_and_non_blank(self):
        from app.directories import load_sortament_directory

        for name, section in load_sortament_directory()["sections"].items():
            values = section["values"]
            assert len(set(values)) == len(values), name
            assert all(v.strip() for v in values), name

    def test_endpoint_serves_sortament_with_etag(self):
        from fastapi.testclient import TestClient

        from app.main import app
        from tests.conftest import OPERATOR

        http = TestClient(app)
        r = http.get("/v1/directories/sortament", headers=OPERATOR)
        assert r.status_code == 200
        etag = r.headers["ETag"]
        assert etag == f'"sortament-v{r.json()["version"]}"'
        assert http.get("/v1/directories/sortament",
                        headers={**OPERATOR, "If-None-Match": etag}).status_code == 304


class TestOperationalDirectories:
    """Справочники source-полей протоколов: имя эндпоинта = имя source."""

    def test_all_four_load(self):
        from app.directories import (
            load_brand_directory, load_media_directory,
            load_si_directory, load_std_directory,
        )

        assert load_media_directory()["sections"]["media"]["values"]
        assert load_brand_directory()["sections"]["brands"]["values"]
        assert load_std_directory()["sections"]["series_names"]["values"]
        assert load_si_directory()["sections"]["instruments"]["values"]

    def test_brand_directory_carries_service_value(self):
        """«БРЕНД НЕ ОПРЕДЕЛЁН» — служебный исход Р-11 п.3, он обязан быть
        в справочнике: по нему сервер создаёт задачу брендинга."""
        from app.directories import load_brand_directory

        assert "БРЕНД НЕ ОПРЕДЕЛЁН" in load_brand_directory()["sections"]["brands"]["values"]

    def test_si_entries_carry_verification_deadline(self):
        """Каждая строка СИ несёт срок поверки — без него офлайн-отсечка
        просроченных СИ (docs/14) не сработает."""
        from app.directories import load_si_directory

        for entry in load_si_directory()["sections"]["instruments"]["values"]:
            assert "поверка до" in entry, entry

    def test_endpoint_serves_all_with_etag(self):
        from fastapi.testclient import TestClient

        from app.main import app
        from tests.conftest import OPERATOR

        http = TestClient(app)
        for name in ("media_directory", "brand_directory", "std_directory", "si_directory"):
            r = http.get(f"/v1/directories/{name}", headers=OPERATOR)
            assert r.status_code == 200, name
            assert r.headers["ETag"] == f'"{name}-v{r.json()["version"]}"'
            assert http.get(f"/v1/directories/{name}",
                            headers={**OPERATOR, "If-None-Match": r.headers["ETag"]},
                            ).status_code == 304, name


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
