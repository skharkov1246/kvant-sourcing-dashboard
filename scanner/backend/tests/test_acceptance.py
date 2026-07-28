"""Вертикаль acceptance-extractor: правила > LLM, деградация вместо блокировки.

Смысл набора: контур приёмки с моделью обязан быть НЕ ХУЖЕ контура без неё.
Каждый тест — инвариант сведения (LLM только вето/флаг) или режим отказа
(таймаут, refusal, мусор), при котором приёмка продолжает работать.
"""

import json

import pytest

from app.acceptance import (
    SYSTEM_PROMPT,
    VERDICT_SCHEMA,
    AcceptancePipeline,
    TransportResult,
    TransportUnavailable,
)
from app.model_registry import TenantDataPolicy
from app.protocol import SessionContext, StepResult

STANDARD = TenantDataPolicy(tenant_id="t-standard", retention_days=30)

PROTOCOL = {
    "schema_version": 1,
    "code": "pallet_general",
    "title": "Паллета: общий протокол",
    "steps": [
        {"id": "overview", "kind": "photo", "title": "Общий вид"},
        {"id": "label", "kind": "photo", "title": "Этикетка"},
    ],
    "acceptance": {
        "auto_accept_if": {"all": [{"step": "overview"}, {"step": "label"},
                                   {"quality_score_at_least": 0.7}]},
        "reject_if": {"step": "label", "field": "unreadable", "eq": True},
    },
}


def _ctx(quality=0.9, label_data=None):
    return SessionContext(results={
        "overview": StepResult("overview", "COMPLETED", quality_score=quality),
        "label": StepResult("label", "COMPLETED", data=label_data or {},
                            quality_score=quality),
    })


def _ok_verdict(**kw):
    base = dict(session_ok=True, missing_items=[], quality_flags=[], confidence=0.9)
    base.update(kw)
    return json.dumps(base)


