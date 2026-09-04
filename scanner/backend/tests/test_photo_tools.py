"""Фото-инструменты: масштаб по опорному маркеру и чтение шильдика.

Смысл набора: «предмет известного размера» из Р-05 становится расчётом,
а не советом — сервер находит напечатанную карточку в кадре и превращает
тапы оператора в миллиметры; чтение шильдика деградирует честно.
Все кадры синтетические — тесты не требуют ни камеры, ни сети.
"""

import hashlib

import numpy as np
import pytest

from tests.conftest import OPERATOR

cv2 = pytest.importorskip("cv2")

from app.photo_scale import (  # noqa: E402
    DEFAULT_DICT, FrameScale, ScaleError, detect_scale, render_marker_png,
)


def synthetic_frame(marker_px=200, canvas=(720, 960), marker_id=7,
                    angle_deg=0.0) -> np.ndarray:
    """Кадр «стол с карточкой»: серый фон, маркер известного размера."""
    aruco = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, DEFAULT_DICT))
    marker = cv2.aruco.generateImageMarker(aruco, marker_id, marker_px)
    frame = np.full((canvas[0], canvas[1], 3), 120, dtype=np.uint8)
    y, x = 80, 100
    marker_bgr = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
    if angle_deg:
        # Перекос плоскости имитируем сжатием по горизонтали.
        squeeze = int(marker_px * np.cos(np.radians(angle_deg)))
        marker_bgr = cv2.resize(marker_bgr, (squeeze, marker_px))
    frame[y:y + marker_bgr.shape[0], x:x + marker_bgr.shape[1]] = marker_bgr
    return frame


class TestScale:
    def test_mm_per_px_is_accurate(self):
        """Маркер 200 px при стороне 50 мм → 0.25 мм/px с точностью 2%."""
        scale = detect_scale(synthetic_frame(marker_px=200), marker_size_mm=50)
        assert scale.marker_id == 7
        assert abs(scale.mm_per_px - 0.25) / 0.25 < 0.02
        assert scale.warnings == []

    def test_operator_taps_become_millimetres(self):
        scale = FrameScale(mm_per_px=0.25, marker_id=7, marker_size_mm=50,
                           skew_ratio=1.0, warnings=[])
        assert scale.distance_mm((100, 100), (500, 100)) == pytest.approx(100.0)

    def test_skewed_marker_warns_instead_of_lying(self):
        """Сильный перекос — предупреждение, а не молчаливо кривые мм."""
        scale = detect_scale(synthetic_frame(angle_deg=45), marker_size_mm=50)
        assert scale.warnings, "перекос 45° обязан дать предупреждение"

    def test_no_marker_is_an_honest_error(self):
        empty = np.full((300, 400, 3), 120, dtype=np.uint8)
        with pytest.raises(ScaleError, match="не найден"):
            detect_scale(empty, marker_size_mm=50)

    def test_printable_marker_roundtrip(self):
        """Напечатанный нами маркер находится нашим же детектором."""
        png = render_marker_png(marker_id=3, size_px=300)
        image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
        frame = np.full((500, 500, 3), 200, dtype=np.uint8)
        frame[100:400, 100:400] = image
        assert detect_scale(frame, marker_size_mm=40).marker_id == 3


