"""Источники геометрии: рулетка, маркер, AR, LiDAR, фотограмметрия,
структурированный свет, промышленный сканер — четыре порядка точности.

Смысл набора: методы различаются в тысячи раз по погрешности, и система
обязана держать их вместе, не давая «точному» молча затереть исходный и не
выдавая одно число без происхождения.
"""

import pytest

from app.geometry import (
    GeometryArtifact,
    GeometryError,
    best_dimensions,
    measurement_conflicts,
    refinement_chain,
)
from tests.conftest import OPERATOR, REVIEWER


def _art(source, dims, **kw) -> GeometryArtifact:
    defaults = {"item_id": "i-1", "dimensions_mm": dims}
    if source in ("manual_tape", "photo_marker", "photogrammetry"):
        defaults["evidence_asset_ids"] = ["a-1"]
    if source in ("structured_light", "industrial_scanner"):
        defaults["instrument_id"] = "SCAN-01"
    defaults.update(kw)
    return GeometryArtifact(source=source, **defaults)


class TestSourceRules:
    def test_scanner_without_instrument_is_refused(self):
        """Поверяемое средство без идентификатора — замер без основания:
        по нему нельзя проверить, действует ли поверка (Р-08)."""
        with pytest.raises(GeometryError, match="средства измерения"):
            GeometryArtifact(item_id="i", source="industrial_scanner",
                             dimensions_mm={"d": 100.0})

    def test_manual_measurement_requires_photo_evidence(self):
        """Класс D без фото рулетки — способ закрыть задание из подсобки."""
        with pytest.raises(GeometryError, match="кадры-основания"):
            GeometryArtifact(item_id="i", source="manual_tape",
                             dimensions_mm={"d": 100.0})

    def test_photogrammetry_needs_frames_for_scale(self):
        """Без кадров с маркером облако точек безразмерное — заявленная
        точность 1 мм была бы враньём."""
        with pytest.raises(GeometryError, match="кадры-основания"):
            GeometryArtifact(item_id="i", source="photogrammetry",
                             dimensions_mm={"d": 100.0})

    def test_point_cloud_without_file_is_refused(self):
        with pytest.raises(GeometryError, match="в базе — ссылка"):
            _art("lidar", {}, kind="point_cloud")

    def test_default_tolerance_comes_from_the_method(self):
        assert _art("industrial_scanner", {"d": 100.0}).tolerance_mm == 0.03
        assert _art("manual_tape", {"d": 100.0}).tolerance_mm == 5.0

    def test_certified_tolerance_overrides_passport_value(self):
        """У поверенного сканера погрешность берётся из свидетельства,
        а не из справочника методов."""
        assert _art("industrial_scanner", {"d": 100.0},
                    tolerance_mm=0.018).tolerance_mm == 0.018

    def test_unknown_source_is_refused(self):
        with pytest.raises(GeometryError, match="Неизвестный источник"):
            GeometryArtifact(item_id="i", source="на_глаз", dimensions_mm={"d": 1})


class TestBestValue:
    def test_most_precise_method_wins_but_keeps_the_others(self):
        best = best_dimensions([
            _art("manual_tape", {"d_outer": 100.0}),
            _art("industrial_scanner", {"d_outer": 100.04}),
            _art("lidar", {"d_outer": 100.2}),
        ])
        assert best["d_outer"]["value_mm"] == 100.04
        assert best["d_outer"]["source"] == "industrial_scanner"
        # Одного числа без происхождения в этой системе не бывает.
        assert len(best["d_outer"]["others"]) == 2

    def test_dimensions_from_different_methods_merge_by_key(self):
        best = best_dimensions([
            _art("industrial_scanner", {"d_outer": 100.04}),
            _art("manual_tape", {"length": 250.0}),
        ])
        assert set(best) == {"d_outer", "length"}
        assert best["length"]["source"] == "manual_tape"


