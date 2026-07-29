"""Модуль и смещение зубчатого венца считает сервер (каталог docs/14, F).

Смысл набора: оператор снимает z/da/W — арифметика эвольвенты на сервере;
износ вершин б/у венца двигает выбор модуля вверх, а не в «ближайший»;
битые данные — честная ошибка строки, а не вердикт по мусору.
"""

import uuid

import pytest

from app.export import gear_checks_from_rows
from app.gears import GOST9563_MODULES, analyze_gear
from tests.conftest import OPERATOR, REVIEWER


class TestAnalyzeGear:
    def test_clean_spur_gear_recovers_module(self):
        # m=3, z=20 → da = m(z+2) = 66
        g = analyze_gear(z=20, da_mm=66.0)
        assert g.module_std == 3.0
        assert g.is_standard_module is True
        assert g.module_est == 3.0

    def test_worn_tips_still_land_on_larger_module(self):
        """Вершины б/у венца изношены — da занижен; выбор идёт вверх, как у
        сечений колец (Р-10 п.5.2), а не к «ближайшему» меньшему."""
        g = analyze_gear(z=20, da_mm=65.4)  # est 2.973 — между 2.5 и 3.0
        assert g.module_std == 3.0
        assert "больший модуль" in g.note

    def test_shift_from_base_tangent_length(self):
        """x≈0 для некорригированного венца: W замерен равным теоретическому."""
        import math

        m, z, n_w = 3.0, 20, 3
        alpha = math.radians(20.0)
        w0 = m * math.cos(alpha) * (math.pi * (n_w - 0.5) + z * (math.tan(alpha) - alpha))
        g = analyze_gear(z=z, da_mm=66.0, w_mm=round(w0, 3), n_w=n_w)
        assert g.x_shift == pytest.approx(0.0, abs=0.01)

    def test_positive_shift_detected(self):
        import math

        m, z, n_w, x = 3.0, 20, 3, 0.3
        alpha = math.radians(20.0)
        w0 = m * math.cos(alpha) * (math.pi * (n_w - 0.5) + z * (math.tan(alpha) - alpha))
        w = w0 + 2 * x * m * math.sin(alpha)
        g = analyze_gear(z=z, da_mm=66.0, w_mm=w, n_w=n_w)
        assert g.x_shift == pytest.approx(0.3, abs=0.01)

    def test_module_off_the_row_is_flagged_for_designer(self):
        g = analyze_gear(z=20, da_mm=61.0)  # est 2.77 → std 3.0, dev велик
        assert g.is_standard_module is False
        assert "у конструктора" in g.note

    def test_too_few_teeth_is_an_error(self):
        with pytest.raises(ValueError, match="меньше 5"):
            analyze_gear(z=3, da_mm=20.0)

    def test_module_row_matches_directory(self):
        """Затравка в коде и справочник рядов — одно и то же (Р-03 разд. 6)."""
        from app.directories import series_values

        assert tuple(series_values("gear_modules")) == GOST9563_MODULES


class TestGearChecksRows:
    def test_rows_become_checks(self):
        checks = gear_checks_from_rows([
            {"venets_no": 1, "z": 20, "da_mm": 66.0, "w_mm": None, "n_w": None}])
        assert checks[0]["module_std"] == 3.0
        assert checks[0]["venets_no"] == 1

    def test_broken_row_is_honest_error(self):
        checks = gear_checks_from_rows([{"venets_no": 1, "z": 3, "da_mm": 20.0}])
        assert "меньше 5" in checks[0]["error"]
        assert "module_std" not in checks[0]

    def test_gear_rows_reach_registry(self, client):
        def ev(seq, etype, payload):
            return {"client_event_id": str(uuid.uuid4()), "session_id": "s-gear-1",
                    "seq": seq, "type": etype,
                    "device_ts": "2026-07-29T05:00:00Z", "payload": payload}

        client.post("/v1/sync/events", json={"device_id": "d1", "events": [
            ev(1, "session.started", {"protocol": "pallet_general@7"}),
            ev(2, "step.completed", {"step_id": "f_geometry", "data": [
                {"venets_no": 1, "z": 20, "da_mm": 66.0}]}),
            ev(3, "session.completed", {}),
        ]}, headers=OPERATOR)
        row = client.get("/v1/export/sessions", headers=REVIEWER).json()["items"][0]
        assert row["gear_checks"][0]["module_std"] == 3.0