class TestScaleEndpoint:
    def _upload_frame(self, client, frame) -> str:
        ok, buf = cv2.imencode(".png", frame)
        payload = bytes(buf)
        digest = hashlib.sha256(payload).hexdigest()
        init = client.post("/v1/assets", json={
            "session_id": "s-scale", "step_id": "dims", "kind": "photo",
            "mime": "image/png", "bytes": len(payload), "sha256": digest,
        }, headers=OPERATOR).json()
        n = 0
        size = init["chunk_size"]
        while n * size < len(payload):
            client.put(f"/v1/assets/{init['asset_id']}/chunks/{n}",
                       content=payload[n * size:(n + 1) * size], headers=OPERATOR)
            n += 1
        assert client.post(f"/v1/assets/{init['asset_id']}/complete",
                           headers=OPERATOR).json()["status"] == "verified"
        return init["asset_id"]

    def test_scale_and_distance_over_http(self, client):
        asset_id = self._upload_frame(client, synthetic_frame(marker_px=200))
        r = client.post(f"/v1/assets/{asset_id}/scale", headers=OPERATOR, json={
            "marker_size_mm": 50, "points": [[100, 100], [500, 100]]})
        assert r.status_code == 200
        doc = r.json()
        assert abs(doc["mm_per_px"] - 0.25) / 0.25 < 0.02
        assert abs(doc["distance_mm"] - 100.0) < 3

    def test_frame_without_marker_422_with_instruction(self, client):
        asset_id = self._upload_frame(
            client, np.full((300, 400, 3), 120, dtype=np.uint8))
        r = client.post(f"/v1/assets/{asset_id}/scale", headers=OPERATOR,
                        json={"marker_size_mm": 50})
        assert r.status_code == 422
        assert "положите карточку" in r.json()["detail"]["message"]

    def test_marker_png_is_served_for_printing(self, client):
        r = client.get("/v1/tools/marker.png?marker_id=5", headers=OPERATOR)
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


class TestNameplate:
    class _CannedTransport:
        def complete(self, **kwargs):
            from app.acceptance import TransportResult

            assert kwargs["images"], "кадр обязан уехать в модель"
            return TransportResult(stop_reason="end_turn", model="claude-sonnet-5", text=(
                '{"legible": true, "confidence": 0.93, "brand": "WARMAN", '
                '"model_designation": "6/4 D-AH", "serial_number": "4471-22", '
                '"article": null, "extra_fields": '
                '[{"label": "ГОСТ", "value": "22247-96"}]}'))

    def test_reading_parses_into_structured_fields(self):
        from app.model_registry import TenantDataPolicy
        from app.nameplate import read_nameplate

        reading = read_nameplate(b"png-bytes", "image/png", self._CannedTransport(),
                                 TenantDataPolicy(tenant_id="t-internal", retention_days=30))
        assert reading.brand == "WARMAN"
        assert reading.serial_number == "4471-22"
        assert reading.article is None  # не видно — null, а не выдумка
        assert reading.extra_fields == [{"label": "ГОСТ", "value": "22247-96"}]

    def test_unavailable_arm_degrades_to_none(self):
        """Нет ключа/сети — None: поле остаётся ручному вводу, не падение."""
        from app.acceptance import TransportUnavailable
        from app.model_registry import TenantDataPolicy
        from app.nameplate import read_nameplate

        class Dead:
            def complete(self, **_):
                raise TransportUnavailable("нет ключа")

        assert read_nameplate(b"x", "image/png", Dead(),
                              TenantDataPolicy(tenant_id="t", retention_days=30)) is None

    def test_endpoint_degrades_honestly_without_key(self, client):
        """NullTransport дев-стенда → available=false с человеческим текстом."""
        frame = np.full((60, 90, 3), 200, dtype=np.uint8)
        ok, buf = cv2.imencode(".png", frame)
        payload = bytes(buf)
        digest = hashlib.sha256(payload).hexdigest()
        init = client.post("/v1/assets", json={
            "session_id": "s-np", "step_id": "label", "kind": "photo",
            "mime": "image/png", "bytes": len(payload), "sha256": digest,
        }, headers=OPERATOR).json()
        client.put(f"/v1/assets/{init['asset_id']}/chunks/0",
                   content=payload, headers=OPERATOR)
        client.post(f"/v1/assets/{init['asset_id']}/complete", headers=OPERATOR)

        r = client.post(f"/v1/assets/{init['asset_id']}/nameplate", headers=OPERATOR)
        assert r.status_code == 200
        assert r.json()["available"] is False
        assert "вручную" in r.json()["message"]
