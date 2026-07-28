"""Общий набор случаев интерпретатора протокола.

Те же случаи продублированы в тестах Kotlin-ядра: клиент и сервер обязаны
давать одинаковый результат из одного состояния сессии.
"""

import pytest

from app.protocol import (
    ProtocolError,
    SessionContext,
    StepResult,
    accuracy_at_least,
    auto_accept,
    check_compatibility,
    evaluate,
    is_complete,
    visible_steps,
)


def ctx(**results):
    return SessionContext(results={k: v for k, v in results.items()})


def completed(step_id, data=None, quality=None):
    return StepResult(step_id=step_id, status="COMPLETED", data=data or {}, quality_score=quality)


class TestAccuracyOrder:
    def test_a_is_best(self):
        assert accuracy_at_least("A", "C")
        assert accuracy_at_least("B", "B")
        assert not accuracy_at_least("D", "B")


class TestConditions:
    def test_missing_step_is_false_unless_exists_false(self):
        c = ctx()
        assert evaluate({"step": "supplier", "field": "has_damage", "eq": True}, c) is False
        assert evaluate({"step": "supplier", "exists": False}, c) is True

    def test_eq_on_nested_path(self):
        c = ctx(label=completed("label", {"parsed": {"gtin": "04607034171234"}}))
        assert evaluate({"step": "label", "field": "parsed.gtin", "exists": True}, c)
        assert evaluate({"step": "label", "field": "parsed.serial", "exists": False}, c)

    def test_bool_from_form_matches_json_bool(self):
        # Форма отдаёт строку "true"; технолог пишет в протоколе eq: true.
        c = ctx(supplier=completed("supplier", {"has_damage": "true"}))
        assert evaluate({"step": "supplier", "field": "has_damage", "eq": True}, c)

    def test_logical_operators(self):
        c = ctx(a=completed("a"), b=completed("b"))
        assert evaluate({"all": [{"step": "a"}, {"step": "b"}]}, c)
        assert evaluate({"any": [{"step": "a"}, {"step": "zzz"}]}, c)
        assert evaluate({"not": {"step": "zzz"}}, c)

    def test_numeric_comparison(self):
        c = ctx(weight=completed("weight", {"kg": 320.5}))
        assert evaluate({"step": "weight", "field": "kg", "gt": 100}, c)
        assert not evaluate({"step": "weight", "field": "kg", "lt": 100}, c)

    def test_accuracy_class_predicate_needs_recorded_measurement(self):
        c = SessionContext(results={"dims": completed("dims")}, measurement_accuracy={})
        assert not evaluate({"step": "dims", "accuracy_class_at_least": "B"}, c)
        c.measurement_accuracy["dims"] = "A"
        assert evaluate({"step": "dims", "accuracy_class_at_least": "B"}, c)

    def test_quality_score_ignores_unscored_steps(self):
        c = ctx(a=completed("a", quality=0.9), b=completed("b"))
        assert c.quality_score == pytest.approx(0.95)

    def test_unknown_form_raises(self):
        with pytest.raises(ProtocolError):
            evaluate({"totally_unknown": 1}, ctx())


class TestVisibility:
    def test_damage_steps_hidden_until_flagged(self, protocol):
        c = ctx(supplier=completed("supplier", {"has_damage": False}))
        ids = [s["id"] for s in visible_steps(protocol, c)]
        assert "damage" not in ids and "damage_note" not in ids

        c2 = ctx(supplier=completed("supplier", {"has_damage": True}))
        ids2 = [s["id"] for s in visible_steps(protocol, c2)]
        assert "damage" in ids2 and "damage_note" in ids2


class TestCompatibility:
    def test_newer_schema_version_is_refused(self, protocol):
        protocol["schema_version"] = 99
        result = check_compatibility(protocol)
        assert not result["ok"] and "99" in result["reason"]

    def test_unknown_required_kind_blocks(self, protocol):
        protocol["steps"].append({"id": "hologram", "kind": "hologram_scan", "title": "X", "required": True})
        assert not check_compatibility(protocol)["ok"]

    def test_unknown_optional_kind_is_tolerated(self, protocol):
        protocol["steps"].append({"id": "hologram", "kind": "hologram_scan", "title": "X", "required": False})
        assert check_compatibility(protocol)["ok"]


class TestAcceptance:
    def _good_session(self):
        return SessionContext(
            results={
                "overview": completed("overview", quality=0.9),
                "sides": completed("sides", quality=0.9),
                "dims": completed("dims", quality=0.9),
                "label": completed("label", {"parsed": {"gtin": "0460"}, "source": "camera"}, quality=0.9),
                "label_photo": completed("label_photo", quality=0.9),
                "supplier": completed("supplier", {"has_damage": False}, quality=0.9),
                "done": completed("done", quality=0.9),
            },
            measurement_accuracy={"dims": "B"},
        )

    def test_auto_accept_on_clean_session(self, protocol):
        assert auto_accept(protocol, self._good_session()) is True

    def test_manual_code_entry_blocks_auto_accept(self, protocol):
        c = self._good_session()
        c.results["label"].data["source"] = "manual"
        assert auto_accept(protocol, c) is False

    def test_class_d_measurement_blocks_auto_accept(self, protocol):
        c = self._good_session()
        c.measurement_accuracy["dims"] = "D"
        assert auto_accept(protocol, c) is False

    def test_damage_blocks_auto_accept(self, protocol):
        c = self._good_session()
        c.results["supplier"].data["has_damage"] = True
        assert auto_accept(protocol, c) is False

    def test_incomplete_session_is_not_complete(self, protocol):
        c = SessionContext(results={"overview": completed("overview")})
        assert is_complete(protocol, c) is False
