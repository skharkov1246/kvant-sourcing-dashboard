"""End-to-end поток приёмки: события устройства → приёмка → очередь контролёра.

Смысл набора: завершённая сессия не повисает в воздухе ни при каком исходе.
Без ANTHROPIC_API_KEY транспорт честно недоступен (NullTransport), поэтому
всё, что не отклонено правилами, оказывается у контролёра с внятной причиной.

Матрица memory/pg — через общий фикстур client (conftest).
"""

import uuid

from tests.conftest import OPERATOR, REVIEWER


def _event(session_id, seq, etype, payload):
    return {
        "client_event_id": str(uuid.uuid4()),
        "session_id": session_id,
        "seq": seq,
        "type": etype,
        "device_ts": "2026-07-28T22:00:00Z",
        "payload": payload,
    }


def _post(client, events):
    return client.post("/v1/sync/events", json={"device_id": "d1", "events": events},
                       headers=OPERATOR)


def _queue(client):
    return client.get("/v1/review/queue", headers=REVIEWER).json()["items"]


class TestCompletedSessionReachesReviewer:
    def test_completed_session_lands_in_queue_with_reason(self, client):
        _post(client, [
            _event("s-flow-1", 1, "session.started", {"protocol": "pallet_general@7"}),
            _event("s-flow-1", 2, "step.completed", {"step_id": "overview", "quality_score": 0.9}),
            _event("s-flow-1", 3, "session.completed", {}),
        ])
        items = _queue(client)
        assert [i["session_id"] for i in items] == ["s-flow-1"]
        assert any("llm_unavailable" in r for r in items[0]["reasons"])

    def test_completion_in_same_batch_out_of_order_still_processed(self, client):
        """Завершение может прийти в одном батче с шагами и даже раньше их —
        приёмка стартует после записи всего батча, по полной проекции."""
        _post(client, [
            _event("s-flow-2", 3, "session.completed", {}),
            _event("s-flow-2", 1, "session.started", {"protocol": "pallet_general@7"}),
            _event("s-flow-2", 2, "step.completed", {"step_id": "overview"}),
        ])
        assert [i["session_id"] for i in _queue(client)] == ["s-flow-2"]

    def test_session_without_protocol_goes_to_reviewer_not_nowhere(self, client):
        _post(client, [
            _event("s-flow-3", 1, "session.completed", {}),
        ])
        items = _queue(client)
        assert [i["session_id"] for i in items] == ["s-flow-3"]
        assert items[0]["reasons"] == ["протокол сессии не определён"]

    def test_duplicate_completion_delivery_does_not_duplicate_queue_entry(self, client):
        """Повтор батча после потери ответа — duplicate, приёмка не перезапускается."""
        events = [
            _event("s-flow-4", 1, "session.started", {"protocol": "pallet_general@7"}),
            _event("s-flow-4", 2, "session.completed", {}),
        ]
        _post(client, events)
        second = _post(client, events).json()["results"]
        assert all(r["status"] == "duplicate" for r in second)
        assert len(_queue(client)) == 1


class TestRulesStillTerminal:
    def test_rejected_by_rules_never_reaches_queue(self, client):
        """reject_if протокола (ручной ввод кода этикетки) терминален:
        контролёру решать нечего, LLM не спрашивается."""
        _post(client, [
            _event("s-flow-5", 1, "session.started", {"protocol": "pallet_general@7"}),
            _event("s-flow-5", 2, "step.completed",
                   {"step_id": "label", "data": {"source": "manual"}}),
            _event("s-flow-5", 3, "session.completed", {}),
        ])
        assert _queue(client) == []
