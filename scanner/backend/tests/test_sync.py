"""Проверки синхронизации: идемпотентность, частичный отказ батча, докачка медиа."""

import hashlib
import uuid

from tests.conftest import CUSTOMER, OPERATOR


def event(session_id, seq, etype="step.completed", payload=None, event_id=None):
    return {
        "client_event_id": event_id or str(uuid.uuid4()),
        "session_id": session_id,
        "seq": seq,
        "type": etype,
        "device_ts": "2026-07-28T09:00:00Z",
        "payload": payload or {"step_id": f"s{seq}"},
    }


class TestEventIdempotency:
    def test_repeat_delivery_is_duplicate_not_error(self, client):
        e = event("sess-1", 1)
        first = client.post("/v1/sync/events", json={"device_id": "d1", "events": [e]}, headers=OPERATOR)
        second = client.post("/v1/sync/events", json={"device_id": "d1", "events": [e]}, headers=OPERATOR)
        assert first.json()["results"][0]["status"] == "accepted"
        # Повтор после потери ответа — успех, клиент спокойно чистит outbox.
        assert second.json()["results"][0]["status"] == "duplicate"

    def test_same_id_different_payload_is_rejected(self, client):
        eid = str(uuid.uuid4())
        a = event("sess-1", 1, payload={"step_id": "overview"}, event_id=eid)
        b = event("sess-1", 1, payload={"step_id": "label"}, event_id=eid)
        client.post("/v1/sync/events", json={"device_id": "d1", "events": [a]}, headers=OPERATOR)
        result = client.post("/v1/sync/events", json={"device_id": "d1", "events": [b]}, headers=OPERATOR)
        row = result.json()["results"][0]
        assert row["status"] == "rejected"
        assert row["error"]["code"] == "conflicting_duplicate"
        assert row["error"]["retryable"] is False

    def test_batch_reports_per_event_and_does_not_fail_wholesale(self, client):
        eid = str(uuid.uuid4())
        client.post(
            "/v1/sync/events",
            json={"device_id": "d1", "events": [event("s", 1, payload={"step_id": "a"}, event_id=eid)]},
            headers=OPERATOR,
        )
        batch = [
            event("s", 2),
            event("s", 1, payload={"step_id": "b"}, event_id=eid),  # конфликт
            event("s", 3),
        ]
        resp = client.post("/v1/sync/events", json={"device_id": "d1", "events": batch}, headers=OPERATOR)
        assert resp.status_code == 200  # транспорт отработал, семантика в теле
        statuses = [r["status"] for r in resp.json()["results"]]
        assert statuses == ["accepted", "rejected", "accepted"]


class TestProjection:
    def test_out_of_order_delivery_folds_correctly(self, client):
        events = [
            event("s2", 3, "session.completed", {}),
            event("s2", 1, "session.started", {"protocol": "pallet_general@7"}),
            event("s2", 2, "step.completed", {"step_id": "overview", "quality_score": 0.9}),
        ]
        client.post("/v1/sync/events", json={"device_id": "d1", "events": events}, headers=OPERATOR)
        state = client.get("/v1/sessions/s2/state", headers=OPERATOR).json()
        assert state["state"] == "COMPLETED_LOCAL"
        assert state["last_seq"] == 3
        assert state["steps"]["overview"] == "COMPLETED"

    def test_partial_prefix_is_valid_state(self, client):
        """Телефон сел на середине — сервер видит валидный префикс, а не мусор."""
        events = [
            event("s3", 1, "session.started", {"protocol": "pallet_general@7"}),
            event("s3", 2, "step.completed", {"step_id": "overview"}),
        ]
        client.post("/v1/sync/events", json={"device_id": "d1", "events": events}, headers=OPERATOR)
        state = client.get("/v1/sessions/s3/state", headers=OPERATOR).json()
        assert state["state"] == "ACTIVE"
        assert state["complete"] is False

    def test_session_log_supports_handoff(self, client):
        events = [event("s4", i, "step.completed", {"step_id": f"s{i}"}) for i in range(1, 4)]
        client.post("/v1/sync/events", json={"device_id": "d1", "events": events}, headers=OPERATOR)
        tail = client.get("/v1/sync/sessions/s4/log?since_seq=1", headers=OPERATOR).json()
        assert [e["seq"] for e in tail["events"]] == [2, 3]


