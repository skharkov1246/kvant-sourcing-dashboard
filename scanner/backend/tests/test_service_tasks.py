"""Служебные исходы kv_no_id → задачи сервера (Р-11 п.3, Р-13 п.5.1).

Смысл набора: «БРЕНД НЕ ОПРЕДЕЛЁН» и «УСТАНОВКА НЕ ОПРЕДЕЛЕНА» — исход
работы у детали, по которому сервер САМ создаёт задачу с дедлайном в
рабочих днях; повтор батча задачу не дублирует; браковка задач не плодит.

Матрица memory/pg — через фикстур client.
"""

import datetime as dt
import uuid

import app.main as main_module
from app.buffer import WorkCalendar
from tests.conftest import OPERATOR

TENANT = "t-internal"


def _event(session_id, seq, etype, payload):
    return {"client_event_id": str(uuid.uuid4()), "session_id": session_id,
            "seq": seq, "type": etype, "device_ts": "2026-07-29T07:00:00Z",
            "payload": payload}


def _no_id_session(client, session_id, outcome, basis="клейм нет нигде"):
    events = [
        _event(session_id, 1, "session.started", {"protocol": "pallet_general@7"}),
        _event(session_id, 2, "step.completed",
               {"step_id": "n_outcome", "data": {"outcome": outcome, "basis": basis}}),
        _event(session_id, 3, "session.completed", {}),
    ]
    return client.post("/v1/sync/events",
                       json={"device_id": "d1", "events": events}, headers=OPERATOR)


def _tasks(client, **params):
    return client.get("/v1/tasks", headers=OPERATOR).json()["items"]


class TestServiceTaskSpawning:
    def test_brand_unknown_spawns_10_business_day_task(self, client):
        _no_id_session(client, "s-svc-1", "БРЕНД НЕ ОПРЕДЕЛЁН")
        tasks = [t for t in _tasks(client) if t["external_system"] == "kvant-scan"]
        assert len(tasks) == 1
        task = tasks[0]
        assert task["subject"]["outcome"] == "БРЕНД НЕ ОПРЕДЕЛЁН"
        assert task["subject"]["source_session"] == "s-svc-1"
        assert task["assignee_pool"] == "brand_owners"
        expected = WorkCalendar().add_business_days(dt.date.today(), 10)
        # PG отдаёт timestamptz, память — дату: сверяем календарный день.
        assert task["due_at"][:10] == expected.isoformat()

    def test_site_unknown_gets_15_business_days(self, client):
        _no_id_session(client, "s-svc-2", "УСТАНОВКА НЕ ОПРЕДЕЛЕНА")
        task = [t for t in _tasks(client) if t["external_system"] == "kvant-scan"][0]
        expected = WorkCalendar().add_business_days(dt.date.today(), 15)
        assert task["due_at"][:10] == expected.isoformat()
        assert task["assignee_pool"] == "site_managers"

    def test_repeat_batch_does_not_duplicate_task(self, client):
        events = [
            _event("s-svc-3", 1, "session.started", {"protocol": "pallet_general@7"}),
            _event("s-svc-3", 2, "step.completed",
                   {"step_id": "n_outcome", "data": {"outcome": "БРЕНД НЕ ОПРЕДЕЛЁН"}}),
            _event("s-svc-3", 3, "session.completed", {}),
        ]
        client.post("/v1/sync/events", json={"device_id": "d1", "events": events},
                    headers=OPERATOR)
        client.post("/v1/sync/events", json={"device_id": "d1", "events": events},
                    headers=OPERATOR)
        tasks = [t for t in _tasks(client) if t["external_system"] == "kvant-scan"]
        assert len(tasks) == 1

    def test_reject_outcome_spawns_no_task(self, client):
        """«Забраковано» — запись реестра, а не задача: дальше работать не над чем."""
        _no_id_session(client, "s-svc-4", "Забраковано", basis="не деталь")
        assert [t for t in _tasks(client) if t["external_system"] == "kvant-scan"] == []

    def test_ordinary_session_spawns_no_task(self, client):
        client.post("/v1/sync/events", json={"device_id": "d1", "events": [
            _event("s-svc-5", 1, "session.started", {"protocol": "pallet_general@7"}),
            _event("s-svc-5", 2, "step.completed", {"step_id": "overview"}),
            _event("s-svc-5", 3, "session.completed", {}),
        ]}, headers=OPERATOR)
        assert [t for t in _tasks(client) if t["external_system"] == "kvant-scan"] == []


class TestWorkCalendarDeadlines:
    def test_friday_plus_one_is_monday(self):
        cal = WorkCalendar()
        friday = dt.date(2026, 7, 31)
        assert cal.add_business_days(friday, 1) == dt.date(2026, 8, 3)

    def test_holiday_is_skipped(self):
        cal = WorkCalendar(holidays={dt.date(2026, 8, 3)})
        friday = dt.date(2026, 7, 31)
        assert cal.add_business_days(friday, 1) == dt.date(2026, 8, 4)

    def test_working_saturday_counts(self):
        cal = WorkCalendar(extra_working={dt.date(2026, 8, 1)})
        friday = dt.date(2026, 7, 31)
        assert cal.add_business_days(friday, 1) == dt.date(2026, 8, 1)
