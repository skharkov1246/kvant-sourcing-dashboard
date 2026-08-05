import json
import os
import pathlib

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.store import Store

PROTOCOL_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "docs" / "schemas" / "examples" / "pallet_general_v7.json"
)
SCHEMA_PATH = pathlib.Path(__file__).resolve().parents[1] / "app" / "schema.sql"

OPERATOR = {"X-Tenant-Id": "t-internal", "X-User-Id": "u-1", "X-Roles": "operator"}
REVIEWER = {"X-Tenant-Id": "t-internal", "X-User-Id": "u-2", "X-Roles": "operator,reviewer"}
CUSTOMER = {"X-Tenant-Id": "t-acme", "X-User-Id": "u-9", "X-Roles": "operator"}

# Локальный кластер: initdb + pg_ctl с сокетом в /tmp/pgs на порту 5433
# (или любой другой — через KVANT_PG_ADMIN_DSN). Без него pg-половина скипается.
PG_ADMIN_DSN = os.environ.get(
    "KVANT_PG_ADMIN_DSN", "host=/tmp/pgs port=5433 user=postgres dbname=postgres")
PG_TEST_DB = "kvant_scan_test"


@pytest.fixture(autouse=True)
def _no_cloud_offload(monkeypatch):
    """Тесты не ходят в реальный бакет: облако по умолчанию отсутствует.
    Тесты оффлоада подставляют фейк поверх этой заглушки."""
    monkeypatch.setattr(main_module, "OBJECT_STORAGE", None)


@pytest.fixture()
def protocol() -> dict:
    return json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def _pg_store():
    """PgStore на одноразовой базе со свежепримененной schema.sql.

    Недоступный PostgreSQL пропускает pg-половину матрицы, а не валит её:
    контракт хранилища всё равно закрыт in-memory половиной.
    """
    psycopg = pytest.importorskip("psycopg")
    from app.pg_store import PgStore

    try:
        admin = psycopg.connect(PG_ADMIN_DSN, autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL недоступен: {exc}")
    admin.execute(f"DROP DATABASE IF EXISTS {PG_TEST_DB}")
    admin.execute(f"CREATE DATABASE {PG_TEST_DB}")
    dsn = PG_ADMIN_DSN.replace("dbname=postgres", f"dbname={PG_TEST_DB}")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    store = PgStore(dsn)
    yield store
    admin.close()


@pytest.fixture(params=["memory", "pg"])
def client(protocol, request, monkeypatch) -> TestClient:
    """Одна и та же матрица API-тестов на обоих хранилищах: эталонное
    in-memory задаёт контракт, PostgreSQL обязан быть неотличим."""
    if request.param == "memory":
        store = Store()
    else:
        store = request.getfixturevalue("_pg_store")
        store.reset()
    monkeypatch.setattr(main_module, "STORE", store)
    store.put_protocol(protocol)
    return TestClient(app)
