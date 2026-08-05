"""Масштаб кадра по опорному маркеру ArUco (Р-05: «предмет известного размера»).

Дешёвый скачок точности без AR: оператор кладёт в кадр напечатанную
карточку-маркер (сторона известна, например 50 мм) — сервер находит её и
считает мм/пиксель. Дальше любые два тапа оператора по кадру превращаются
в миллиметры; класс точности C (маркер в кадре) вместо D (на глаз).

Почему сервер, а не телефон: считаем по уже загруженному кадру — телефону
не нужны ни OpenCV, ни обновление приложения при смене логики; плюс расчёт
воспроизводим при пересмотре спора (сырьё храним — ADR-0004).

Ограничение честно объявлено: мм/пиксель верен В ПЛОСКОСТИ маркера.
Перекос плоскости оценивается по искажению квадрата маркера; сильный
перекос — предупреждение, а не молчаливо кривые миллиметры.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Словарь по умолчанию: 4x4 — крупные ячейки, читается с расстояния и в
#: смазе лучше плотных словарей; 50 идентификаторов складу за глаза.
DEFAULT_DICT = "DICT_4X4_50"

#: Перекос (отношение сторон квадрата маркера) хуже этого — предупреждение.
SKEW_WARN_RATIO = 0.85


class ScaleError(ValueError):
    """Маркер не найден или непригоден — честный отказ вместо кривых мм."""


@dataclass
class FrameScale:
    mm_per_px: float
    marker_id: int
    marker_size_mm: float
    #: 1.0 — маркер снят строго в лоб; ниже — плоскость перекошена.
    skew_ratio: float
    warnings: list[str]

    def distance_mm(self, p1: tuple[float, float], p2: tuple[float, float]) -> float:
        """Два тапа оператора по кадру → миллиметры в плоскости маркера."""
        dx, dy = p1[0] - p2[0], p1[1] - p2[1]
        return float((dx * dx + dy * dy) ** 0.5) * self.mm_per_px


def detect_scale(image_bgr: np.ndarray, marker_size_mm: float,
                 dictionary: str = DEFAULT_DICT) -> FrameScale:
    """Находит маркер и считает мм/пиксель по средней стороне его квадрата."""
    import cv2

    aruco_dict = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary))
    detector = cv2.aruco.ArucoDetector(aruco_dict, cv2.aruco.DetectorParameters())
    corners, ids, _ = detector.detectMarkers(image_bgr)
    if ids is None or len(ids) == 0:
        raise ScaleError(
            "Опорный маркер не найден в кадре — положите карточку-маркер "
            "рядом с деталью и переснимите")

    # Несколько маркеров — берём самый крупный: он ближе к детали и к камере,
    # его пиксельная сторона оценена точнее.
    def side_px(quad: np.ndarray) -> float:
        pts = quad.reshape(4, 2)
        sides = [float(np.linalg.norm(pts[i] - pts[(i + 1) % 4])) for i in range(4)]
        return sum(sides) / 4.0

    best = max(range(len(ids)), key=lambda i: side_px(corners[i]))
    pts = corners[best].reshape(4, 2)
    sides = [float(np.linalg.norm(pts[i] - pts[(i + 1) % 4])) for i in range(4)]
    mean_side = sum(sides) / 4.0
    skew = min(sides) / max(sides)

    warnings: list[str] = []
    if skew < SKEW_WARN_RATIO:
        warnings.append(
            "маркер снят под сильным углом — размеры вне его плоскости "
            "будут занижены, переснимите ближе к перпендикуляру")

    return FrameScale(
        mm_per_px=marker_size_mm / mean_side,
        marker_id=int(ids.flatten()[best]),
        marker_size_mm=marker_size_mm,
        skew_ratio=skew,
        warnings=warnings,
    )


def render_marker_png(marker_id: int = 7, size_px: int = 600,
                      dictionary: str = DEFAULT_DICT) -> bytes:
    """PNG маркера для печати — раздаётся эндпоинтом, чтобы склад мог
    напечатать карточку сам (сторона печатается известного размера)."""
    import cv2

    aruco_dict = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary))
    image = cv2.aruco.generateImageMarker(aruco_dict, marker_id, size_px)
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise ScaleError("не удалось закодировать маркер в PNG")
    return bytes(buf)
