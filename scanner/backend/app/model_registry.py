"""Реестр ролей моделей — исполняемая часть §12.3.

Принцип: в коде и промптах нет ни одной строки вида «claude-opus-5».
Есть роли (acceptance-extractor, dispute-agent, …) и привязки роль → модель.
Откат или ввод новой модели — операция над данными, а не деплой.

Зачем модуль существует отдельно от таблицы в БД: ограничения моделей —
это знание, которое должно проверяться, а не помниться. Три класса ошибок,
которые он снимает:

  * параметрические 400-е: `effort` не поддерживается на haiku-4-5;
    `thinking` нельзя передавать на fable-5 (всегда включён, явное значение
    отвергается); на opus-5 `thinking: disabled` совместим только с
    effort ≤ high;
  * политика данных: fable-5 требует 30-дневного хранения у Anthropic и
    недоступен тенантам с режимом zero-data-retention — привязка обязана
    откатиться на совместимую модель, а не упасть в рантайме;
  * SLA: модель с минутными ходами (fable-5 на высоком effort) не должна
    оказаться в синхронном приёмочном контуре с p95 < 12 с — это ловится
    на валидации привязки, а не на проде.

Факты о моделях продублированы из документации Anthropic и подтверждаются
живым деревом capabilities из GET /v1/models (см. validate_against_capabilities):
статические факты — то, чего в capabilities нет (retention, латентность),
динамические — то, что есть (effort, vision, structured outputs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


# ─────────────────────────────────────────────────────────────────────────────
# Факты о моделях, которых нет в Models API
# ─────────────────────────────────────────────────────────────────────────────

class ThinkingParam(Enum):
    """Как модель принимает параметр `thinking`."""

    ADAPTIVE_DEFAULT = "adaptive_default"   # включён по умолчанию, disabled ограничен
    ALWAYS_ON = "always_on"                 # параметр НЕ передавать: явное значение → 400
    OPTIONAL = "optional"                   # обычные правила


class LatencyClass(Enum):
    """Грубая классификация под SLA слоёв из §12.2."""

    INTERACTIVE = "interactive"   # годится в синхронный контур L1 (p95 < 12 с)
    MINUTES = "minutes"           # ходы могут занимать минуты — только L2/L3


@dataclass(frozen=True)
class ModelFacts:
    model_id: str
    supports_effort: bool
    thinking: ThinkingParam
    latency: LatencyClass
    min_retention_days: int = 0
    """> 0 — модель недоступна тенантам с более коротким хранением (ZDR = 0)."""

    refusal_fallback: str | None = None
    """Рекомендованная модель для server-side fallback при отказе классификаторов."""


MODEL_FACTS: dict[str, ModelFacts] = {
    "claude-fable-5": ModelFacts(
        model_id="claude-fable-5",
        supports_effort=True,
        thinking=ThinkingParam.ALWAYS_ON,
        latency=LatencyClass.MINUTES,
        min_retention_days=30,
        refusal_fallback="claude-opus-4-8",
    ),
    "claude-opus-5": ModelFacts(
        model_id="claude-opus-5",
        supports_effort=True,
        thinking=ThinkingParam.ADAPTIVE_DEFAULT,
        latency=LatencyClass.INTERACTIVE,
        refusal_fallback="claude-opus-4-8",
    ),
    "claude-sonnet-5": ModelFacts(
        model_id="claude-sonnet-5",
        supports_effort=True,
        thinking=ThinkingParam.ADAPTIVE_DEFAULT,
        latency=LatencyClass.INTERACTIVE,
    ),
    "claude-haiku-4-5": ModelFacts(
        model_id="claude-haiku-4-5",
        supports_effort=False,
        thinking=ThinkingParam.OPTIONAL,
        latency=LatencyClass.INTERACTIVE,
    ),
    "claude-opus-4-8": ModelFacts(
        model_id="claude-opus-4-8",
        supports_effort=True,
        thinking=ThinkingParam.OPTIONAL,
        latency=LatencyClass.INTERACTIVE,
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# Роли и привязки
# ─────────────────────────────────────────────────────────────────────────────

Role = Literal[
    "acceptance-extractor",   # L1: разбор шильдика, вердикт в окне приёмки
    "escalation-judge",       # L1: второй эшелон по детерминированному триггеру
    "session-auditor",        # L2: сплошной судья качества, batch
    "defect-classifier",      # L2: классификация повреждений
    "doc-writer",             # L3: проект претензии
    "dispute-agent",          # L3: агентский арбитраж спорной приёмки
    "protocol-assistant",     # web-консоль: ассистент технолога при сборке протоколов
]

# Роли синхронного контура: сюда нельзя ставить модель класса MINUTES.
_INTERACTIVE_ROLES: frozenset[str] = frozenset({"acceptance-extractor", "escalation-judge"})

# Роли, которым нужно зрение.
_VISION_ROLES: frozenset[str] = frozenset({
    "acceptance-extractor", "escalation-judge", "session-auditor",
    "defect-classifier", "dispute-agent",
})


@dataclass(frozen=True)
class RoleBinding:
    role: Role
    model_id: str
    effort: str | None = None
    prompt_version: str = "v1"
    schema_version: str = "v1"
    status: Literal["active", "canary", "retired"] = "active"

    def request_params(self) -> dict[str, Any]:
        """Параметры вызова, безопасные для этой модели.

        Здесь материализуются правила «не передавать thinking на fable»,
        «не передавать effort на haiku» — чтобы 400-е ловились в одном месте,
        а не размазывались по вызывающему коду.
        """
        facts = MODEL_FACTS[self.model_id]
        params: dict[str, Any] = {"model": facts.model_id}
        if self.effort is not None and facts.supports_effort:
            params["output_config"] = {"effort": self.effort}
        if facts.thinking is ThinkingParam.ADAPTIVE_DEFAULT:
            params["thinking"] = {"type": "adaptive"}
        # ALWAYS_ON: параметр thinking сознательно отсутствует.
        if facts.refusal_fallback:
            params["fallbacks"] = [{"model": facts.refusal_fallback}]
        return params


class BindingError(ValueError):
    """Привязка нарушает известные ограничения модели."""


def validate_binding(binding: RoleBinding) -> None:
    """Статическая проверка привязки — до всякого сетевого вызова."""
    facts = MODEL_FACTS.get(binding.model_id)
    if facts is None:
        raise BindingError(f"Модель {binding.model_id!r} не описана в MODEL_FACTS — "
                           f"добавьте факты до использования")

    if binding.effort is not None and not facts.supports_effort:
        raise BindingError(
            f"{binding.model_id} не поддерживает output_config.effort — "
            f"роль {binding.role!r} не должна задавать effort"
        )

    if binding.role in _INTERACTIVE_ROLES and facts.latency is LatencyClass.MINUTES:
        raise BindingError(
            f"{binding.model_id} допускает минутные ходы и не годится для "
            f"синхронной роли {binding.role!r} (SLA p95 < 12 с). "
            f"Место этой модели — dispute-agent / doc-writer / protocol-assistant."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Разрешение привязки с учётом политики данных тенанта
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TenantDataPolicy:
    tenant_id: str
    retention_days: int
    """0 — zero data retention; 30+ — стандартное хранение."""


@dataclass(frozen=True)
class Resolution:
    binding: RoleBinding
    downgraded_from: str | None = None
    reason: str | None = None


class Registry:
    """Привязки ролей + запасные варианты.

    Прод-реализация читает таблицу model_binding из PostgreSQL; здесь —
    та же логика на структурах в памяти, чтобы её можно было тестировать.
    """

    def __init__(self) -> None:
        self._bindings: dict[str, RoleBinding] = {}
        self._fallbacks: dict[str, RoleBinding] = {}

    def bind(self, binding: RoleBinding, fallback: RoleBinding | None = None) -> None:
        validate_binding(binding)
        if fallback is not None:
            validate_binding(fallback)
            fb_facts = MODEL_FACTS[fallback.model_id]
            if fb_facts.min_retention_days > 0:
                raise BindingError(
                    f"Запасная модель {fallback.model_id} сама требует "
                    f"{fb_facts.min_retention_days}-дневного хранения — запасной "
                    f"вариант обязан быть доступен любому тенанту"
                )
        self._bindings[binding.role] = binding
        if fallback is not None:
            self._fallbacks[binding.role] = fallback

    def resolve(self, role: Role, tenant: TenantDataPolicy) -> Resolution:
        """Выбор модели для роли с учётом политики данных тенанта.

        Ключевой случай: fable-5 требует 30-дневного хранения у Anthropic.
        Тенант с ZDR не может обслуживаться этой моделью — привязка обязана
        откатиться на запасную, и это решение фиксируется в Resolution,
        чтобы попасть в лог вызова (заказчик вправе спросить, почему его
        спор разбирала другая модель).
        """
        binding = self._bindings.get(role)
        if binding is None:
            raise BindingError(f"Роль {role!r} не привязана")

        facts = MODEL_FACTS[binding.model_id]
        if facts.min_retention_days > tenant.retention_days:
            fallback = self._fallbacks.get(role)
            if fallback is None:
                raise BindingError(
                    f"{binding.model_id} требует хранения {facts.min_retention_days} дн., "
                    f"у тенанта {tenant.tenant_id} — {tenant.retention_days} дн., "
                    f"а запасной модели для роли {role!r} нет"
                )
            return Resolution(
                binding=fallback,
                downgraded_from=binding.model_id,
                reason=(
                    f"политика данных тенанта ({tenant.retention_days} дн.) "
                    f"короче требуемой моделью ({facts.min_retention_days} дн.)"
                ),
            )
        return Resolution(binding=binding)


# ─────────────────────────────────────────────────────────────────────────────
# Сверка с живым деревом capabilities из GET /v1/models
# ─────────────────────────────────────────────────────────────────────────────

def validate_against_capabilities(binding: RoleBinding, model_info: dict[str, Any]) -> list[str]:
    """Проверка привязки против ответа Models API.

    Возвращает список проблем; пустой список — привязка совместима.
    Проверяются пути дерева capabilities программно (§12.3), а не по памяти:
    именно так реестр узнаёт, что новая модель не умеет нужного, ДО канарейки.
    """
    problems: list[str] = []
    caps = model_info.get("capabilities") or {}

    def supported(*path: str) -> bool:
        node: Any = caps
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return False
            node = node[key]
        return bool(isinstance(node, dict) and node.get("supported"))

    if binding.role in _VISION_ROLES and not supported("image_input"):
        problems.append(f"{binding.model_id}: нет image_input, а роль {binding.role!r} требует зрение")

    if not supported("structured_outputs"):
        problems.append(f"{binding.model_id}: нет structured_outputs — строгая json_schema обязательна для всех ролей")

    if binding.effort is not None and not supported("effort", binding.effort):
        problems.append(
            f"{binding.model_id}: effort {binding.effort!r} не поддерживается по дереву capabilities"
        )

    return problems


# ─────────────────────────────────────────────────────────────────────────────
# Привязки по умолчанию (§12.3 + докупка Fable)
# ─────────────────────────────────────────────────────────────────────────────

def default_registry() -> Registry:
    """Состав из §12.3 с Fable 5 в глубоких ролях.

    Fable добавлен туда, где его сила (длинные автономные ходы, самый глубокий
    разбор) совпадает с профилем задачи, а латентность и цена не мешают:
      * dispute-agent — арбитраж спорной приёмки, часы-сутки, ~100 кейсов/мес;
      * doc-writer — претензии: там ошибка дороже токенов;
      * protocol-assistant — ассистент технолога, редкие длинные сессии.
    Синхронный контур остаётся на sonnet/opus: минутные ходы Fable ломают
    SLA p95 < 12 с, и validate_binding это запрещает жёстко.
    """
    r = Registry()
    r.bind(RoleBinding("acceptance-extractor", "claude-sonnet-5", effort="medium"))
    r.bind(RoleBinding("escalation-judge", "claude-opus-5", effort="high"))
    r.bind(RoleBinding("session-auditor", "claude-haiku-4-5"))
    r.bind(RoleBinding("defect-classifier", "claude-sonnet-5", effort="medium"))
    r.bind(
        RoleBinding("doc-writer", "claude-fable-5", effort="high"),
        fallback=RoleBinding("doc-writer", "claude-opus-5", effort="high"),
    )
    r.bind(
        RoleBinding("dispute-agent", "claude-fable-5", effort="xhigh"),
        fallback=RoleBinding("dispute-agent", "claude-opus-5", effort="xhigh"),
    )
    r.bind(
        RoleBinding("protocol-assistant", "claude-fable-5", effort="high"),
        fallback=RoleBinding("protocol-assistant", "claude-opus-5", effort="high"),
    )
    return r
