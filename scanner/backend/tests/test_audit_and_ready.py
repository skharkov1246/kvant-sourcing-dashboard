"""Аудит решений (§07.4) и readiness-проба для деплоя.

Смысл набора: вердикт контролёра и судьба экспорта восстановимы — кто,
что, когда; /health/ready отвечает «можно подавать трафик», а не «процесс
запущен». Матрица memory/pg — через фикстур client.
"""

import uuid

import app.main as main_module
from app.export_worker import ExportWorker
from connectors.bitrix import BitrixUnavailable
from tests.conftest import OPERATOR, REVIEWER

TENANT = "t-internal"


def _event(session_id, seq, etype, payload):
    return {"client_event_id": str(uuid.uuid4()), "session_id": session_id,
            "seq": seq, "type": etype, "device_ts": "2026-08-02T20:00:00Z",
            "payload": payload}


def _accepted_task_session(client, session_id):
    r = client.post(
        "/v1/inbound/tasks",
        json={"external_system": "bitrix24", "external_ref": "7001",
              "protocol_code": "pallet_general", "subject": {}},
        headers={**OPERATOR, "Idempotency-Key": str(uuid.uuid4())},
    )
    task_id = r.json()["task_id"]
    client.post("/v1/sync/events", json={"device_id": "d1", "events": [
        _event(session_id, 1, "session.started",
               {"protocol": "pallet_general@7", "task_id": task_id}),
        _event(session_id, 2, "session.completed", {}),
    ]}, headers=OPERATOR)
    client.post(f"/v1/review/{session_id}/verdict", headers=REVIEWER,
                json={"verdict": "ACCEPTED"})


class FakeConnector:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)

    def push_result(self, row, field_map=None):
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome


class TestAuditTrail:
    def test_verdict_is_audited_with_actor(self, client):
        client.post("/v1/sync/events", json={"device_id": "d1", "events": [
            _event("s-au-1", 1, "session.completed", {}),
        ]}, headers=OPERATOR)
        client.post("/v1/review/s-au-1/verdict", headers=REVIEWER,
                    json={"verdict": "REJECTED", "comment": "фото с экрана"})
        entries = main_module.STORE.list_audit(TENANT)
        verdicts = [e for e in entries if e["action"] == "review.verdict"]
        assert len(verdicts) == 1
        entry = verdicts[0]
        assert entry["actor"] == "u-2"
        assert entry["target"] == "s-au-1"
        assert entry["details"] == {"verdict": "REJECTED", "comment": "фото с экрана"}

    def test_export_confirmation_is_audited(self, client):
        _accepted_task_session(client, "s-au-2")
        ExportWorker(main_module.STORE, FakeConnector()).run_once(TENANT)
        actions = [e["action"] for e in main_module.STORE.list_audit(TENANT)]
        assert "export.confirmed" in actions

    def test_export_dead_letter_is_audited_with_error(self, client):
        _accepted_task_session(client, "s-au-3")
        ExportWorker(main_module.STORE, FakeConnector(BitrixUnavailable("503")),
                     max_attempts=1).run_once(TENANT)
        entry = [e for e in main_module.STORE.list_audit(TENANT)
                 if e["action"] == "export.dead_letter"][0]
        assert entry["target"] == "s-au-3"
        assert "503" in entry["details"]["error"]

    def test_audit_accumulates_not_overwrites(self, client):
        for sid in ("s-au-4", "s-au-5"):
            client.post("/v1/sync/events", json={"device_id": "d1", "events": [
                _event(sid, 1, "session.completed", {}),
            ]}, headers=OPERATOR)
            client.post(f"/v1/review/{sid}/verdict", headers=REVIEWER,
                        json={"verdict": "ACCEPTED"})
        verdicts = [e for e in main_module.STORE.list_audit(TENANT)
                    if e["action"] == "review.verdict"]
        assert [e["target"] for e in verdicts] == ["s-au-4", "s-au-5"]


class TestReadiness:
    def test_ready_reports_all_checks_green(self, client):
        r = client.get("/health/ready")
        assert r.status_code == 200
        body = r.json()
        assert body["ready"] is True
        assert body["checks"]["directories"]["ok"] is True
        assert body["checks"]["directories"]["series_version"] >= 2
        assert body["checks"]["protocols"]["ok"] is True
        assert body["checks"]["store"]["ok"] is True

    def test_store_kind_is_honest(self, client):
        kind = client.get("/health/ready").json()["checks"]["store"]["kind"]
        assert kind in ("Store", "PgStore")
