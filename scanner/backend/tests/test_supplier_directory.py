"""Справочник поставщиков из CRM (docs/09 §9.5): офлайн-подсказки трассировки.

Смысл набора: контрагенты приезжают из crm.company.list снапшотом в
динамический справочник; версия растёт только при изменении контента —
ежепятиминутный опрос без изменений не сбрасывает 304-кэш устройств;
имена файловых справочников динамической записью не перекрываются.

Матрица memory/pg — через фикстур client; портал — HTTP-эмулятор.
"""

import pytest
from fastapi.testclient import TestClient

from app.bitrix_emulator import BitrixState, create_emulator
from connectors.bitrix import BitrixConnector
from connectors.bitrix_poller import BitrixPoller
from tests.conftest import OPERATOR, REVIEWER
from tests.test_bitrix_emulator import EmulatorTransport
from tests.test_bitrix_poller import ClientPlatform


@pytest.fixture()
def emulator():
    app, state = create_emulator(BitrixState())
    return TestClient(app), state


class PlatformWithDirectories(ClientPlatform):
    def put_directory(self, name, sections):
        response = self.http.put(f"/v1/directories/{name}",
                                 json={"sections": sections}, headers=REVIEWER)
        assert response.status_code == 200, response.text
        return response.json()


class TestPullSuppliers:
    def test_companies_become_supplier_rows(self, emulator):
        http, state = emulator
        state.seed_company("Ningbo Seals Co", inn=None)
        state.seed_company("ООО Уплотнитель", inn="7701234567")
        connector = BitrixConnector(transport=EmulatorTransport(http))
        suppliers = connector.pull_suppliers()
        assert {s["name"] for s in suppliers} == {"Ningbo Seals Co", "ООО Уплотнитель"}
        assert any(s["inn"] == "7701234567" for s in suppliers)


class TestDynamicDirectory:
    def test_version_bumps_only_on_content_change(self, client):
        put = lambda values: client.put(  # noqa: E731
            "/v1/directories/suppliers",
            json={"sections": {"suppliers": {"values": values}}}, headers=REVIEWER,
        ).json()
        assert put([{"name": "А"}])["version"] == 1
        # Тот же снапшот — версия не растёт, 304-кэш устройств живёт.
        assert put([{"name": "А"}])["version"] == 1
        assert put([{"name": "А"}, {"name": "Б"}])["version"] == 2

    def test_served_with_etag_and_304(self, client):
        client.put("/v1/directories/suppliers",
                   json={"sections": {"suppliers": {"values": [{"name": "А"}]}}},
                   headers=REVIEWER)
        first = client.get("/v1/directories/suppliers", headers=OPERATOR)
        assert first.status_code == 200
        etag = first.headers["ETag"]
        assert etag == '"suppliers-v1"'
        cached = client.get("/v1/directories/suppliers",
                            headers={**OPERATOR, "If-None-Match": etag})
        assert cached.status_code == 304

    def test_file_directory_names_are_reserved(self, client):
        r = client.put("/v1/directories/materials",
                       json={"sections": {"x": {"values": []}}}, headers=REVIEWER)
        assert r.status_code == 409

    def test_put_requires_reviewer(self, client):
        r = client.put("/v1/directories/suppliers",
                       json={"sections": {"x": {"values": []}}}, headers=OPERATOR)
        assert r.status_code == 403


class TestSupplierSyncEndToEnd:
    def test_crm_snapshot_reaches_device_endpoint(self, client, emulator, tmp_path):
        http, state = emulator
        state.seed_company("ООО Уплотнитель", inn="7701234567")
        poller = BitrixPoller(
            connector=BitrixConnector(transport=EmulatorTransport(http)),
            platform=PlatformWithDirectories(client),
            cursor_file=tmp_path / "cursor",
        )
        doc = poller.sync_suppliers()
        assert doc["version"] == 1
        # Повтор без изменений в CRM — версия та же.
        assert poller.sync_suppliers()["version"] == 1

        served = client.get("/v1/directories/suppliers", headers=OPERATOR).json()
        values = served["sections"]["suppliers"]["values"]
        assert values[0]["name"] == "ООО Уплотнитель"
        assert values[0]["inn"] == "7701234567"
