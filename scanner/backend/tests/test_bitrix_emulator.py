"""Коннектор против HTTP-эмулятора Bitrix: от опроса до обратной записи.

Смысл набора: BitrixConnector проверяется не на моке транспорта, а на
HTTP-форме портала — тем же путём, каким пойдёт настоящий вебхук. Полный
цикл: сделка NEED_SCAN → pull_tasks → inbound-задание → съёмка → вердикт
ACCEPTED → экспорт-воркер → UF-поля и комментарий с маркером В ЭМУЛЯТОРЕ,
задание EXPORTED.

Матрица memory/pg платформы — через фикстур client; эмулятор — отдельный
TestClient.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.bitrix_emulator import BitrixState, create_emulator
from app.export_worker import ExportWorker
from connectors.bitrix import BitrixConnector, BitrixUnavailable
from tests.conftest import OPERATOR, REVIEWER

TENANT = "t-internal"


class EmulatorTransport:
    """Транспорт коннектора поверх HTTP-клиента эмулятора — та же форма
    вызова, что у боевого вебхука: POST /rest/{method} с JSON-параметрами."""

    def __init__(self, http: TestClient):
        self.http = http

    def call(self, method, params):
        response = self.http.post(f"/rest/{method}", json=params)
        if response.status_code >= 500:
            raise BitrixUnavailable(f"HTTP {response.status_code}")
        return response.json()


@pytest.fixture()
def bitrix():
    app, state = create_emulator(BitrixState())
    return TestClient(app), state


def _event(session_id, seq, etype, payload):
    return {"client_event_id": str(uuid.uuid4()), "session_id": session_id,
            "seq": seq, "type": etype, "device_ts": "2026-08-03T06:00:00Z",
            "payload": payload}


class TestPullFromEmulator:
    def test_deals_become_inbound_requests(self, bitrix):
        http, state = bitrix
        state.seed_deal("Насос центробежный", updated="2026-08-03T05:00:00+00:00")
        state.seed_deal("Редуктор", updated="2026-08-03T05:30:00+00:00")
        state.seed_deal("Не наша стадия", stage_id="WON")

        connector = BitrixConnector(transport=EmulatorTransport(http))
        requests, cursor = connector.pull_tasks(None)

        assert [r["subject"]["title"] for r in requests] == ["Насос центробежный", "Редуктор"]
        assert cursor == "2026-08-03T05:30:00+00:00"

    def test_cursor_window_sees_boundary_edits(self, bitrix):
        """Перекрытие 60 с: правка ровно на границе курсора не теряется."""
        http, state = bitrix
        state.seed_deal("Старая", updated="2026-08-03T04:00:00+00:00")
        state.seed_deal("Граничная", updated="2026-08-03T04:59:30+00:00")
        connector = BitrixConnector(transport=EmulatorTransport(http))
        requests, _ = connector.pull_tasks("2026-08-03T05:00:00+00:00")
        assert [r["subject"]["title"] for r in requests] == ["Граничная"]


class TestFullLoopThroughEmulator:
    def test_deal_to_exported_with_writeback(self, client, bitrix):
        http, state = bitrix
        deal = state.seed_deal("Паллета насосов", updated="2026-08-03T05:00:00+00:00")
        # В фикстуре платформы активен pallet_general — маршрутизация протокола
        # конфигурируется на коннекторе (в бою это kv_router).
        connector = BitrixConnector(transport=EmulatorTransport(http),
                                    protocol_code="pallet_general")

        # 1. Опрос портала → inbound-задание платформы.
        requests, _ = connector.pull_tasks(None)
        inbound = client.post("/v1/inbound/tasks", json=requests[0],
                              headers={**OPERATOR, "Idempotency-Key": str(uuid.uuid4())})
        task_id = inbound.json()["task_id"]

        # 2. Съёмка по заданию и вердикт контролёра.
        client.post("/v1/sync/events", json={"device_id": "d1", "events": [
            _event("s-btx-1", 1, "session.started",
                   {"protocol": "pallet_general@7", "task_id": task_id}),
            _event("s-btx-1", 2, "step.completed",
                   {"step_id": "overview", "quality_score": 0.9}),
            _event("s-btx-1", 3, "session.completed", {}),
        ]}, headers=OPERATOR)
        client.post("/v1/review/s-btx-1/verdict", headers=REVIEWER,
                    json={"verdict": "ACCEPTED", "comment": "ок"})

        # 3. Экспорт-воркер пишет обратно в «портал».
        stats = ExportWorker(
            main_module.STORE, connector,
            field_map={"verdict": "ufCrm12Verdict", "quality_score": "ufCrm12Quality"},
        ).run_once(TENANT)
        assert stats["confirmed"] == 1

        # Обратная запись дошла: UF-поля на элементе, маркер в таймлайне.
        item = state.items[deal["id"]]
        assert item["ufCrm12Verdict"] == "ACCEPTED"
        assert item["ufCrm12Quality"] == 0.9
        comment = state.timeline[-1]
        assert comment["entity_id"] == deal["id"]
        assert "[kvant-scan s-btx-1]" in comment["comment"]

        # Задание закрыто только ПОСЛЕ подтверждения портала (I-8).
        task = client.get(f"/v1/tasks/{task_id}", headers=OPERATOR).json()
        assert task["state"] == "EXPORTED"

    def test_emulator_survives_repeat_writeback(self, client, bitrix):
        """Повтор push_result (потерян ответ) не дублирует ничего страшного:
        UF-поля идемпотентны по значению, маркер в комментарии одинаковый."""
        http, state = bitrix
        deal = state.seed_deal("Повторная", updated="2026-08-03T05:00:00+00:00")
        connector = BitrixConnector(transport=EmulatorTransport(http))
        row = {
            "session_id": "s-btx-2", "external_ref": str(deal["id"]),
            "protocol": "kv_router@1", "quality_score": 0.8,
            "measurements": [], "codes": [], "review": {"verdict": "ACCEPTED"},
        }
        connector.push_result(row, field_map={"verdict": "ufCrm12Verdict"})
        connector.push_result(row, field_map={"verdict": "ufCrm12Verdict"})
        markers = [c for c in state.timeline if "[kvant-scan s-btx-2]" in c["comment"]]
        assert len(markers) == 2  # два комментария с ОДНИМ маркером —
        # приёмная сторона отличает повтор по маркеру, поле не испорчено
        assert state.items[deal["id"]]["ufCrm12Verdict"] == "ACCEPTED"
