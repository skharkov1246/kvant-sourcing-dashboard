"""Планировщик опроса Bitrix (docs/09 §9.2): повторы не плодят задания.

Смысл набора: два прохода поллера по одному «порталу» дают одно задание;
новая сделка между проходами создаётся ровно один раз; отказ портала не
сдвигает курсор — окно не теряется. Портал — HTTP-эмулятор, платформа —
фикстур client (матрица memory/pg).
"""

import pytest
from fastapi.testclient import TestClient

from app.bitrix_emulator import BitrixState, create_emulator
from connectors.bitrix import BitrixConnector, BitrixUnavailable
from connectors.bitrix_poller import BitrixPoller
from tests.conftest import OPERATOR
from tests.test_bitrix_emulator import EmulatorTransport


class ClientPlatform:
    """PlatformClient поверх TestClient платформы."""

    def __init__(self, http):
        self.http = http

    def submit_inbound(self, request, idempotency_key):
        response = self.http.post(
            "/v1/inbound/tasks", json=request,
            headers={**OPERATOR, "Idempotency-Key": idempotency_key},
        )
        assert response.status_code in (200, 202), response.text
        return response.json()


@pytest.fixture()
def poller_env(client, tmp_path):
    emulator_app, state = create_emulator(BitrixState())
    poller = BitrixPoller(
        connector=BitrixConnector(
            transport=EmulatorTransport(TestClient(emulator_app)),
            protocol_code="pallet_general",
        ),
        platform=ClientPlatform(client),
        cursor_file=tmp_path / "cursor",
    )
    return poller, state, client


def _platform_tasks(client):
    return [t for t in client.get("/v1/tasks", headers=OPERATOR).json()["items"]
            if t["external_system"] == "bitrix24"]


class TestPoller:
    def test_two_passes_do_not_duplicate(self, poller_env):
        poller, state, client = poller_env
        state.seed_deal("Насос", updated="2026-08-03T07:00:00+00:00")

        first = poller.run_once()
        assert first == {"pulled": 1, "created": 1, "deduplicated": 0, "skipped_round": 0}

        # Второй проход: перекрытие окна вернёт ту же сделку — дедуп, не дубль.
        second = poller.run_once()
        assert second["created"] == 0
        assert len(_platform_tasks(client)) == 1

    def test_new_deal_between_passes_created_once(self, poller_env):
        poller, state, client = poller_env
        state.seed_deal("Первая", updated="2026-08-03T07:00:00+00:00")
        poller.run_once()
        state.seed_deal("Вторая", updated="2026-08-03T07:10:00+00:00")
        stats = poller.run_once()
        assert stats["created"] == 1
        titles = sorted(t["subject"]["title"] for t in _platform_tasks(client))
        assert titles == ["Вторая", "Первая"] or titles == sorted(["Первая", "Вторая"])

    def test_portal_outage_skips_round_and_keeps_cursor(self, poller_env, tmp_path):
        poller, state, client = poller_env
        state.seed_deal("До обрыва", updated="2026-08-03T07:00:00+00:00")
        poller.run_once()
        cursor_before = poller.cursor_file.read_text()

        class DeadTransport:
            def call(self, method, params):
                raise BitrixUnavailable("портал лёг")

        dead = BitrixPoller(
            connector=BitrixConnector(transport=DeadTransport(),
                                      retry=poller.connector.retry),
            platform=poller.platform,
            cursor_file=poller.cursor_file,
        )
        stats = dead.run_once()
        assert stats["skipped_round"] == 1
        # Курсор не сдвинулся — следующий живой проход заберёт то же окно.
        assert poller.cursor_file.read_text() == cursor_before

    def test_cursor_survives_restart(self, poller_env):
        """Новый экземпляр поллера (рестарт процесса) читает курсор из файла
        и не перекачивает историю целиком."""
        poller, state, client = poller_env
        state.seed_deal("Старая", updated="2026-08-03T06:00:00+00:00")
        poller.run_once()

        restarted = BitrixPoller(
            connector=poller.connector,
            platform=poller.platform,
            cursor_file=poller.cursor_file,
        )
        stats = restarted.run_once()
        assert stats["created"] == 0  # история не перекачана