class TestConflicts:
    def test_disagreement_beyond_tolerances_is_a_dispute(self):
        """Сканер ±0.03 говорит 100, рулетка ±5 говорит 250 — это не
        «рулетка ошиблась», это мерили разные детали."""
        conflicts = measurement_conflicts([
            _art("industrial_scanner", {"d": 100.0}),
            _art("manual_tape", {"d": 250.0}),
        ])
        assert len(conflicts) == 1
        assert conflicts[0]["delta_mm"] == 150.0
        assert "одну деталь" in conflicts[0]["note"]

    def test_agreement_within_tolerances_is_not_a_conflict(self):
        assert measurement_conflicts([
            _art("industrial_scanner", {"d": 100.00}),
            _art("manual_tape", {"d": 102.0}),   # в пределах ±5 + ±0.03
        ]) == []

    def test_refinement_chain_leads_back_to_the_source(self):
        first = _art("manual_tape", {"d": 100.0}, id="g1")
        second = _art("photogrammetry", {"d": 100.4}, id="g2", refined_from="g1")
        third = _art("industrial_scanner", {"d": 100.42}, id="g3", refined_from="g2")
        assert refinement_chain([first, second, third], "g3") == ["g3", "g2", "g1"]


class TestGeometryApi:
    def test_scanner_and_phone_land_on_one_card(self, client):
        """Цеховой сканер и телефон кладовщика пишут в одну дверь."""
        phone = client.post("/v1/items/i-geo/geometry", headers=OPERATOR, json={
            "source": "lidar", "dimensions_mm": {"d_outer": 100.2}})
        assert phone.status_code == 201
        assert phone.json()["tolerance_mm"] == 4.0

        scanner = client.post("/v1/items/i-geo/geometry", headers=REVIEWER, json={
            "source": "industrial_scanner", "dimensions_mm": {"d_outer": 100.04},
            "instrument_id": "SCAN-01", "tolerance_mm": 0.03})
        assert scanner.status_code == 201

        doc = client.get("/v1/items/i-geo/geometry", headers=OPERATOR).json()
        assert len(doc["artifacts"]) == 2
        assert doc["best_dimensions"]["d_outer"]["source"] == "industrial_scanner"
        assert doc["conflicts"] == []

    def test_refinement_adds_a_record_and_never_overwrites(self, client):
        first = client.post("/v1/items/i-ref/geometry", headers=OPERATOR, json={
            "source": "manual_tape", "dimensions_mm": {"length": 250.0},
            "evidence_asset_ids": ["a-tape"]}).json()
        client.post("/v1/items/i-ref/geometry", headers=OPERATOR, json={
            "source": "photogrammetry", "dimensions_mm": {"length": 251.3},
            "evidence_asset_ids": ["a-frame-1"], "refined_from": first["id"]})

        doc = client.get("/v1/items/i-ref/geometry", headers=OPERATOR).json()
        assert len(doc["artifacts"]) == 2      # исходный замер на месте
        assert doc["best_dimensions"]["length"]["source"] == "photogrammetry"

    def test_conflict_is_reported_to_the_human(self, client):
        client.post("/v1/items/i-conf/geometry", headers=OPERATOR, json={
            "source": "industrial_scanner", "dimensions_mm": {"d": 100.0},
            "instrument_id": "SCAN-01"})
        client.post("/v1/items/i-conf/geometry", headers=OPERATOR, json={
            "source": "manual_tape", "dimensions_mm": {"d": 250.0},
            "evidence_asset_ids": ["a-tape"]})
        doc = client.get("/v1/items/i-conf/geometry", headers=OPERATOR).json()
        assert doc["conflicts"][0]["delta_mm"] == 150.0

    def test_bad_input_is_refused_with_reason(self, client):
        r = client.post("/v1/items/i-bad/geometry", headers=OPERATOR, json={
            "source": "industrial_scanner", "dimensions_mm": {"d": 100.0}})
        assert r.status_code == 422
        assert "средства измерения" in r.json()["detail"]["message"]
