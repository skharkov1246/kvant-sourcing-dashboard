"""Предохранители из живого архива: сессия с дырами не проходит молча.

Источник уроков — реальный «Реестр деталей и замеров»: замеры у 4 позиций
из 102, масштабная опора на 16 кадрах из 87. Идиотская ошибка должна
ловиться правилом, а не удачей."""

from app.acceptance import archive_lesson_flags
from app.protocol import SessionContext, StepResult


def _proto(steps):
    return {"code": "t", "version": 1, "steps": steps}


def test_measure_step_without_measurements_is_flagged():
    proto = _proto([
        {"id": "p1", "kind": "photo"},
        {"id": "m1", "kind": "measure"},
    ])
    ctx = SessionContext(results={
        "p1": StepResult("p1", "COMPLETED", asset_ids=["a1"]),
        "m1": StepResult("m1", "SKIPPED"),
    })
    flags = archive_lesson_flags(proto, ctx)
    assert any("ни одного замера" in f for f in flags)


def test_measurements_present_no_flag():
    proto = _proto([{"id": "m1", "kind": "measure"}])
    ctx = SessionContext(
        results={"m1": StepResult("m1", "COMPLETED")},
        measurement_accuracy={"m1": "C"},
    )
    assert archive_lesson_flags(proto, ctx) == []


def test_protocol_without_measure_steps_is_not_punished():
    """Краудскан и онбординг замеров не требуют — флага быть не должно."""
    proto = _proto([{"id": "p1", "kind": "photo"}])
    ctx = SessionContext(results={"p1": StepResult("p1", "COMPLETED", asset_ids=["a1"])})
    assert archive_lesson_flags(proto, ctx) == []


def test_photo_steps_done_but_session_has_no_frames_is_flagged():
    proto = _proto([{"id": "p1", "kind": "photo"}])
    ctx = SessionContext(results={"p1": StepResult("p1", "COMPLETED", asset_ids=[])})
    flags = archive_lesson_flags(proto, ctx)
    assert any("нет ни одного кадра" in f for f in flags)


def test_frames_attached_to_any_step_calm_the_guard():
    """Кадр, прикреплённый позже другим шагом/событием, гасит флаг."""
    proto = _proto([{"id": "p1", "kind": "photo"}, {"id": "p2", "kind": "photo"}])
    ctx = SessionContext(results={
        "p1": StepResult("p1", "COMPLETED", asset_ids=[]),
        "p2": StepResult("p2", "COMPLETED", asset_ids=["a9"]),
    })
    assert archive_lesson_flags(proto, ctx) == []
