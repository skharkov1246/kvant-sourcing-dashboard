"""Интерпретатор протокола съёмки — серверная половина.

Зеркало `shared/.../protocol/ProtocolInterpreter.kt`. Клиент и сервер обязаны
получать ОДИНАКОВЫЙ результат из одного состояния сессии: клиент решает, какой
шаг показать, сервер — принимать ли сессию автоматически. Расхождение означает,
что кладовщик и диспетчер видят разные данные.

Тесты `tests/test_protocol.py` содержат общий набор случаев, продублированный
в тестах Kotlin-ядра.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SUPPORTED_SCHEMA_VERSION = 1

ACCURACY_RANK = {"A": 0, "B": 1, "C": 2, "D": 3}


def accuracy_at_least(actual: str, required: str) -> bool:
    """A — лучший класс, поэтому «не хуже» означает меньший ранг."""
    return ACCURACY_RANK[actual] <= ACCURACY_RANK[required]


@dataclass
class StepResult:
    step_id: str
    status: str  # PENDING | COMPLETED | SKIPPED | UNSUPPORTED | BLOCKED
    data: dict[str, Any] = field(default_factory=dict)
    asset_ids: list[str] = field(default_factory=list)
    quality_score: float | None = None
    violations: list[str] = field(default_factory=list)


@dataclass
class SessionContext:
    results: dict[str, StepResult] = field(default_factory=dict)
    measurement_accuracy: dict[str, str] = field(default_factory=dict)

    @property
    def quality_score(self) -> float:
        """Средняя оценка по завершённым шагам.

        Шаг без оценки считается идеальным — иначе необязательные шаги
        несправедливо тянули бы сессию вниз.
        """
        scored = [r for r in self.results.values() if r.status == "COMPLETED"]
        if not scored:
            return 0.0
        return sum(r.quality_score if r.quality_score is not None else 1.0 for r in scored) / len(scored)


class ProtocolError(ValueError):
    """Документ протокола не может быть исполнен."""


def check_compatibility(protocol: dict[str, Any], supported: int = SUPPORTED_SCHEMA_VERSION) -> dict[str, Any]:
    """Проверка совместимости — первый эшелон, до раздачи задания устройству."""
    schema_version = protocol.get("schema_version")
    if schema_version is None:
        raise ProtocolError("В документе протокола нет schema_version")
    if schema_version > supported:
        return {
            "ok": False,
            "reason": (
                f"Протокол {protocol['code']}@{protocol['version']} рассчитан на формат "
                f"{schema_version}, поддерживается {supported}"
            ),
        }
    known_kinds = {"photo", "video", "measure", "code", "form", "weight", "note", "confirm"}
    blocking = [
        s["id"] for s in protocol.get("steps", [])
        if s.get("kind") not in known_kinds and s.get("required", True)
    ]
    if blocking:
        return {"ok": False, "reason": f"Неподдерживаемые обязательные шаги: {', '.join(blocking)}"}
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# Вычисление условий
# ─────────────────────────────────────────────────────────────────────────────

_OPERATORS = ("eq", "neq", "in", "gt", "lt", "exists")


def evaluate(condition: dict[str, Any] | None, ctx: SessionContext) -> bool:
    """Декларативное условие без скриптов (§03.5).

    Ровно пять форм: all / any / not / quality_score_at_least / предикат по шагу.
    """
    if condition is None:
        return True
    if "all" in condition:
        return all(evaluate(c, ctx) for c in condition["all"])
    if "any" in condition:
        return any(evaluate(c, ctx) for c in condition["any"])
    if "not" in condition:
        return not evaluate(condition["not"], ctx)
    if "quality_score_at_least" in condition:
        return ctx.quality_score >= condition["quality_score_at_least"]
    if "step" in condition:
        return _evaluate_step(condition, ctx)
    raise ProtocolError(f"Неизвестная форма условия: {sorted(condition.keys())}")


def _evaluate_step(pred: dict[str, Any], ctx: SessionContext) -> bool:
    step_id = pred["step"]
    result = ctx.results.get(step_id)
    if result is None:
        # Отсутствующий шаг удовлетворяет только явному exists: false.
        return pred.get("exists") is False

    if "accuracy_class_at_least" in pred:
        actual = ctx.measurement_accuracy.get(step_id)
        if actual is None:
            return False
        return accuracy_at_least(actual, pred["accuracy_class_at_least"])

    value = _resolve_path(result.data, pred["field"]) if "field" in pred else result.status

    if "exists" in pred:
        if (value is not _MISSING) != pred["exists"]:
            return False
    if "eq" in pred and not _json_equals(value, pred["eq"]):
        return False
    if "neq" in pred and _json_equals(value, pred["neq"]):
        return False
    if "in" in pred and not any(_json_equals(value, v) for v in pred["in"]):
        return False
    if "gt" in pred:
        number = _as_number(value)
        if number is None or number <= pred["gt"]:
            return False
    if "lt" in pred:
        number = _as_number(value)
        if number is None or number >= pred["lt"]:
            return False

    if not any(op in pred for op in _OPERATORS):
        # Предикат без операторов означает «шаг выполнен».
        return result.status == "COMPLETED"
    return True


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover - только для отладки
        return "<missing>"


_MISSING = _Missing()


def _resolve_path(root: Any, path: str) -> Any:
    """Точечная нотация внутри payload шага: `parsed.gtin`."""
    current = root
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return _MISSING
        current = current[segment]
    return current


def _json_equals(a: Any, b: Any) -> bool:
    if a is _MISSING:
        return False
    if a == b:
        return True
    # "true" из формы и True из JSON — одно и то же значение для технолога.
    return str(a).lower() == str(b).lower()


def _as_number(value: Any) -> float | None:
    if value is _MISSING or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Исполнение протокола
# ─────────────────────────────────────────────────────────────────────────────

def visible_steps(protocol: dict[str, Any], ctx: SessionContext) -> list[dict[str, Any]]:
    return [s for s in protocol["steps"] if evaluate(s.get("visible_if"), ctx)]


def is_complete(protocol: dict[str, Any], ctx: SessionContext) -> bool:
    required = [s for s in visible_steps(protocol, ctx) if s.get("required", True)]
    return all(
        (r := ctx.results.get(s["id"])) is not None and r.status == "COMPLETED"
        for s in required
    )


def auto_accept(protocol: dict[str, Any], ctx: SessionContext) -> bool:
    """Решение об автоприёме. Считается сервером и повторяется клиентом."""
    acceptance = protocol.get("acceptance")
    if not acceptance:
        return False
    if evaluate(acceptance.get("reject_if"), ctx) and acceptance.get("reject_if") is not None:
        return False
    auto = acceptance.get("auto_accept_if")
    return evaluate(auto, ctx) if auto is not None else False
