"""Свёртка лога событий в состояние сессии — серверная половина.

Зеркало `shared/.../sync/SessionProjection.kt`. Это единственный способ получить
состояние сессии (ADR-0002): «обновления записи» в системе не существует.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .protocol import SessionContext, StepResult


@dataclass
class SessionState:
    session_id: str
    state: str = "DRAFT"
    results: dict[str, StepResult] = field(default_factory=dict)
    measurement_accuracy: dict[str, str] = field(default_factory=dict)
    asset_ids: set[str] = field(default_factory=set)
    last_seq: int = -1
    started_at: str | None = None
    finished_at: str | None = None

    def to_context(self) -> SessionContext:
        return SessionContext(results=self.results, measurement_accuracy=self.measurement_accuracy)


def fold(session_id: str, events: Iterable[dict[str, Any]]) -> SessionState:
    """Применяет события по возрастанию seq.

    Дубликаты по seq игнорируются (побеждает первое применённое), пропуски в
    нумерации не мешают: сервер видит валидный префикс сессии, даже если хвост
    ещё не доехал.
    """
    state = SessionState(session_id=session_id)
    for event in sorted(events, key=lambda e: e["seq"]):
        if event["seq"] <= state.last_seq:
            continue
        state = apply(state, event)
    return state


def apply(state: SessionState, event: dict[str, Any]) -> SessionState:
    state.last_seq = event["seq"]
    payload = event.get("payload") or {}
    etype = event["type"]

    if etype == "session.started":
        state.state = "ACTIVE"
        state.started_at = event.get("device_ts")

    elif etype == "step.completed":
        step_id = payload.get("step_id")
        if step_id:
            state.results[step_id] = StepResult(
                step_id=step_id,
                status="COMPLETED",
                data=payload.get("data") or {},
                asset_ids=payload.get("asset_ids") or [],
                quality_score=payload.get("quality_score"),
                violations=payload.get("violations") or [],
            )
            state.asset_ids.update(payload.get("asset_ids") or [])

    elif etype == "step.skipped":
        step_id = payload.get("step_id")
        if step_id:
            state.results[step_id] = StepResult(step_id=step_id, status="SKIPPED")

    elif etype == "measurement.recorded":
        step_id = payload.get("step_id")
        accuracy = payload.get("accuracy_class")
        if step_id and accuracy:
            state.measurement_accuracy[step_id] = accuracy

    elif etype == "asset.attached":
        asset_id = payload.get("asset_id")
        if asset_id:
            state.asset_ids.add(asset_id)

    elif etype == "session.paused":
        state.state = "PAUSED"

    elif etype == "session.completed":
        state.state = "COMPLETED_LOCAL"
        state.finished_at = event.get("device_ts")

    elif etype == "session.abandoned":
        state.state = "ABANDONED"
        state.finished_at = event.get("device_ts")

    return state
