"""Самолечение соединения PgStore.

Управляемые облачные базы рвут простаивающие соединения (у Timeweb —
примерно через час тишины). Стенд, который после этого отвечает 500 до
ручного перезапуска, — не прод. Здесь обрыв воспроизводится жёстко:
соединение закрывается под хранилищем, а хранилище обязано пережить это
незаметно для вызывающего.
"""

import pytest


def _skip_without_pg(request):
    return request.getfixturevalue("_pg_store")


def test_store_survives_closed_connection(request):
    store = _skip_without_pg(request)
    store.reset()
    store.upsert_task({
        "id": "6f000000-0000-4000-8000-0000000000a1", "tenant_id": "t-internal", "site_id": None,
        "external_system": "test", "external_ref": "reconnect-1",
        "protocol_code": "kv_router", "protocol_version": None,
        "subject": {"kind": "liveness"}, "priority": 50,
        "due_at": None, "assignee_pool": None, "state": "NEW",
        "created_at": "2026-08-06T00:00:00+00:00",
    })

    store._raw.close()
    assert store._raw.closed

    got = store.get_task("t-internal", "6f000000-0000-4000-8000-0000000000a1")
    assert got is not None and got["state"] == "NEW"
    assert not store._raw.closed


def test_reconnect_reuses_connection_when_alive(request):
    store = _skip_without_pg(request)
    alive = store._raw
    store.list_tasks("t-internal")
    assert store._raw is alive
