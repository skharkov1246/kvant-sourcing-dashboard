"""Строки реестра (Р-13) — общий сборщик для эндпоинта экспорта и воркера.

Одна функция строит строку и для GET /v1/export/sessions, и для доставки
коннектором: расхождение форматов означало бы, что человек в выгрузке видит
одно, а CRM получает другое.
"""

from __future__ import annotations

from typing import Any

from .projections import fold
from .series import analyze_surface

_SECTION_ORDER = {"край 1": 0, "середина": 1, "край 2": 2}


def surface_stats_from_grid(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Статистика посадочных поверхностей по строкам сетки 3×2 (Р-10 п.4.1).

    Оператор снимает сырые числа, арифметику — овальность, конусность, базу
    восстановления номинала — считает сервер (series.analyze_surface).
    Неполная сетка не считается «как получится»: строка честно помечается
    ошибкой, конструктор видит, чего не хватает.
    """
    groups: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault((r.get("step_no"), r.get("surface")), []).append(r)

    out: list[dict[str, Any]] = []
    for (step_no, surface), grp in sorted(
        groups.items(), key=lambda kv: (kv[0][0] or 0, str(kv[0][1]))
    ):
        sections: dict[str, list[float]] = {}
        for r in grp:
            if r.get("value_mm") is not None:
                sections.setdefault(str(r.get("section")), []).append(float(r["value_mm"]))
        ordered = [sections[name] for name in
                   sorted(sections, key=lambda s: _SECTION_ORDER.get(s, 9))]
        if len(ordered) < 3 or any(len(s) < 2 for s in ordered):
            out.append({
                "step_no": step_no, "surface": surface,
                "error": "сетка неполна: нужно 3 сечения × 2 замера (Р-10 п.4.1)",
            })
            continue
        stats = analyze_surface(ordered, surface="shaft" if surface == "вал" else "bore")
        out.append({
            "step_no": step_no,
            "surface": surface,
            "min_mm": stats.min_mm,
            "max_mm": stats.max_mm,
            "ovality_mm": stats.ovality_mm,
            "conicity_mm": stats.conicity_mm,
            "base_mm": stats.base_mm,
            "heavy_wear": stats.heavy_wear,
            "advice": stats.advice,
        })
    return out


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
        "surface_stats": _grid_stats(state) or None,
    }


def _grid_stats(state: Any) -> list[dict[str, Any]]:
    """Строки сетки замеров из repeat-шагов сессии (payload — список)."""
    grid_rows: list[dict[str, Any]] = []
    for res in state.results.values():
        if isinstance(res.data, list):
            grid_rows.extend(
                r for r in res.data
                if isinstance(r, dict) and "value_mm" in r and "section" in r
            )
    return surface_stats_from_grid(grid_rows) if grid_rows else []
