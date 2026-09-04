"""Коннектор Bitrix24: опрос с курсором, обратная запись, retry (docs/09).

Смысл набора: перекрытие окна курсора не теряет правки на границе; отказ
портала не превращается в «в приложении принято, в CRM пусто» (I-8); лишние
поля в CRM не пишутся.
"""

import pytest

from connectors.bitrix import (
    BitrixConnector,
    BitrixError,
    BitrixUnavailable,
    RetryPolicy,
)


class FakeBitrix:
    """Скриптованный транспорт: очередь исходов + журнал вызовов."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def call(self, method, params):
        self.calls.append({"method": method, "params": params})
        outcome = self.outcomes.pop(0) if self.outcomes else {"result": {}}
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _connector(*outcomes, **kw):
    transport = FakeBitrix(*outcomes)
    delays = []
    kw.setdefault("retry", RetryPolicy(max_attempts=3, base_delay_s=1.0))
    connector = BitrixConnector(transport=transport, sleep=delays.append, **kw)
    return connector, transport, delays


def _items(*rows):
    return {"result": {"items": list(rows)}}


ROW = {
    "session_id": "s-1",
    "task_id": "t-1",
    "external_system": "bitrix24",
    "external_ref": "4242",
    "protocol": "kv_router@1",
    "quality_score": 0.91,
    "measurements": [{"step_id": "dims", "length_mm": 1200, "width_mm": 800}],
    "codes": [{"code_type": "gtin", "raw_value": "4601234567890"}],
    "review": {"verdict": "ACCEPTED", "verdict_by": "u-2", "verdict_comment": "ок"},
}


class TestPull:
    def test_items_become_inbound_requests_for_router(self):
        connector, transport, _ = _connector(_items(
            {"id": 101, "title": "Насос", "stageId": "NEED_SCAN",
             "companyId": 7, "updatedTime": "2026-07-29T00:10:00+03:00"},
        ))
        requests, cursor = connector.pull_tasks(None)
        assert requests == [{
            "external_system": "bitrix24",
            "external_ref": "101",
            "protocol_code": "kv_router",
            "subject": {"title": "Насос", "stage_id": "NEED_SCAN", "company_id": 7},
        }]
        assert cursor == "2026-07-29T00:10:00+03:00"
        assert transport.calls[0]["params"]["entityTypeId"] == 166

    def test_cursor_overlap_window_does_not_lose_boundary_edits(self):
        """§9.2: фильтр берёт cursor − 60 с; повторы гасит идемпотентность."""
        connector, transport, _ = _connector(_items())
        connector.pull_tasks("2026-07-29T00:10:00+03:00")
        filter_ = transport.calls[0]["params"]["filter"]
        assert filter_[">=updatedTime"] == "2026-07-29T00:09:00+03:00"

    def test_empty_page_keeps_cursor(self):
        connector, _, _ = _connector(_items())
        _, cursor = connector.pull_tasks("2026-07-29T00:10:00+03:00")
        assert cursor == "2026-07-29T00:10:00+03:00"


class TestPushResult:
    def test_update_maps_only_configured_fields(self):
        """Лишнее поле в CRM хуже отсутствующего — маппится только конфиг."""
        connector, transport, _ = _connector({"result": {}}, {"result": {}})
        connector.push_result(ROW, field_map={
            "length_mm": "ufCrm12Length",
            "verdict": "ufCrm12Verdict",
        })
        update = transport.calls[0]
        assert update["method"] == "crm.item.update"
        assert update["params"]["id"] == 4242
        assert update["params"]["fields"] == {
            "ufCrm12Length": 1200,
            "ufCrm12Verdict": "ACCEPTED",
        }

    def test_timeline_comment_carries_idempotency_marker(self):
        connector, transport, _ = _connector({"result": {}}, {"result": {}})
        connector.push_result(ROW, field_map={"length_mm": "ufCrm12Length"})
        comment = transport.calls[1]["params"]["fields"]["COMMENT"]
        assert "[kvant-scan s-1]" in comment
        assert "ACCEPTED" in comment and "4601234567890" in comment

    def test_row_without_external_ref_is_config_error(self):
        connector, transport, _ = _connector()
        with pytest.raises(BitrixError, match="external_ref"):
            connector.push_result({**ROW, "external_ref": None})
        assert transport.calls == []


class TestRetry:
    def test_flaky_portal_is_retried_with_backoff(self):
        connector, transport, delays = _connector(
            BitrixUnavailable("503"), BitrixUnavailable("503"), _items())
        requests, _ = connector.pull_tasks(None)
        assert requests == []
        assert len(transport.calls) == 3
        assert delays == [1.0, 2.0]  # экспонента от base_delay_s

    def test_exhausted_retries_raise_for_caller_to_keep_task_open(self):
        """I-8: после исчерпания попыток исключение уходит наверх — задание
        остаётся ACCEPTED, а не «закрыто в приложении, потеряно в CRM»."""
        connector, transport, _ = _connector(
            BitrixUnavailable("503"), BitrixUnavailable("503"), BitrixUnavailable("503"))
        with pytest.raises(BitrixUnavailable, match="после 3 попыток"):
            connector.pull_tasks(None)
        assert len(transport.calls) == 3

    def test_fatal_error_is_not_retried(self):
        connector, transport, delays = _connector(BitrixError("нет прав"))
        with pytest.raises(BitrixError):
            connector.pull_tasks(None)
        assert len(transport.calls) == 1 and delays == []
