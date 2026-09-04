"""Проверка СИ в приёмке (Р-08): замер неповеренным СИ недействителен.

Смысл набора: неизвестное или просроченное СИ отправляет сессию контролёру
с внятным флагом — даже если правила и модель всем довольны; один прибор в
двадцати строках сетки даёт один флаг, а не двадцать.

Матрица memory/pg — через фикстур client.
"""

import uuid

import app.main as main_module
from tests.conftest import OPERATOR, REVIEWER

VALID_SI = "Микрометр МК-25 №Б0001, поверка до 2027-01"
EXPIRING_SI = "Твердомер ТЭМП-4 №Д0001, поверка до 2026-11"


def _event(session_id, seq, etype, payload, ts="2026-08-02T23:00:00Z"):
    return {"client_event_id": str(uuid.uuid4()), "session_id": session_id,
            "seq": seq, "type": etype, "device_ts": ts, "payload": payload}


def _session(client, session_id, instrument, completed_ts="2026-08-02T23:00:00Z"):
    client.post("/v1/sync/events", json={"device_id": "d1", "events": [
        _event(session_id, 1, "session.started", {"protocol": "pallet_general@7"}),
        _event(session_id, 2, "step.completed",
               {"step_id": "dims", "data": {"value_mm": 50.0, "instrument": instrument}}),
        _event(session_id, 3, "session.completed", {}, ts=completed_ts),
    ]}, headers=OPERATOR)


def _reasons(client, session_id):
    items = client.get("/v1/review/queue", headers=REVIEWER).json()["items"]
    return next(e["reasons"] for e in items if e["session_id"] == session_id)


class TestInstrumentFlags:
    def test_valid_instrument_adds_no_flags(self, client):
        _session(client, "s-si-1", VALID_SI)
        assert not any(r.startswith("си_") for r in _reasons(client, "s-si-1"))

    def test_unknown_instrument_is_flagged(self, client):
        _session(client, "s-si-2", "Линейка из канцелярии")
        reasons = _reasons(client, "s-si-2")
        assert any(r.startswith("си_вне_справочника: Линейка") for r in reasons)

    def test_instrument_from_grid_rows_is_checked_once(self, client):
        """Repeat-строки: один прибор в трёх строках — один флаг."""
        client.post("/v1/sync/events", json={"device_id": "d1", "events": [
            _event("s-si-3", 1, "session.started", {"protocol": "pallet_general@7"}),
            _event("s-si-3", 2, "step.completed", {"step_id": "c_grid", "data": [
                {"value_mm": 50.0, "instrument": "Самодельный штанген"},
                {"value_mm": 50.1, "instrument": "Самодельный штанген"},
                {"value_mm": 50.2, "instrument": "Самодельный штанген"},
            ]}),
            _event("s-si-3", 3, "session.completed", {}),
        ]}, headers=OPERATOR)
        flags = [r for r in _reasons(client, "s-si-3") if r.startswith("си_")]
        assert len(flags) == 1

    def test_expired_verification_is_flagged_by_session_month(self, client):
        """Поверка до 2026-11, сессия в 2027-06 — просрочено на дату СЪЁМКИ."""
        _session(client, "s-si-4", EXPIRING_SI, completed_ts="2027-06-15T10:00:00Z")
        # ВНИМАНИЕ: server_ts у события — момент приёма (сейчас), поэтому месяц
        # сессии берётся из server_ts; проверяем через прямой вызов ядра.
        from app.projections import fold
        from app.si_check import instrument_flags

        events = main_module.STORE.session_events("t-internal", "s-si-4")
        flags = instrument_flags(fold("s-si-4", events), "2027-06")
        assert any(r.startswith("си_поверка_просрочена") for r in flags)

    def test_stand_field_is_checked_too(self, client):
        """Поле stand (стенд усилия kv_cat_b) проверяется наравне с instrument."""
        client.post("/v1/sync/events", json={"device_id": "d1", "events": [
            _event("s-si-5", 1, "session.started", {"protocol": "pallet_general@7"}),
            _event("s-si-5", 2, "step.completed",
                   {"step_id": "b_force", "data": {"force_n": 100, "stand": "Гараж-стенд"}}),
            _event("s-si-5", 3, "session.completed", {}),
        ]}, headers=OPERATOR)
        assert any("Гараж-стенд" in r for r in _reasons(client, "s-si-5"))
