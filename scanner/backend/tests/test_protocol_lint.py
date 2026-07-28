"""Линтер публикации протоколов: каждое правило — реальный класс тихой потери.

Смысл набора: технолог правит протокол без релиза, значит опечатка не падает
компиляцией — её обязан поймать линтер ДО публикации. Плюс sweep: все
протоколы репозитория обязаны проходить без ошибок (предупреждения — можно).
"""

import json
import pathlib

import pytest

from app.protocol_lint import lint

EXAMPLES = pathlib.Path(__file__).resolve().parents[2] / "docs" / "schemas" / "examples"
ALL_PROTOCOLS = sorted(EXAMPLES.rglob("*.json"))


def _base(steps, acceptance=None):
    doc = {
        "schema_version": 1, "code": "lint_test", "version": 1,
        "status": "draft", "scope": "global", "title": "Тест",
        "settings": {"estimated_duration_s": 60},
        "steps": steps,
    }
    if acceptance:
        doc["acceptance"] = acceptance
    return doc


def _form_step(sid, fields, **kw):
    return {"id": sid, "kind": "form", "title": sid,
            "form": {"fields": fields}, **kw}


class TestRules:
    def test_duplicate_step_ids(self):
        report = lint(_base([
            {"id": "a", "kind": "photo", "title": "a"},
            {"id": "a", "kind": "photo", "title": "a2"},
        ]))
        assert any(e.startswith("E1") for e in report.errors)

    def test_predicate_on_repeat_step_field(self):
        """Дефект g_shore: payload repeat-шага — список, предикат всегда ложен."""
        report = lint(_base([
            _form_step("meas", [{"key": "v", "type": "number"}],
                       repeat={"min": 3, "max": 6}),
            {"id": "dep", "kind": "photo", "title": "dep",
             "visible_if": {"step": "meas", "field": "v", "gt": 5}},
        ]))
        assert any(e.startswith("E2") for e in report.errors)

    def test_reference_to_unknown_step(self):
        report = lint(_base([
            {"id": "a", "kind": "photo", "title": "a",
             "visible_if": {"step": "ghost", "field": "x", "eq": 1}},
        ]))
        assert any(e.startswith("E3") for e in report.errors)

    def test_forward_reference_is_dead_branch(self):
        """Ссылка вперёд: шаг ещё не выполнен в линейном проходе — ветка мертва."""
        report = lint(_base([
            {"id": "early", "kind": "photo", "title": "early",
             "visible_if": {"step": "late", "field": "x", "eq": 1}},
            _form_step("late", [{"key": "x", "type": "number"}]),
        ]))
        assert any(e.startswith("E4") for e in report.errors)

    def test_forward_reference_with_exists_false_is_legal(self):
        report = lint(_base([
            {"id": "early", "kind": "photo", "title": "early",
             "visible_if": {"step": "late", "exists": False}},
            _form_step("late", [{"key": "x", "type": "number"}]),
        ]))
        assert not any(e.startswith("E4") for e in report.errors)

    def test_enum_value_typo_is_dead_branch(self):
        """Опечатка в eq против options — тот самый молчаливый провал."""
        report = lint(_base([
            _form_step("kind_step", [{"key": "t", "type": "enum",
                                      "options": ["кольцо", "манжета"]}]),
            {"id": "dep", "kind": "photo", "title": "dep",
             "visible_if": {"step": "kind_step", "field": "t", "eq": "колцо"}},
        ]))
        assert any(e.startswith("E5") and "колцо" in e for e in report.errors)

    def test_unknown_condition_form(self):
        report = lint(_base([
            {"id": "a", "kind": "photo", "title": "a",
             "visible_if": {"when": "later"}},
        ]))
        assert any(e.startswith("E6") for e in report.errors)

    def test_unknown_predicate_operator(self):
        report = lint(_base([
            _form_step("s", [{"key": "x", "type": "number"}]),
            {"id": "b", "kind": "photo", "title": "b",
             "visible_if": {"step": "s", "field": "x", "gte": 5}},
        ]))
        assert any(e.startswith("E6") and "gte" in e for e in report.errors)

    def test_enum_without_options_or_source(self):
        """«Закрытый перечень» без перечня = свободный ввод через enum (Р-03 п.2)."""
        report = lint(_base([
            _form_step("s", [{"key": "brand", "type": "enum"}]),
        ]))
        assert any(e.startswith("E7") for e in report.errors)

    def test_acceptance_conditions_are_linted_too(self):
        report = lint(_base(
            [_form_step("s", [{"key": "x", "type": "number"}])],
            acceptance={"reject_if": {"step": "ghost", "exists": True}},
        ))
        assert any(e.startswith("E3") for e in report.errors)

    def test_blocking_glare_on_marking_is_warning_not_error(self):
        """W1: выбитые клейма читаются за счёт блика — предупреждение технологу,
        не блокировка публикации (в текущих протоколах порог сознательный)."""
        report = lint(_base([
            {"id": "c_mark", "kind": "photo", "title": "Маркировка",
             "quality": {"glare_ratio": 0.12, "on_violation": "block"}},
        ]))
        assert report.ok
        assert any(w.startswith("W1") for w in report.warnings)

    def test_missing_duration_is_warning(self):
        doc = _base([{"id": "a", "kind": "photo", "title": "a"}])
        doc["settings"] = {}
        report = lint(doc)
        assert report.ok
        assert any(w.startswith("W2") for w in report.warnings)


class TestRepositorySweep:
    @pytest.mark.parametrize("path", ALL_PROTOCOLS, ids=lambda p: p.stem)
    def test_every_repo_protocol_passes_without_errors(self, path):
        """Все опубликованные протоколы репозитория чисты по ошибкам линтера.
        Предупреждения допустимы — они адресованы технологу."""
        report = lint(json.loads(path.read_text(encoding="utf-8")))
        assert report.ok, report.errors

    def test_sweep_actually_found_protocols(self):
        assert len(ALL_PROTOCOLS) >= 4  # router, cat_a, cat_g, crowd, pallet
