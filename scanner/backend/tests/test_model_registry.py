"""Реестр ролей моделей: ограничения Fable 5 и правила привязки (§12.3).

Смысл набора: знания об ограничениях моделей должны проверяться, а не
помниться. Каждый тест соответствует классу ошибки, которая иначе всплыла бы
в проде как 400-я, как нарушение политики данных или как сорванный SLA.
"""

import pytest

from app.model_registry import (
    BindingError,
    Registry,
    Resolution,
    RoleBinding,
    TenantDataPolicy,
    default_registry,
    validate_against_capabilities,
    validate_binding,
)

STANDARD = TenantDataPolicy(tenant_id="t-standard", retention_days=30)
ZDR = TenantDataPolicy(tenant_id="t-zdr", retention_days=0)


class TestParameterSafety:
    def test_fable_never_receives_thinking_param(self):
        """У fable-5 thinking всегда включён; явное значение параметра — 400."""
        params = RoleBinding("dispute-agent", "claude-fable-5", effort="xhigh").request_params()
        assert "thinking" not in params
        assert params["output_config"] == {"effort": "xhigh"}

    def test_opus_gets_explicit_adaptive_thinking(self):
        params = RoleBinding("escalation-judge", "claude-opus-5", effort="high").request_params()
        assert params["thinking"] == {"type": "adaptive"}

    def test_haiku_never_receives_effort(self):
        """`output_config.effort` не поддерживается на haiku-4-5 — это источник 400-х."""
        with pytest.raises(BindingError, match="effort"):
            validate_binding(RoleBinding("session-auditor", "claude-haiku-4-5", effort="low"))
        # без effort — привязка валидна и параметр не появляется
        params = RoleBinding("session-auditor", "claude-haiku-4-5").request_params()
        assert "output_config" not in params

    def test_fable_carries_refusal_fallback(self):
        """Классификаторы fable могут отказать на benign-контенте (нефтехимия, H2S) —
        server-side fallback обязателен по умолчанию."""
        params = RoleBinding("doc-writer", "claude-fable-5", effort="high").request_params()
        assert params["fallbacks"] == [{"model": "claude-opus-4-8"}]

    def test_unknown_model_is_refused_not_guessed(self):
        with pytest.raises(BindingError, match="не описана"):
            validate_binding(RoleBinding("doc-writer", "claude-nonexistent-9"))


class TestLatencyGate:
    def test_fable_is_banned_from_synchronous_roles(self):
        """Минутные ходы Fable ломают SLA p95 < 12 с приёмочного контура —
        запрет жёсткий, на уровне валидации привязки."""
        for role in ("acceptance-extractor", "escalation-judge"):
            with pytest.raises(BindingError, match="синхронной роли"):
                validate_binding(RoleBinding(role, "claude-fable-5", effort="high"))

    def test_fable_is_allowed_in_deep_roles(self):
        for role in ("dispute-agent", "doc-writer", "protocol-assistant"):
            validate_binding(RoleBinding(role, "claude-fable-5", effort="high"))


class TestDataPolicyResolution:
    def test_standard_tenant_gets_fable(self):
        res = default_registry().resolve("dispute-agent", STANDARD)
        assert res.binding.model_id == "claude-fable-5"
        assert res.downgraded_from is None

    def test_zdr_tenant_is_downgraded_with_recorded_reason(self):
        """fable-5 требует 30-дневного хранения — тенант с ZDR получает запасную
        модель, и причина фиксируется для лога вызова: заказчик вправе спросить,
        почему его спор разбирала другая модель."""
        res = default_registry().resolve("dispute-agent", ZDR)
        assert res.binding.model_id == "claude-opus-5"
        assert res.downgraded_from == "claude-fable-5"
        assert "политика данных" in res.reason

    def test_retention_shorter_than_30_days_also_downgrades(self):
        week = TenantDataPolicy(tenant_id="t-week", retention_days=7)
        res = default_registry().resolve("doc-writer", week)
        assert res.binding.model_id == "claude-opus-5"

    def test_fable_without_fallback_is_rejected_at_bind_time(self):
        """Привязка модели с требованием retention без запасной — ошибка
        конфигурации, а не рантайма."""
        r = Registry()
        r.bind(RoleBinding("dispute-agent", "claude-fable-5", effort="xhigh"))
        with pytest.raises(BindingError, match="запасной модели"):
            r.resolve("dispute-agent", ZDR)

    def test_fallback_must_itself_be_universally_available(self):
        r = Registry()
        with pytest.raises(BindingError, match="запасной вариант"):
            r.bind(
                RoleBinding("doc-writer", "claude-fable-5", effort="high"),
                fallback=RoleBinding("doc-writer", "claude-fable-5", effort="high"),
            )

    def test_unbound_role_fails_loudly(self):
        with pytest.raises(BindingError, match="не привязана"):
            Registry().resolve("doc-writer", STANDARD)


class TestCapabilitiesCheck:
    def _info(self, *, vision=True, structured=True, efforts=("low", "medium", "high", "xhigh", "max")):
        return {
            "id": "claude-x",
            "capabilities": {
                "image_input": {"supported": vision},
                "structured_outputs": {"supported": structured},
                "effort": {e: {"supported": True} for e in efforts} | {"supported": True},
            },
        }

    def test_compatible_model_passes(self):
        b = RoleBinding("dispute-agent", "claude-fable-5", effort="xhigh")
        assert validate_against_capabilities(b, self._info()) == []

    def test_missing_vision_is_caught_for_vision_role(self):
        b = RoleBinding("defect-classifier", "claude-sonnet-5", effort="medium")
        problems = validate_against_capabilities(b, self._info(vision=False))
        assert any("image_input" in p for p in problems)

    def test_missing_effort_level_is_caught(self):
        """Ровно тот случай, ради которого проверка программная: новая модель
        может не поддерживать xhigh — реестр узнаёт это до канарейки."""
        b = RoleBinding("dispute-agent", "claude-fable-5", effort="xhigh")
        problems = validate_against_capabilities(b, self._info(efforts=("low", "medium", "high")))
        assert any("xhigh" in p for p in problems)

    def test_missing_structured_outputs_blocks_everything(self):
        b = RoleBinding("session-auditor", "claude-haiku-4-5")
        problems = validate_against_capabilities(b, self._info(structured=False))
        assert any("structured_outputs" in p for p in problems)


class TestDefaultRegistry:
    def test_all_default_bindings_are_internally_consistent(self):
        r = default_registry()
        for role in ("acceptance-extractor", "escalation-judge", "session-auditor",
                     "defect-classifier", "doc-writer", "dispute-agent", "protocol-assistant"):
            res = r.resolve(role, STANDARD)
            assert isinstance(res, Resolution)

    def test_synchronous_layer_is_interactive_for_every_tenant(self):
        """Ни при какой политике данных синхронный контур не получает
        модель с минутными ходами."""
        r = default_registry()
        for tenant in (STANDARD, ZDR):
            for role in ("acceptance-extractor", "escalation-judge"):
                assert r.resolve(role, tenant).binding.model_id != "claude-fable-5"
