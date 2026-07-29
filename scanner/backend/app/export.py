"""Строки реестра (Р-13) — общий сборщик для эндпоинта экспорта и воркера.

Одна функция строит строку и для GET /v1/export/sessions, и для доставки
коннектором: расхождение форматов означало бы, что человек в выгрузке видит
одно, а CRM получает другое.
"""

from __future__ import annotations

from typing import Any

from .projections import fold


def registry_row(store: Any, tenant_id: str, session_id: str) -> dict[str, Any] | None:
    """Строка реестра по сессии; None — сессия не завершена (не результат)."""
    events = store.session_events(tenant_id, session_id)
    completed_at = next(
        (e.get("server_ts") for e in events if e["type"] == "session.completed"),
        None,
    )
    if completed_at is None:
        return None
    state = fold(session_id, events)
    ctx = state.to_context()
    protocol_ref = next(
        (e["payload"].get("protocol") for e in events if e["type"] == "session.started"),
        None,
    )
    task_id = next(
        (e["payload"].get("task_id") for e in events if e["type"] == "session.started"),
        None,
    )
    task = store.get_task(tenant_id, task_id) if task_id else None
    return {
        "session_id": session_id,
        "task_id": task_id,
        # Адрес результата во внешней системе — коннектору не нужно
        # ходить за заданием отдельно (§09.4).
        "external_system": task.get("external_system") if task else None,
        "external_ref": task.get("external_ref") if task else None,
        "protocol": protocol_ref,
        "completed_at": completed_at,
        "quality_score": round(ctx.quality_score, 3),
        "steps": {k: v.status for k, v in state.results.items()},
        "measurements": [e["payload"] for e in events if e["type"] == "measurement.recorded"],
        "codes": [e["payload"] for e in events if e["type"] == "code.read"],
        "asset_ids": sorted(state.asset_ids),
        "review": store.get_review(tenant_id, session_id),
    }
