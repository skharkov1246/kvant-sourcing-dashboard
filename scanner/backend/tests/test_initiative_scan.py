"""Инициативный скан — сессия без задания.

База наполняется не только заданиями сверху: кладовщик у стеллажа может
отснять деталь добровольно. Такая сессия несёт origin=initiative и НЕ несёт
task_id — и обязана пройти весь путь как обычная: приёмка → контролёр →
вердикт → карточка товара. И ничего не должна сломать в жизненном цикле
заданий, которых у неё нет.

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
        "device_ts": "2026-08-06T10:00:00Z",
        "payload": payload,
    }


def _post(client, events):
    return client.post("/v1/sync/events", json={"device_id": "d1", "events": events},
                       headers=OPERATOR)


def _run_initiative(client, session_id):
    _post(client, [
        _event(session_id, 1, "session.started",
               {"protocol": "pallet_general@7", "origin": "initiative"}),
        _event(session_id, 2, "step.completed",
               {"step_id": "overview", "quality_score": 0.9}),
        _event(session_id, 3, "code.read",
               {"code_type": "gtin", "raw_value": "4601234567890"}),
        _event(session_id, 4, "session.completed", {}),
    ])


class TestInitiativeScan:
    def test_reaches_reviewer_without_task(self, client):
        _run_initiative(client, "s-init-1")
        items = client.get("/v1/review/queue", headers=REVIEWER).json()["items"]
        assert [i["session_id"] for i in items] == ["s-init-1"]

    def test_accept_materializes_item_and_moves_no_task(self, client):
        _run_initiative(client, "s-init-2")
        r = client.post("/v1/review/s-init-2/verdict",
                        json={"verdict": "ACCEPTED"}, headers=REVIEWER)
        assert r.status_code == 200

        card = client.get("/v1/items/itm-s-init-2", headers=OPERATOR)
        assert card.status_code == 200
        identity = card.json()["identity"]
        assert identity.get("gtin") == "4601234567890"

    def test_rework_verdict_does_not_crash_without_task(self, client):
        """REWORK двигает задание — а у инициативной сессии задания нет.
        Это не может быть ошибкой сервера."""
        _run_initiative(client, "s-init-3")
        r = client.post("/v1/review/s-init-3/verdict",
                        json={"verdict": "REWORK", "comment": "переснять шильдик"},
                        headers=REVIEWER)
        assert r.status_code == 200

    def test_initiative_session_visible_in_export(self, client):
        _run_initiative(client, "s-init-4")
        client.post("/v1/review/s-init-4/verdict",
                    json={"verdict": "ACCEPTED"}, headers=REVIEWER)
        csv_text = client.get("/v1/export/sessions.csv", headers=REVIEWER).text
        assert "s-init-4" in csv_text
