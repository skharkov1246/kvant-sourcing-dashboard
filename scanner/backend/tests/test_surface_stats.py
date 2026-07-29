"""Серверная статистика сетки замеров 3×2 (Р-10 п.4.1, каталог kv_cat_c).

Смысл набора: оператор снимает сырые числа — овальность, конусность и базу
считает сервер; неполная сетка помечается честной ошибкой, а не считается
«как получится»; статистика доезжает до строки реестра.
"""

import uuid

import pytest

from app.export import surface_stats_from_grid
from tests.conftest import OPERATOR, REVIEWER


def _row(step_no, surface, section, direction, value):
    return {"step_no": step_no, "surface": surface, "section": section,
            "direction": direction, "value_mm": value, "instrument": "МК-25 №123"}


def _full_grid(step_no=1, surface="вал", base=50.0):
    """Полная сетка: у края 2 максимальный размер — там нет дорожки трения."""
    return [
        _row(step_no, surface, "край 1", "0°", base + 0.01),
        _row(step_no, surface, "край 1", "90°", base + 0.02),
        _row(step_no, surface, "середина", "0°", base - 0.03),
        _row(step_no, surface, "середина", "90°", base - 0.01),
        _row(step_no, surface, "край 2", "0°", base + 0.04),
        _row(step_no, surface, "край 2", "90°", base + 0.03),
    ]


class TestGridStats:
    def test_shaft_base_is_max_reading(self):
        stats = surface_stats_from_grid(_full_grid())
        assert len(stats) == 1
        s = stats[0]
        assert s["base_mm"] == 50.04  # вал: база — максимум (края вне трения)
        assert s["ovality_mm"] == pytest.approx(0.02, abs=1e-9)
        assert "error" not in s

    def test_bore_base_is_min_stable_in_depth(self):
        """Отверстие: у кромок раструб, база — наименьший размер в глубине."""
        stats = surface_stats_from_grid(_full_grid(surface="отверстие"))
        assert stats[0]["base_mm"] == 49.97  # минимум серединного сечения

    def test_each_step_and_surface_counted_separately(self):
        rows = _full_grid(1, "вал", 50.0) + _full_grid(2, "вал", 80.0) \
            + _full_grid(2, "отверстие", 30.0)
        stats = surface_stats_from_grid(rows)
        assert [(s["step_no"], s["surface"]) for s in stats] == [
            (1, "вал"), (2, "вал"), (2, "отверстие")]

    def test_incomplete_grid_is_flagged_not_guessed(self):
        rows = _full_grid()[:4]  # только 2 сечения
        stats = surface_stats_from_grid(rows)
        assert stats[0]["error"].startswith("сетка неполна")
        assert "base_mm" not in stats[0]

    def test_heavy_wear_flag_raises_advice(self):
        rows = _full_grid()
        rows[0]["value_mm"] = 50.2  # овальность в крае 1 — 0.18 мм
        s = surface_stats_from_grid(rows)[0]
        assert s["heavy_wear"] is True
        assert "неизношенный поясок" in s["advice"]


class TestStatsReachRegistry:
    def _event(self, session_id, seq, etype, payload):
        return {"client_event_id": str(uuid.uuid4()), "session_id": session_id,
                "seq": seq, "type": etype, "device_ts": "2026-07-29T03:00:00Z",
                "payload": payload}

    def test_grid_step_produces_surface_stats_in_export_row(self, client):
        """Repeat-шаг сетки (payload — список) → surface_stats строки реестра."""
        client.post("/v1/sync/events", json={"device_id": "d1", "events": [
            self._event("s-grid-1", 1, "session.started", {"protocol": "pallet_general@7"}),
            self._event("s-grid-1", 2, "step.completed",
                        {"step_id": "c_grid", "data": _full_grid()}),
            self._event("s-grid-1", 3, "session.completed", {}),
        ]}, headers=OPERATOR)
        row = client.get("/v1/export/sessions", headers=REVIEWER).json()["items"][0]
        assert row["surface_stats"][0]["base_mm"] == 50.04
        assert row["surface_stats"][0]["ovality_mm"] == pytest.approx(0.02)

    def test_session_without_grid_has_no_stats(self, client):
        client.post("/v1/sync/events", json={"device_id": "d1", "events": [
            self._event("s-grid-2", 1, "session.started", {"protocol": "pallet_general@7"}),
            self._event("s-grid-2", 2, "step.completed", {"step_id": "overview"}),
            self._event("s-grid-2", 3, "session.completed", {}),
        ]}, headers=OPERATOR)
        row = client.get("/v1/export/sessions", headers=REVIEWER).json()["items"][0]
        assert row["surface_stats"] is None
