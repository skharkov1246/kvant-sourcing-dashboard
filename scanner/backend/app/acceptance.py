"""Вертикаль приёмки: acceptance-extractor — первый живой контур Claude (§12, L1).

Устройство и границы ответственности:

  * Детерминированная половина решает ПЕРВОЙ: reject_if/auto_accept_if протокола
    (protocol.py) — это регламент, и модель не вправе его перекрыть. LLM-вердикт
    может только НАЛОЖИТЬ ВЕТО на автоприём и добавить флаги — принять сессию,
    которую отклонили правила, он не может никогда.
  * Модель и параметры берутся ТОЛЬКО из реестра ролей (model_registry.py):
    роль acceptance-extractor синхронная, SLA p95 < 12 с — реестр не выдаст сюда
    модель с минутными ходами (fable запрещён на уровне валидации привязки).
  * Деградация вместо блокировки: таймаут, отказ классификатора или мусор в
    ответе НЕ останавливают приёмку — сессия уходит на ручную проверку с
    причиной. Кладовщик никогда не ждёт LLM.
  * Транспорт — интерфейс: боевой AnthropicTransport и фейковый в тестах
    неразличимы для решателя, поэтому вся логика решения покрыта тестами
    без сети и ключа.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from .model_registry import Registry, TenantDataPolicy, default_registry
from .protocol import SessionContext, auto_accept, evaluate

# ─────────────────────────────────────────────────────────────────────────────
# Контракт вердикта: строгая json_schema (additionalProperties: false + required)
# ─────────────────────────────────────────────────────────────────────────────

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "session_ok": {
            "type": "boolean",
            "description": "Данные сессии согласованы и достаточны для приёма",
        },
        "missing_items": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Чего не хватает по протоколу и здравому смыслу",
        },
        "quality_flags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Замеченные проблемы качества: противоречия, подозрения",
        },
        "confidence": {
            "type": "number",
            "description": "Уверенность вердикта, 0..1 (границы проверяет код: "
                           "numeric-констрейнты в structured outputs не поддерживаются)",
        },
    },
    "required": ["session_ok", "missing_items", "quality_flags", "confidence"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "Ты — контролёр приёмки данных склада. Тебе дают свёртку сессии съёмки "
    "(результаты шагов протокола) и краткое описание протокола. Твоя задача — "
    "проверить СОГЛАСОВАННОСТЬ данных: противоречия между полями, подозрительные "
    "значения, пропуски, которые формальные правила не ловят. Ты НЕ решаешь "
    "приём — решают правила протокола; ты можешь только поднять флаг. "
    "Отвечай строго по схеме."
)


@dataclass
class ExtractorVerdict:
    session_ok: bool
    missing_items: list[str]
    quality_flags: list[str]
    confidence: float

    @classmethod
    def parse(cls, raw: str) -> "ExtractorVerdict":
        data = json.loads(raw)
        confidence = float(data["confidence"])
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence вне [0,1]: {confidence}")
        return cls(
            session_ok=bool(data["session_ok"]),
            missing_items=list(data["missing_items"]),
            quality_flags=list(data["quality_flags"]),
            confidence=confidence,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Транспорт
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TransportResult:
    stop_reason: str          # end_turn | refusal | max_tokens | ...
    text: str                 # первый text-блок (валидный JSON при json_schema)
    model: str                # кто фактически ответил


class TransportUnavailable(Exception):
    """Сеть/таймаут/5xx: модель недоступна — деградируем, а не падаем."""


class LlmTransport(Protocol):
    def complete(self, *, params: dict[str, Any], system: str,
                 user_content: str, schema: dict[str, Any]) -> TransportResult: ...


class AnthropicTransport:
    """Боевой транспорт поверх официального SDK.

    Таймаут — бюджет SLA, не дефолт SDK: приёмочный контур обязан ответить
    за секунды, при просрочке решатель отправит сессию на ручную проверку.
    """

    def __init__(self, timeout_s: float = 10.0) -> None:
        import anthropic  # ленивый импорт: тестам без сети SDK не нужен

        self._anthropic = anthropic
        self._client = anthropic.Anthropic()
        self._timeout_s = timeout_s

    def complete(self, *, params: dict[str, Any], system: str,
                 user_content: str, schema: dict[str, Any]) -> TransportResult:
        try:
            response = self._client.with_options(
                timeout=self._timeout_s, max_retries=0
            ).messages.create(
                max_tokens=1024,
                system=[{
                    "type": "text",
                    "text": system,
                    # системный промпт стабилен — кэшируем префикс
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user_content}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
                **params,
            )
        except (self._anthropic.APITimeoutError,
                self._anthropic.APIConnectionError,
                self._anthropic.RateLimitError) as exc:
            raise TransportUnavailable(str(exc)) from exc
        except self._anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise TransportUnavailable(str(exc)) from exc
            raise  # 4xx — ошибка конфигурации, её надо видеть, а не глотать

        text = next((b.text for b in response.content if b.type == "text"), "")
        return TransportResult(
            stop_reason=response.stop_reason or "end_turn",
            text=text,
            model=response.model,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Решение о приёме
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AcceptanceDecision:
    outcome: str                    # AUTO_ACCEPTED | REJECTED | MANUAL_REVIEW
    reasons: list[str] = field(default_factory=list)
    verdict: ExtractorVerdict | None = None
    model_used: str | None = None
    downgraded_from: str | None = None


class AcceptancePipeline:
    def __init__(self, transport: LlmTransport,
                 registry: Registry | None = None) -> None:
        self._transport = transport
        self._registry = registry or default_registry()

    def decide(self, protocol: dict[str, Any], ctx: SessionContext,
               tenant: TenantDataPolicy) -> AcceptanceDecision:
        """Никогда не бросает исключений доступности: любой сбой LLM-плеча —
        это MANUAL_REVIEW с причиной, а не остановка приёмки."""

        # Эшелон 1: регламент. Отклонение правилами — терминально.
        acceptance = protocol.get("acceptance") or {}
        reject_cond = acceptance.get("reject_if")
        if reject_cond is not None and evaluate(reject_cond, ctx):
            return AcceptanceDecision(
                outcome="REJECTED",
                reasons=["reject_if протокола: условие регламента нарушено"],
            )
        rules_accept = auto_accept(protocol, ctx)

        # Эшелон 2: извлечение вердикта моделью из реестра ролей.
        resolution = self._registry.resolve("acceptance-extractor", tenant)
        params = resolution.binding.request_params()
        user_content = _render_session(protocol, ctx)

        verdict: ExtractorVerdict | None = None
        llm_reason: str | None = None
        model_used: str | None = None
        for attempt in (1, 2):  # один повтор на мусорный ответ, не больше
            try:
                result = self._transport.complete(
                    params=params, system=SYSTEM_PROMPT,
                    user_content=user_content, schema=VERDICT_SCHEMA,
                )
            except TransportUnavailable as exc:
                llm_reason = f"llm_unavailable: {exc}"
                break
            model_used = result.model
            if result.stop_reason == "refusal":
                # Отказ классификатора на складских данных — редкость, но не
                # блокер: сессию проверит человек.
                llm_reason = "llm_refusal: классификатор отклонил запрос"
                break
            try:
                verdict = ExtractorVerdict.parse(result.text)
                break
            except (ValueError, KeyError, json.JSONDecodeError):
                llm_reason = "llm_malformed: вердикт не соответствует схеме"
                continue

        # Эшелон 3: сведение. LLM может только вето/флаг.
        if verdict is None:
            return AcceptanceDecision(
                outcome="MANUAL_REVIEW",
                reasons=[llm_reason or "llm_unavailable"],
                model_used=model_used,
                downgraded_from=resolution.downgraded_from,
            )
        if rules_accept and verdict.session_ok and not verdict.quality_flags:
            return AcceptanceDecision(
                outcome="AUTO_ACCEPTED",
                verdict=verdict,
                model_used=model_used,
                downgraded_from=resolution.downgraded_from,
            )
        reasons = []
        if not rules_accept:
            reasons.append("правила протокола не дали автоприём")
        if not verdict.session_ok:
            reasons.append("вето извлекателя: " + "; ".join(verdict.missing_items or ["без деталей"]))
        reasons.extend(f"флаг: {f}" for f in verdict.quality_flags)
        return AcceptanceDecision(
            outcome="MANUAL_REVIEW",
            reasons=reasons,
            verdict=verdict,
            model_used=model_used,
            downgraded_from=resolution.downgraded_from,
        )


def _render_session(protocol: dict[str, Any], ctx: SessionContext) -> str:
    """Свёртка сессии для модели: детерминированный JSON (стабильность байтов
    важна и для воспроизводимости логов, и для кэширования)."""
    return json.dumps(
        {
            "protocol": {
                "code": protocol.get("code"),
                "title": protocol.get("title"),
                "required_steps": [
                    {"id": s["id"], "kind": s.get("kind"), "title": s.get("title")}
                    for s in protocol.get("steps", [])
                    if s.get("required", True)
                ],
            },
            "session": {
                "quality_score": round(ctx.quality_score, 3),
                "steps": {
                    step_id: {"status": r.status, "data": r.data,
                              "violations": r.violations}
                    for step_id, r in sorted(ctx.results.items())
                },
            },
        },
        ensure_ascii=False,
        sort_keys=True,
    )
