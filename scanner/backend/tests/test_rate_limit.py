"""Ограничение частоты: шторм с одного адреса получает 429, здоровье — нет."""

from fastapi.testclient import TestClient

from app import main as m
from tests.conftest import OPERATOR


def test_storm_gets_429_then_window_frees(monkeypatch):
    monkeypatch.setattr(m, "_RATE_MAX", 5)
    m._rate_hits.clear()
    client = TestClient(m.app)
    codes = [client.get("/v1/tasks", headers=OPERATOR).status_code for _ in range(8)]
    assert 429 in codes
    assert codes[0] != 429

    body = client.get("/v1/tasks", headers=OPERATOR)
    assert body.status_code == 429
    assert body.json()["code"] == "rate_limited"
    m._rate_hits.clear()


def test_health_is_never_limited(monkeypatch):
    monkeypatch.setattr(m, "_RATE_MAX", 2)
    m._rate_hits.clear()
    client = TestClient(m.app)
    codes = [client.get("/health").status_code for _ in range(10)]
    assert all(c == 200 for c in codes)
    m._rate_hits.clear()
