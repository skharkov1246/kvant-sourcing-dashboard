"""Приём заданий извне, раздача с учётом возможностей устройства, трассировка."""

from tests.conftest import CUSTOMER, OPERATOR, REVIEWER

TASK = {
    "external_system": "wms-acme",
    "external_ref": "REQ-88213",
    "protocol_code": "pallet_general",
    "subject": {"sku": "ART-99120", "name": "Кабель ВВГнг 3х2.5"},
}


class TestInbound:
    def test_idempotency_key_prevents_duplicate(self, client):
        h = {**OPERATOR, "Idempotency-Key": "wms-acme:REQ-88213"}
        a = client.post("/v1/inbound/tasks", json=TASK, headers=h).json()
        b = client.post("/v1/inbound/tasks", json=TASK, headers=h).json()
        assert a["task_id"] == b["task_id"]
        assert b["deduplicated"] is True

    def test_external_ref_dedupes_even_with_new_key(self, client):
        client.post("/v1/inbound/tasks", json=TASK, headers={**OPERATOR, "Idempotency-Key": "k1"})
        again = client.post("/v1/inbound/tasks", json=TASK, headers={**OPERATOR, "Idempotency-Key": "k2"})
        assert again.json()["deduplicated"] is True

    def test_unknown_protocol_names_the_field(self, client):
        bad = {**TASK, "protocol_code": "nonexistent"}
        resp = client.post("/v1/inbound/tasks", json=bad, headers={**OPERATOR, "Idempotency-Key": "k3"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["field"] == "protocol_code"


class TestSyncPullGating:
    def _pull(self, client, headers, schema_version=1, accuracy="B"):
        return client.post(
            "/v1/sync/pull",
            json={
                "device_id": "d1",
                "capabilities": {
                    "schema_version": schema_version,
                    "app_version": "1.0.0",
                    "max_accuracy_class": accuracy,
                },
                "scopes": ["tasks", "protocols"],
            },
            headers=headers,
        ).json()

    def test_task_is_delivered_to_capable_device(self, client):
        client.post("/v1/inbound/tasks", json=TASK, headers={**OPERATOR, "Idempotency-Key": "k1"})
        changes = self._pull(client, OPERATOR)["changes"]
        assert any(c["type"] == "task.upsert" for c in changes)

    def test_old_app_gets_no_incompatible_protocol(self, client):
        changes = self._pull(client, OPERATOR, schema_version=0)["changes"]
        assert not any(c["type"] == "protocol.upsert" for c in changes)

    def test_other_tenant_sees_no_tasks(self, client):
        client.post("/v1/inbound/tasks", json=TASK, headers={**OPERATOR, "Idempotency-Key": "k1"})
        changes = self._pull(client, CUSTOMER)["changes"]
        assert not any(c["type"] == "task.upsert" for c in changes)


class TestTraceConfidence:
    BODY = {"code_type": "gtin", "code_value": "04607034171234", "supplier_name": "ООО Ромашка"}

    def test_external_user_can_only_declare(self, client):
        link = client.post("/v1/items/item-1/trace", json=self.BODY, headers=CUSTOMER).json()
        assert link["confidence"] == "declared"
        assert link["declared_by_tenant_id"] == "t-acme"

    def test_reviewer_can_verify(self, client):
        link = client.post("/v1/items/item-1/trace", json=self.BODY, headers=REVIEWER).json()
        assert link["confidence"] == "verified"

    def test_confidence_is_not_taken_from_client(self, client):
        forged = {**self.BODY, "confidence": "verified"}
        link = client.post("/v1/items/item-1/trace", json=forged, headers=CUSTOMER).json()
        assert link["confidence"] == "declared"

    def test_links_accumulate_rather_than_overwrite(self, client):
        client.post("/v1/items/item-2/trace", json=self.BODY, headers=CUSTOMER)
        client.post("/v1/items/item-2/trace", json={**self.BODY, "supplier_name": "ООО Василёк"}, headers=REVIEWER)
        links = client.get("/v1/items/item-2/trace", headers=REVIEWER).json()["links"]
        assert len(links) == 2
        assert {l["confidence"] for l in links} == {"declared", "verified"}
