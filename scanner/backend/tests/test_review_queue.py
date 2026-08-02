"""Очередь ручного ревью (приёмка → контролёр).

Смысл набора: MANUAL_REVIEW из приёмки не теряется, а превращается в
рабочую очередь контролёра; вердикт первого контролёра не перезаписывается
молча; REWORK-цикл переоткрывает запись, а не плодит дубли.

Матрица memory/pg — через общий фикстур client (conftest).
"""

import app.main as main_module
from tests.conftest import CUSTOMER, OPERATOR, REVIEWER

TENANT = "t-internal"


def _enqueue(session_id="s-review-1", reasons=("llm_flag: маркировка нечитаема",)):
    return main_module.STORE.enqueue_review(TENANT, session_id, list(reasons))


class TestQueue:
    def test_starts_empty(self, client):
        r = client.get("/v1/review/queue", headers=REVIEWER)
        assert r.status_code == 200 and r.json()["items"] == []

    def test_enqueued_session_is_visible(self, client):
        _enqueue()
        items = client.get("/v1/review/queue", headers=REVIEWER).json()["items"]
        assert [i["session_id"] for i in items] == ["s-review-1"]
        assert items[0]["reasons"] == ["llm_flag: маркировка нечитаема"]
        assert items[0]["verdict"] is None

    def test_repeat_enqueue_merges_reasons_without_duplicates(self, client):
        _enqueue(reasons=("причина-1",))
        _enqueue(reasons=("причина-1", "причина-2"))
        items = client.get("/v1/review/queue", headers=REVIEWER).json()["items"]
        assert len(items) == 1
        assert items[0]["reasons"] == ["причина-1", "причина-2"]

    def test_operator_cannot_see_queue(self, client):
        assert client.get("/v1/review/queue", headers=OPERATOR).status_code == 403

    def test_customer_cannot_see_queue(self, client):
        assert client.get("/v1/review/queue", headers=CUSTOMER).status_code == 403


class TestVerdict:
    def test_verdict_resolves_and_removes_from_queue(self, client):
        _enqueue()
        r = client.post("/v1/review/s-review-1/verdict", headers=REVIEWER,
                        json={"verdict": "ACCEPTED", "comment": "всё на месте"})
        assert r.status_code == 200
        entry = r.json()
        assert entry["verdict"] == "ACCEPTED"
        assert entry["verdict_by"] == "u-2"
        assert entry["resolved_at"] is not None
        assert client.get("/v1/review/queue", headers=REVIEWER).json()["items"] == []

    def test_second_verdict_conflicts_not_overwrites(self, client):
        _enqueue()
        client.post("/v1/review/s-review-1/verdict", headers=REVIEWER,
                    json={"verdict": "REJECTED"})
        r = client.post("/v1/review/s-review-1/verdict", headers=REVIEWER,
                        json={"verdict": "ACCEPTED"})
        assert r.status_code == 409
        assert main_module.STORE.get_review(TENANT, "s-review-1")["verdict"] == "REJECTED"

    def test_verdict_on_unknown_session_404(self, client):
        r = client.post("/v1/review/s-ghost/verdict", headers=REVIEWER,
                        json={"verdict": "ACCEPTED"})
        assert r.status_code == 404

    def test_operator_cannot_pass_verdict(self, client):
        _enqueue()
        r = client.post("/v1/review/s-review-1/verdict", headers=OPERATOR,
                        json={"verdict": "ACCEPTED"})
        assert r.status_code == 403

    def test_rework_cycle_reopens_entry(self, client):
        """REWORK → пересъёмка → повторная приёмка снова ставит сессию в
        очередь: запись переоткрывается с чистым вердиктом и свежими причинами."""
        _enqueue(reasons=("старая причина",))
        client.post("/v1/review/s-review-1/verdict", headers=REVIEWER,
                    json={"verdict": "REWORK", "comment": "переснять шильдик"})
        _enqueue(reasons=("после пересъёмки: сверить маркировку",))
        items = client.get("/v1/review/queue", headers=REVIEWER).json()["items"]
        assert len(items) == 1
        entry = items[0]
        assert entry["verdict"] is None and entry["resolved_at"] is None
        assert entry["reasons"] == ["после пересъёмки: сверить маркировку"]


class TestQueueSummary:
    def test_empty_queue_summary(self, client):
        s = client.get("/v1/review/queue/summary", headers=REVIEWER).json()
        assert s == {"open": 0, "by_reason": {}, "oldest_enqueued_at": None}

    def test_reasons_are_categorized_by_prefix(self, client):
        _enqueue("s-sum-1", reasons=("llm_unavailable: нет ключа",))
        _enqueue("s-sum-2", reasons=("llm_unavailable: нет ключа",
                                     "флаг: фото с экрана"))
        s = client.get("/v1/review/queue/summary", headers=REVIEWER).json()
        assert s["open"] == 2
        assert s["by_reason"] == {"llm_unavailable": 2, "флаг": 1}
        assert s["oldest_enqueued_at"] is not None

    def test_summary_requires_reviewer(self, client):
        assert client.get("/v1/review/queue/summary",
                          headers=OPERATOR).status_code == 403


class TestDashboardPage:
    def test_page_renders_queue_for_reviewer(self, client):
        _enqueue("s-dash-1", reasons=("llm_unavailable: нет ключа",))
        r = client.get("/review/dashboard", headers=REVIEWER)
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "s-dash-1" in r.text
        assert "llm_unavailable" in r.text

    def test_page_requires_reviewer_role(self, client):
        assert client.get("/review/dashboard", headers=OPERATOR).status_code == 403

    def test_reasons_are_html_escaped(self, client):
        _enqueue("s-dash-2", reasons=("флаг: <script>alert(1)</script>",))
        text = client.get("/review/dashboard", headers=REVIEWER).text
        assert "<script>alert(1)</script>" not in text
        assert "&lt;script&gt;" in text


class TestTenantIsolation:
    def test_queues_are_tenant_scoped(self, client):
        _enqueue()
        main_module.STORE.enqueue_review("t-acme", "s-acme-1", ["чужая причина"])
        internal = client.get("/v1/review/queue", headers=REVIEWER).json()["items"]
        assert [i["session_id"] for i in internal] == ["s-review-1"]
