"""Линтер публикации протоколов (этап 0+, docs/14 §14.6).

Протокол — данные, и технолог правит его без релиза приложения. Обратная
сторона этой свободы: опечатка в условии не падает компиляцией, а молча
прячет шаг — твёрдость по Шору «не собирается никогда», и никто не замечает
месяц. Линтер обязателен ПЕРЕД публикацией версии: ошибки блокируют
публикацию, предупреждения показываются технологу.

Классы правил (каждое родилось из реального дефекта или пункта регламента):
  E1  дубликаты id шагов
  E2  предикат по полю repeat-шага — payload список, предикат всегда ложен
      (дефект g_shore, найден проработкой Р-05)
  E3  ссылка условия на несуществующий шаг
  E4  ссылка «вперёд» без exists:false — шаг ещё не выполнен в линейном
      проходе, предикат всегда ложен, ветка мертва
  E5  eq/in по enum-полю со значением вне options — опечатка = мёртвая ветка
  E6  неизвестная форма условия или оператора — интерпретатор упадёт в рантайме
  E7  enum-поле без options и без source — «закрытый перечень» без перечня
  E8  условия acceptance подчиняются тем же правилам E2–E6
  W1  блокирующий порог glare у шага маркировки — выбитые клейма и гравировка
      на нержавейке читаются именно за счёт бокового блика (проработка Р-11)
  W2  нет settings.estimated_duration_s — приложение не покажет оценку времени
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_CONDITION_FORMS = {"all", "any", "not", "quality_score_at_least", "step"}
_PREDICATE_OPS = {"step", "field", "eq", "neq", "in", "gt", "lt", "exists",
                  "accuracy_class_at_least"}


@dataclass
class LintReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Публикация разрешена только при пустом списке ошибок."""
        return not self.errors


def lint(protocol: dict[str, Any]) -> LintReport:
    report = LintReport()
    steps = protocol.get("steps", [])

    # E1: дубликаты id
    seen: set[str] = set()
    for step in steps:
        sid = step.get("id", "<без id>")
        if sid in seen:
            report.errors.append(f"E1 {sid}: дубликат id шага")
        seen.add(sid)

    step_order = {s["id"]: i for i, s in enumerate(steps) if "id" in s}
    repeat_ids = {s["id"] for s in steps if "repeat" in s and "id" in s}
    enum_options = _enum_options_by_step(steps)

    for i, step in enumerate(steps):
        sid = step.get("id", f"#{i}")
        _lint_condition(step.get("visible_if"), f"шаг {sid}, visible_if",
                        origin_index=i, step_order=step_order,
                        repeat_ids=repeat_ids, enum_options=enum_options,
                        report=report)
        _lint_form(step, sid, report)
        _lint_marking_quality(step, sid, report)

    # E8: acceptance — те же правила; порядок не ограничен (считается в конце)
    acceptance = protocol.get("acceptance") or {}
    for key in ("auto_accept_if", "reject_if"):
        _lint_condition(acceptance.get(key), f"acceptance.{key}",
                        origin_index=len(steps), step_order=step_order,
                        repeat_ids=repeat_ids, enum_options=enum_options,
                        report=report)

    # W2: оценка длительности
    if not (protocol.get("settings") or {}).get("estimated_duration_s"):
        report.warnings.append(
            "W2: нет settings.estimated_duration_s — приложение не покажет "
            "оценку времени в маршрутизаторе")

    return report


# ─────────────────────────────────────────────────────────────────────────────
# Внутреннее
# ─────────────────────────────────────────────────────────────────────────────

def _enum_options_by_step(steps: list[dict[str, Any]]) -> dict[tuple[str, str], list[str]]:
    """(step_id, field_key) → options для enum-полей с явным перечнем."""
    out: dict[tuple[str, str], list[str]] = {}
    for step in steps:
        for f in (step.get("form") or {}).get("fields", []):
            if f.get("type") == "enum" and f.get("options"):
                out[(step.get("id", ""), f["key"])] = f["options"]
    return out


def _lint_condition(cond: Any, where: str, *, origin_index: int,
                    step_order: dict[str, int], repeat_ids: set[str],
                    enum_options: dict[tuple[str, str], list[str]],
                    report: LintReport) -> None:
    if cond is None:
        return
    if not isinstance(cond, dict):
        report.errors.append(f"E6 {where}: условие должно быть объектом")
        return

    forms = set(cond.keys()) & _CONDITION_FORMS
    if not forms:
        report.errors.append(
            f"E6 {where}: неизвестная форма условия {sorted(cond.keys())}")
        return

    if "all" in cond or "any" in cond:
        for sub in cond.get("all") or cond.get("any") or []:
            _lint_condition(sub, where, origin_index=origin_index,
                            step_order=step_order, repeat_ids=repeat_ids,
                            enum_options=enum_options, report=report)
        return
    if "not" in cond:
        _lint_condition(cond["not"], where, origin_index=origin_index,
                        step_order=step_order, repeat_ids=repeat_ids,
                        enum_options=enum_options, report=report)
        return
    if "quality_score_at_least" in cond:
        return

    # Предикат по шагу
    unknown_ops = set(cond.keys()) - _PREDICATE_OPS
    if unknown_ops:
        report.errors.append(
            f"E6 {where}: неизвестные операторы предиката {sorted(unknown_ops)}")

    target = cond["step"]
    if target not in step_order:
        report.errors.append(f"E3 {where}: ссылка на несуществующий шаг «{target}»")
        return

    if "field" in cond and target in repeat_ids:
        report.errors.append(
            f"E2 {where}: предикат по полю repeat-шага «{target}» — payload "
            f"такого шага список, предикат всегда ложен")

    if step_order[target] >= origin_index and cond.get("exists") is not False:
        report.errors.append(
            f"E4 {where}: ссылка вперёд на «{target}» — шаг ещё не выполнен "
            f"в линейном проходе, ветка мертва (легален только exists: false)")

    # E5: значения eq/in против options enum-поля
    field_key = cond.get("field")
    options = enum_options.get((target, field_key)) if field_key else None
    if options:
        values = []
        if "eq" in cond:
            values.append(cond["eq"])
        if "neq" in cond:
            values.append(cond["neq"])
        values.extend(cond.get("in") or [])
        for v in values:
            if isinstance(v, str) and v not in options:
                report.errors.append(
                    f"E5 {where}: значение «{v}» отсутствует в options поля "
                    f"{target}.{field_key} — опечатка даёт мёртвую ветку")


def _lint_form(step: dict[str, Any], sid: str, report: LintReport) -> None:
    for f in (step.get("form") or {}).get("fields", []):
        if f.get("type") == "enum" and not f.get("options") and not f.get("source"):
            report.errors.append(
                f"E7 шаг {sid}, поле {f.get('key')}: enum без options и без "
                f"source — закрытый перечень без перечня (Р-03 п.2)")


_MARKING_HINTS = ("mark", "маркиров", "шильдик")


def _lint_marking_quality(step: dict[str, Any], sid: str, report: LintReport) -> None:
    """W1: блик — не повод блокировать кадр маркировки (проработка Р-11):
    выбитые клейма и гравировка на нержавейке читаются за счёт блика."""
    if step.get("kind") != "photo":
        return
    text = (sid + " " + str(step.get("title", ""))).lower()
    if not any(h in text for h in _MARKING_HINTS):
        return
    quality = step.get("quality") or {}
    if quality.get("glare_ratio") is not None and quality.get("on_violation") == "block":
        report.warnings.append(
            f"W1 шаг {sid}: блокирующий порог glare_ratio у маркировки — "
            f"выбитые клейма читаются за счёт блика; рекомендован warn")
