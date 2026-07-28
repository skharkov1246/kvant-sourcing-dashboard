import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from app.main import STORE, app

PROTOCOL_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "docs" / "schemas" / "examples" / "pallet_general_v7.json"
)

OPERATOR = {"X-Tenant-Id": "t-internal", "X-User-Id": "u-1", "X-Roles": "operator"}
REVIEWER = {"X-Tenant-Id": "t-internal", "X-User-Id": "u-2", "X-Roles": "operator,reviewer"}
CUSTOMER = {"X-Tenant-Id": "t-acme", "X-User-Id": "u-9", "X-Roles": "operator"}


@pytest.fixture()
def protocol() -> dict:
    return json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def client(protocol) -> TestClient:
    STORE.__init__()  # чистое состояние на каждый тест
    STORE.put_protocol(protocol)
    return TestClient(app)
