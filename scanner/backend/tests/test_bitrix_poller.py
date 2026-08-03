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
from tests.conftest import OPERATOR, REVIEWER
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

    def put_directory(self, name, sections):
        response = self.http.put(
            f"/v1/directories/{name}", json={"sections": sections},
            headers=REVIEWER,
        )
        assert response.status_code == 200, response.text
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


class TestSuppliersSync:
    """Снапшот поставщиков CRM → динамический справочник suppliers (§9.5)."""

    def _directory(self, client):
        return client.get("/v1/directories/suppliers", headers=OPERATOR)

    def test_companies_become_directory(self, poller_env):
        poller, state, client = poller_env
        state.seed_company("ООО Уплотнитель", inn="7701234567")
        state.seed_company("Ningbo Seals Co")

        published = poller.sync_suppliers()
        assert published["version"] == 1

        doc = self._directory(client).json()
        values = doc["sections"]["suppliers"]["values"]
        assert {v["name"] for v in values} == {"ООО Уплотнитель", "Ningbo Seals Co"}
        seal = next(v for v in values if v["inn"])
        # ref — id контрагента в CRM: заявка ссылается на реального поставщика.
        assert seal["inn"] == "7701234567" and seal["ref"]

    def test_unchanged_crm_keeps_version_and_304_cache(self, poller_env):
        """Ежепятиминутный опрос без изменений в CRM не сбрасывает
        304-кэш всех устройств: версия растёт только на новом контенте."""
        poller, state, client = poller_env
        state.seed_company("ООО Уплотнитель")
        assert poller.sync_suppliers()["version"] == 1
        assert poller.sync_suppliers()["version"] == 1

        etag = self._directory(client).headers["ETag"]
        cached = client.get("/v1/directories/suppliers",
                            headers={**OPERATOR, "If-None-Match": etag})
        assert cached.status_code == 304

    def test_new_company_bumps_version(self, poller_env):
        poller, state, client = poller_env
        state.seed_company("Первый")
        poller.sync_suppliers()
        state.seed_company("Второй")
        assert poller.sync_suppliers()["version"] == 2

    def test_portal_outage_keeps_previous_snapshot(self, poller_env):
        poller, state, client = poller_env
        state.seed_company("ООО Уплотнитель")
        poller.sync_suppliers()

        class DeadTransport:
            def call(self, method, params):
                raise BitrixUnavailable("портал лёг")

        dead = BitrixPoller(
            connector=BitrixConnector(transport=DeadTransport(),
                                      retry=poller.connector.retry),
            platform=poller.platform,
            cursor_file=poller.cursor_file,
        )
        assert dead.sync_suppliers() is None
        # Прежний справочник остаётся валиден — подсказки не пропадают.
        doc = self._directory(client).json()
        assert doc["sections"]["suppliers"]["values"]