class FakeTransport:
    """Скриптованный транспорт: очередь ответов + журнал запросов."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def complete(self, *, params, system, user_content, schema):
        self.calls.append({"params": params, "system": system,
                           "user_content": user_content, "schema": schema})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _pipeline(*outcomes):
    transport = FakeTransport(*outcomes)
    return AcceptancePipeline(transport), transport


def _result(text, stop_reason="end_turn", model="claude-sonnet-5"):
    return TransportResult(stop_reason=stop_reason, text=text, model=model)


class TestRulesOverLlm:
    def test_llm_cannot_accept_what_rules_rejected(self):
        """Регламент терминален: даже идеальный вердикт не спасает reject_if —
        транспорт вообще не вызывается, нечего спрашивать."""
        pipeline, transport = _pipeline(_result(_ok_verdict()))
        decision = pipeline.decide(PROTOCOL, _ctx(label_data={"unreadable": True}), STANDARD)
        assert decision.outcome == "REJECTED"
        assert transport.calls == []

    def test_happy_path_auto_accepts(self):
        pipeline, _ = _pipeline(_result(_ok_verdict()))
        decision = pipeline.decide(PROTOCOL, _ctx(), STANDARD)
        assert decision.outcome == "AUTO_ACCEPTED"
        assert decision.model_used == "claude-sonnet-5"

    def test_llm_veto_downgrades_to_manual_review(self):
        """Вето извлекателя: правила приняли бы, но модель видит несогласованность."""
        pipeline, _ = _pipeline(_result(_ok_verdict(
            session_ok=False, missing_items=["вес не соответствует габаритам"])))
        decision = pipeline.decide(PROTOCOL, _ctx(), STANDARD)
        assert decision.outcome == "MANUAL_REVIEW"
        assert any("вес не соответствует" in r for r in decision.reasons)

    def test_quality_flag_alone_blocks_auto_accept(self):
        pipeline, _ = _pipeline(_result(_ok_verdict(
            quality_flags=["фото этикетки снято с экрана"])))
        decision = pipeline.decide(PROTOCOL, _ctx(), STANDARD)
        assert decision.outcome == "MANUAL_REVIEW"
        assert "флаг: фото этикетки снято с экрана" in decision.reasons

    def test_rules_deny_plus_llm_ok_is_still_manual(self):
        """LLM не вправе и принять то, что правила не приняли (низкое качество)."""
        pipeline, _ = _pipeline(_result(_ok_verdict()))
        decision = pipeline.decide(PROTOCOL, _ctx(quality=0.4), STANDARD)
        assert decision.outcome == "MANUAL_REVIEW"
        assert "правила протокола не дали автоприём" in decision.reasons


class TestDegradation:
    def test_timeout_degrades_to_manual_review_not_exception(self):
        """SLA: просрочка LLM-плеча не блокирует приёмку и не роняет процесс."""
        pipeline, _ = _pipeline(TransportUnavailable("timeout after 10s"))
        decision = pipeline.decide(PROTOCOL, _ctx(), STANDARD)
        assert decision.outcome == "MANUAL_REVIEW"
        assert any(r.startswith("llm_unavailable") for r in decision.reasons)

    def test_refusal_stop_reason_degrades_gracefully(self):
        """HTTP 200 + stop_reason=refusal: контент не читается, сессия — людям."""
        pipeline, _ = _pipeline(_result("", stop_reason="refusal"))
        decision = pipeline.decide(PROTOCOL, _ctx(), STANDARD)
        assert decision.outcome == "MANUAL_REVIEW"
        assert any(r.startswith("llm_refusal") for r in decision.reasons)

    def test_malformed_verdict_is_retried_once_then_manual(self):
        pipeline, transport = _pipeline(
            _result("не json"),
            _result(json.dumps({"session_ok": True})),  # неполная схема
        )
        decision = pipeline.decide(PROTOCOL, _ctx(), STANDARD)
        assert decision.outcome == "MANUAL_REVIEW"
        assert len(transport.calls) == 2  # ровно один повтор, не цикл
        assert any(r.startswith("llm_malformed") for r in decision.reasons)

    def test_retry_can_recover_from_one_bad_response(self):
        pipeline, transport = _pipeline(_result("мусор"), _result(_ok_verdict()))
        decision = pipeline.decide(PROTOCOL, _ctx(), STANDARD)
        assert decision.outcome == "AUTO_ACCEPTED"
        assert len(transport.calls) == 2

    def test_confidence_out_of_range_is_malformed(self):
        """Numeric-констрейнты в structured outputs не поддерживаются —
        границы confidence обязан проверять код."""
        pipeline, _ = _pipeline(
            _result(_ok_verdict(confidence=1.7)),
            _result(_ok_verdict(confidence=42)),
        )
        decision = pipeline.decide(PROTOCOL, _ctx(), STANDARD)
        assert decision.outcome == "MANUAL_REVIEW"


class TestRegistryWiring:
    def test_request_uses_registry_binding_for_sync_role(self):
        """Модель и параметры — из реестра ролей: синхронная роль получает
        sonnet-5 (никогда fable — минутные ходы ломают SLA), thinking если
        задан — только adaptive (единственный on-режим sonnet-5)."""
        pipeline, transport = _pipeline(_result(_ok_verdict()))
        pipeline.decide(PROTOCOL, _ctx(), STANDARD)
        params = transport.calls[0]["params"]
        assert params["model"] == "claude-sonnet-5"
        assert params.get("thinking") in (None, {"type": "adaptive"})
        assert params["output_config"]["effort"] in ("low", "medium")

    def test_schema_is_strict(self):
        """additionalProperties: false + полный required — гарантия парсинга."""
        assert VERDICT_SCHEMA["additionalProperties"] is False
        assert set(VERDICT_SCHEMA["required"]) == set(VERDICT_SCHEMA["properties"])

    def test_system_prompt_and_payload_are_deterministic(self):
        """Стабильные байты — предпосылка кэширования префикса и сверяемости логов."""
        p1, t1 = _pipeline(_result(_ok_verdict()))
        p2, t2 = _pipeline(_result(_ok_verdict()))
        p1.decide(PROTOCOL, _ctx(), STANDARD)
        p2.decide(PROTOCOL, _ctx(), STANDARD)
        assert t1.calls[0]["system"] == SYSTEM_PROMPT == t2.calls[0]["system"]
        assert t1.calls[0]["user_content"] == t2.calls[0]["user_content"]
