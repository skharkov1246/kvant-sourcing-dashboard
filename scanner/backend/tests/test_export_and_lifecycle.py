"""Экспорт реестра (Р-13) и жизненный цикл задания после вердикта.

Смысл набора: результат съёмки доходит до реестра (и дальше — в
Bitrix/Google Sheets), а вердикт контролёра не остаётся записью в очереди,
а двигает задание: REWORK возвращает его на устройство, ACCEPTED/REJECTED
закрывают. Матрица memory/pg — через фикстур client.
"""

import uuid

from tests.conftest import OPERATOR, REVIEWER


def _event(session_id, seq, etype, payload):
    return {
        "client_event_id": str(uuid.uuid4()),
        "session_id": session_id,
        "seq": seq,
        "type": etype,
        "device_ts": "2026-07-28T23:00:00Z",
        "payload": payload,
    }


def _complete_session(client, session_id, task_id=None, extra_events=()):
    started = {"protocol": "pallet_general@7"}
    if task_id:
        started["task_id"] = task_id
    events = [
        _event(session_id, 1, "session.started", started),
        _event(session_id, 2, "step.completed", {"step_id": "overview", "quality_score": 0.9}),
        *extra_events,
        _event(session_id, 9, "session.completed", {}),
    ]
    r = client.post("/v1/sync/events", json={"device_id": "d1", "events": events},
                    headers=OPERATOR)
    assert r.status_code == 200


def _make_task(client):
    r = client.post(
        "/v1/inbound/tasks",
        json={"external_system": "bitrix", "external_ref": f"deal-{uuid.uuid4()}",
              "protocol_code": "pallet_general", "subject": {"kind": "pallet"}},
        headers={**OPERATOR, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 202
    return r.json()["task_id"]


class TestExport:
    def test_completed_session_becomes_registry_row(self, client):
        _complete_session(client, "s-exp-1", extra_events=[
            _event("s-exp-1", 3, "measurement.recorded",
                   {"step_id": "dims", "accuracy_class": "B", "length_mm": 1200}),
            _event("s-exp-1", 4, "code.read",
                   {"step_id": "code", "code_type": "gtin", "raw_value": "4601234567890"}),
        ])
        data = client.get("/v1/export/sessions", headers=REVIEWER).json()
        assert data["count"] == 1
        row = data["items"][0]
        assert row["session_id"] == "s-exp-1"
        assert row["protocol"] == "pallet_general@7"
        assert row["steps"]["overview"] == "COMPLETED"
        assert row["measurements"][0]["length_mm"] == 1200
        assert row["codes"][0]["raw_value"] == "4601234567890"
        # Приёмка без ключа отправила сессию в ревью — причина видна в реестре.
        assert any("llm_unavailable" in r for r in row["review"]["reasons"])

    def test_incomplete_session_is_not_a_result(self, client):
        client.post("/v1/sync/events", json={"device_id": "d1", "events": [
            _event("s-exp-2", 1, "session.started", {"protocol": "pallet_general@7"}),
        ]}, headers=OPERATOR)
        assert client.get("/v1/export/sessions", headers=REVIEWER).json()["count"] == 0

    def test_verdict_lands_in_registry_row(self, client):
        _complete_session(client, "s-exp-3")
        client.post("/v1/review/s-exp-3/verdict", headers=REVIEWER,
                    json={"verdict": "ACCEPTED", "comment": "ок"})
        row = client.get("/v1/export/sessions", headers=REVIEWER).json()["items"][0]
        assert row["review"]["verdict"] == "ACCEPTED"
        assert row["review"]["verdict_by"] == "u-2"

    def test_date_filter_bounds_export(self, client):
        _complete_session(client, "s-exp-4")
        assert client.get("/v1/export/sessions?date_from=2000-01-01T00:00:00",
                          headers=REVIEWER).json()["count"] == 1
        assert client.get("/v1/export/sessions?date_to=2000-01-01T00:00:00",
                          headers=REVIEWER).json()["count"] == 0

    def test_export_requires_reviewer_role(self, client):
        assert client.get("/v1/export/sessions", headers=OPERATOR).status_code == 403


class TestVerdictMovesTask:
    def test_rework_returns_task_to_device(self, client):
        task_id = _make_task(client)
        _complete_session(client, "s-lc-1", task_id=task_id)
        client.post("/v1/review/s-lc-1/verdict", headers=REVIEWER,
                    json={"verdict": "REWORK", "comment": "переснять этикетку"})
        task = client.get(f"/v1/tasks/{task_id}", headers=OPERATOR).json()
        assert task["state"] == "REWORK"

    def test_accept_closes_task(self, client):
        task_id = _make_task(client)
        _complete_session(client, "s-lc-2", task_id=task_id)
        client.post("/v1/review/s-lc-2/verdict", headers=REVIEWER,
                    json={"verdict": "ACCEPTED"})
        task = client.get(f"/v1/tasks/{task_id}", headers=OPERATOR).json()
        assert task["state"] == "ACCEPTED"

    def test_reject_also_closes_task(self, client):
        """Брак — тоже результат: фиксируется в реестре, задание не висит."""
        task_id = _make_task(client)
        _complete_session(client, "s-lc-3", task_id=task_id)
        client.post("/v1/review/s-lc-3/verdict", headers=REVIEWER,
                    json={"verdict": "REJECTED", "comment": "фото с экрана"})
        task = client.get(f"/v1/tasks/{task_id}", headers=OPERATOR).json()
        assert task["state"] == "REJECTED"

    def test_adhoc_session_without_task_is_fine(self, client):
        _complete_session(client, "s-lc-4")
        r = client.post("/v1/review/s-lc-4/verdict", headers=REVIEWER,
                        json={"verdict": "ACCEPTED"})
        assert r.status_code == 200