class TestTenantIsolation:
    def test_other_tenant_cannot_see_session(self, client):
        client.post(
            "/v1/sync/events",
            json={"device_id": "d1", "events": [event("secret", 1)]},
            headers=OPERATOR,
        )
        assert client.get("/v1/sessions/secret/state", headers=CUSTOMER).status_code == 404

    def test_unauthenticated_is_rejected(self, client):
        assert client.get("/v1/tasks").status_code == 401


class TestAssetUpload:
    def test_chunked_upload_and_checksum(self, client):
        payload = b"x" * 3000
        digest = hashlib.sha256(payload).hexdigest()
        init = client.post(
            "/v1/assets",
            json={
                "session_id": "s5", "step_id": "overview", "kind": "photo",
                "mime": "image/jpeg", "bytes": len(payload), "sha256": digest,
            },
            headers=OPERATOR,
        ).json()
        assert init["deduplicated"] is False
        client.put(f"/v1/assets/{init['asset_id']}/chunks/0", content=payload, headers=OPERATOR)
        done = client.post(f"/v1/assets/{init['asset_id']}/complete", headers=OPERATOR).json()
        assert done["status"] == "verified"

    def test_missing_chunk_is_reported_for_resume(self, client):
        payload = b"y" * (1 << 21)  # два чанка по 1 МиБ
        digest = hashlib.sha256(payload).hexdigest()
        init = client.post(
            "/v1/assets",
            json={
                "session_id": "s6", "step_id": "overview", "kind": "photo",
                "mime": "image/jpeg", "bytes": len(payload), "sha256": digest,
            },
            headers=OPERATOR,
        ).json()
        client.put(f"/v1/assets/{init['asset_id']}/chunks/0", content=payload[: 1 << 20], headers=OPERATOR)
        done = client.post(f"/v1/assets/{init['asset_id']}/complete", headers=OPERATOR).json()
        assert done["status"] == "missing_chunks" and done["missing_chunks"] == [1]

    def test_checksum_mismatch_resets_upload(self, client):
        real = b"z" * 500
        init = client.post(
            "/v1/assets",
            json={
                "session_id": "s7", "step_id": "overview", "kind": "photo",
                "mime": "image/jpeg", "bytes": len(real),
                "sha256": hashlib.sha256(b"other").hexdigest(),
            },
            headers=OPERATOR,
        ).json()
        client.put(f"/v1/assets/{init['asset_id']}/chunks/0", content=real, headers=OPERATOR)
        done = client.post(f"/v1/assets/{init['asset_id']}/complete", headers=OPERATOR).json()
        assert done["status"] == "checksum_mismatch"
        # Инвариант I-1: устройство не удалит локальный файл, пока нет verified.
        status = client.get(f"/v1/assets/{init['asset_id']}/status", headers=OPERATOR).json()
        assert status["upload_state"] == "pending" and status["received_chunks"] == []

    def test_identical_asset_is_deduplicated(self, client):
        payload = b"same"
        digest = hashlib.sha256(payload).hexdigest()
        body = {
            "session_id": "s8", "step_id": "overview", "kind": "photo",
            "mime": "image/jpeg", "bytes": len(payload), "sha256": digest,
        }
        first = client.post("/v1/assets", json=body, headers=OPERATOR).json()
        second = client.post("/v1/assets", json=body, headers=OPERATOR).json()
        assert second["deduplicated"] is True
        assert second["asset_id"] == first["asset_id"]
