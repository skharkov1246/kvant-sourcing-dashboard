"""Очередь экспорта и воркер (I-8): результат не теряется между нами и CRM.

Смысл набора: EXPORTED задание становится только после подтверждения
внешней системы; отказ портала — backoff, исчерпание — dead letter, и в
обоих случаях задание остаётся ACCEPTED. Матрица memory/pg — фикстур client.
"""

import datetime as dt
import uuid

import app.main as main_module
from app.export_worker import ExportWorker
from connectors.bitrix import BitrixError, BitrixUnavailable
from tests.conftest import OPERATOR, REVIEWER

TENANT = "t-internal"


def _event(session_id, seq, etype, payload):
    return {
        "client_event_id": str(uuid.uuid4()),
        "session_id": session_id,
        "seq": seq,
        "type": etype,
        "device_ts": "2026-07-29T01:00:00Z",
        "payload": payload,
    }


def _accepted_session(client, session_id, with_task=True):
    task_id = None
    if with_task:
        r = client.post(
            "/v1/inbound/tasks",
            json={"external_system": "bitrix24", "external_ref": "4242",
                  "protocol_code": "pallet_general", "subject": {"kind": "pallet"}},
            headers={**OPERATOR, "Idempotency-Key": str(uuid.uuid4())},
        )
        task_id = r.json()["task_id"]
    started = {"protocol": "pallet_general@7"}
    if task_id:
        started["task_id"] = task_id
    client.post("/v1/sync/events", json={"device_id": "d1", "events": [
        _event(session_id, 1, "session.started", started),
        _event(session_id, 2, "step.completed", {"step_id": "overview", "quality_score": 0.9}),
        _event(session_id, 3, "session.completed", {}),
    ]}, headers=OPERATOR)
    client.post(f"/v1/review/{session_id}/verdict", headers=REVIEWER,
                json={"verdict": "ACCEPTED"})
    return task_id


class FakeConnector:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.pushed = []

    def push_result(self, row, field_map=None):
        self.pushed.append(row)
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome


def _future(minutes=10):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes)).isoformat()


class TestEnqueueOnVerdict:
    def test_accepted_task_session_is_enqueued(self, client):
        _accepted_session(client, "s-eq-1")
        entry = main_module.STORE.get_export(TENANT, "s-eq-1")
        assert entry is not None and entry["state"] == "PENDING"

    def test_adhoc_session_without_external_system_is_not_enqueued(self, client):
        _accepted_session(client, "s-eq-2", with_task=False)
        assert main_module.STORE.get_export(TENANT, "s-eq-2") is None

    def test_rework_verdict_does_not_enqueue(self, client):
        task_id = None
        r = client.post(
            "/v1/inbound/tasks",
            json={"external_system": "bitrix24", "external_ref": "4243",
                  "protocol_code": "pallet_general", "subject": {}},
            headers={**OPERATOR, "Idempotency-Key": str(uuid.uuid4())},
        )
        task_id = r.json()["task_id"]
        client.post("/v1/sync/events", json={"device_id": "d1", "events": [
            _event("s-eq-3", 1, "session.started",
                   {"protocol": "pallet_general@7", "task_id": task_id}),
            _event("s-eq-3", 2, "session.completed", {}),
        ]}, headers=OPERATOR)
        client.post("/v1/review/s-eq-3/verdict", headers=REVIEWER,
                    json={"verdict": "REWORK"})
        assert main_module.STORE.get_export(TENANT, "s-eq-3") is None


class TestWorker:
    def test_confirmation_exports_task(self, client):
        task_id = _accepted_session(client, "s-wk-1")
        connector = FakeConnector()
        stats = ExportWorker(main_module.STORE, connector).run_once(TENANT)
        assert stats == {"confirmed": 1, "deferred": 0, "dead_lettered": 0}
        assert connector.pushed[0]["external_ref"] == "4242"
        assert main_module.STORE.get_export(TENANT, "s-wk-1")["state"] == "CONFIRMED"
        task = client.get(f"/v1/tasks/{task_id}", headers=OPERATOR).json()
        assert task["state"] == "EXPORTED"

    def test_flaky_portal_defers_with_backoff_task_stays_accepted(self, client):
        task_id = _accepted_session(client, "s-wk-2")
        worker = ExportWorker(main_module.STORE, FakeConnector(BitrixUnavailable("503")))
        stats = worker.run_once(TENANT)
        assert stats["deferred"] == 1
        entry = main_module.STORE.get_export(TENANT, "s-wk-2")
        assert entry["state"] == "PENDING" and entry["attempts"] == 1
        assert entry["last_error"].startswith("503")
        task = client.get(f"/v1/tasks/{task_id}", headers=OPERATOR).json()
        assert task["state"] == "ACCEPTED"  # I-8

    def test_deferred_entry_is_not_due_until_backoff_passes(self, client):
        _accepted_session(client, "s-wk-3")
        worker = ExportWorker(main_module.STORE, FakeConnector(BitrixUnavailable("503")))
        worker.run_once(TENANT)
        # Сразу после отказа запись не созрела; после окна backoff — снова в работе.
        assert worker.run_once(TENANT) == {"confirmed": 0, "deferred": 0, "dead_lettered": 0}
        assert ExportWorker(main_module.STORE, FakeConnector()).run_once(
            TENANT, now=_future())["confirmed"] == 1

    def test_exhausted_attempts_dead_letter_task_stays_accepted(self, client):
        task_id = _accepted_session(client, "s-wk-4")
        worker = ExportWorker(main_module.STORE,
                              FakeConnector(BitrixUnavailable("503")), max_attempts=1)
        stats = worker.run_once(TENANT)
        assert stats["dead_lettered"] == 1
        assert main_module.STORE.get_export(TENANT, "s-wk-4")["state"] == "DEAD_LETTER"
        task = client.get(f"/v1/tasks/{task_id}", headers=OPERATOR).json()
        assert task["state"] == "ACCEPTED"  # I-8: результат не потерян

    def test_fatal_error_dead_letters_without_retry(self, client):
        _accepted_session(client, "s-wk-5")
        worker = ExportWorker(main_module.STORE, FakeConnector(BitrixError("нет прав")))
        assert worker.run_once(TENANT)["dead_lettered"] == 1

    def test_confirmed_entry_is_not_redelivered(self, client):
        _accepted_session(client, "s-wk-6")
        connector = FakeConnector()
        worker = ExportWorker(main_module.STORE, connector)
        worker.run_once(TENANT)
        worker.run_once(TENANT)
        assert len(connector.pushed) == 1
