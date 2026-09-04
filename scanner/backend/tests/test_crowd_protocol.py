"""Протокол kv_crowd: схема + поведение приёмки краудскана (docs/15 §2).

Порог качества в acceptance обязан совпадать с min_quality_score дефолтного
тарифа (crowd.py): скан, прошедший протокол, не должен отсекаться движком
вознаграждений по качеству — и наоборот.
"""

import json
import pathlib

import jsonschema

from app.crowd import RewardPolicy
from app.protocol import SessionContext, StepResult, auto_accept, check_compatibility, visible_steps

SCHEMAS = pathlib.Path(__file__).resolve().parents[2] / "docs" / "schemas"
DOC = json.loads((SCHEMAS / "examples" / "crowd" / "kv_crowd.json").read_text(encoding="utf-8"))


def _done(step_id: str, data: dict | None = None, score: float = 1.0) -> StepResult:
    return StepResult(step_id=step_id, status="COMPLETED", data=data or {}, quality_score=score)


class TestSchema:
    def test_valid_and_executable(self):
        schema = json.loads((SCHEMAS / "capture-protocol.schema.json").read_text(encoding="utf-8"))
        jsonschema.validate(DOC, schema)
        assert check_compatibility(DOC) == {"ok": True}

    def test_quality_threshold_matches_reward_policy(self):
        """Единый порог: acceptance протокола и тариф движка не должны спорить."""
        cond = DOC["acceptance"]["auto_accept_if"]["all"]
        threshold = next(c["quality_score_at_least"] for c in cond
                         if "quality_score_at_least" in c)
        assert threshold == RewardPolicy().min_quality_score


class TestBehavior:
    def test_evidence_photo_appears_only_for_documents(self):
        """Фото документа запрашивается только когда источник — инвойс/упаковка."""
        by_words = SessionContext(results={
            "c_supplier": _done("c_supplier", {"evidence": "со слов"})})
        assert "c_evidence_photo" not in [s["id"] for s in visible_steps(DOC, by_words)]
        invoice = SessionContext(results={
            "c_supplier": _done("c_supplier", {"evidence": "инвойс/документы"})})
        assert "c_evidence_photo" in [s["id"] for s in visible_steps(DOC, invoice)]

    def test_happy_path_auto_accepts(self):
        ctx = SessionContext(results={
            "c_mark": _done("c_mark", score=0.8),
            "c_supplier": _done("c_supplier", {
                "supplier_name": "Ningbo Seals", "evidence": "шильдик"}, score=0.8),
        })
        assert auto_accept(DOC, ctx) is True

    def test_low_quality_or_missing_supplier_blocks_auto_accept(self):
        low = SessionContext(results={
            "c_mark": _done("c_mark", score=0.4),
            "c_supplier": _done("c_supplier", {"supplier_name": "X"}, score=0.4),
        })
        assert auto_accept(DOC, low) is False
        no_supplier = SessionContext(results={"c_mark": _done("c_mark", score=0.9)})
        assert auto_accept(DOC, no_supplier) is False
