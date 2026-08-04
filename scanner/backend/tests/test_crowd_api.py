"""HTTP-контур краудсорсинга (docs/15): скан за вознаграждение.

Смысл набора: платим за ПРИНЯТЫЙ УНИКАЛЬНЫЙ скан — дедуп, качество, капы и
бюджет кампании отсекают мусор ДО начисления; деньги движутся только
строками реестра (клобэк — отдельной строкой, не переписыванием); крипто-
рельса выключена по умолчанию. Логика живёт в crowd.py (там свои юниты) —
здесь проверяется HTTP-обвязка, роли и аудит.

Движок крауда in-memory по построению — матрица хранилищ не нужна.
"""

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.store import Store
from tests.conftest import OPERATOR, REVIEWER

T0 = "2026-08-03T10:00:00+00:00"


@pytest.fixture()
def http(monkeypatch):
    monkeypatch.setattr(main_module, "STORE", Store())
    monkeypatch.setattr(main_module, "_CROWD_ENGINES", {})
    return TestClient(main_module.app)


def _contributor(http, **extra):
    r = http.post("/v1/crowd/contributors", headers=REVIEWER, json={
        "display_name": "Иван", "phone_hash": "ph-1", **extra})
    assert r.status_code == 201
    return r.json()["id"]


def _submit(http, contributor_id, sha, *, at=T0, quality=0.9, **extra):
    return http.post("/v1/crowd/submissions", headers=OPERATOR, json={
        "contributor_id": contributor_id, "content_sha256": sha,
        "submitted_at": at, "quality_score": quality, **extra})


class TestSubmission:
    def test_accepted_unique_scan_accrues_5_eur(self, http):
        cid = _contributor(http)
        r = _submit(http, cid, "sha-1")
        assert r.status_code == 201
        assert r.json()["state"] == "ACCEPTED"

        ledger = http.get(f"/v1/crowd/contributors/{cid}/ledger",
                          headers=REVIEWER).json()
        assert ledger["balance_cents_eur"] == 500
        assert ledger["entries"][0]["kind"] == "ACCRUAL"

    def test_duplicate_sha_rejected_without_accrual(self, http):
        cid = _contributor(http)
        _submit(http, cid, "sha-dup")
        r = _submit(http, cid, "sha-dup")
        assert r.json()["state"] == "REJECTED"
        assert r.json()["reject_reason"] == "duplicate"
        balance = http.get(f"/v1/crowd/contributors/{cid}/ledger",
                           headers=REVIEWER).json()["balance_cents_eur"]
        assert balance == 500  # только первый скан

    def test_low_quality_gate(self, http):
        cid = _contributor(http)
        r = _submit(http, cid, "sha-blur", quality=0.3)
        assert r.json()["state"] == "REJECTED"
        assert r.json()["reject_reason"] == "quality"

    def test_unregistered_contributor_is_409_not_silent_reject(self, http):
        r = _submit(http, "ghost", "sha-x")
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "crowd_rule"

    def test_velocity_quarantine_resolved_by_auditor(self, http):
        """Быстрая серия уходит в карантин без денег; аудитор решает —
        и только его «ок» начисляет вознаграждение."""
        cid = _contributor(http)
        last = None
        for i in range(13):
            last = _submit(http, cid, f"sha-v{i}")
        assert last.json()["state"] == "FLAGGED"

        sid = last.json()["id"]
        resolved = http.post(f"/v1/crowd/submissions/{sid}/resolve",
                             headers=REVIEWER, json={"ok": True})
        assert resolved.json()["state"] == "ACCEPTED"
        balance = http.get(f"/v1/crowd/contributors/{cid}/ledger",
                           headers=REVIEWER).json()["balance_cents_eur"]
        assert balance == 13 * 500

    def test_resolve_non_flagged_conflicts(self, http):
        cid = _contributor(http)
        sid = _submit(http, cid, "sha-ok").json()["id"]
        r = http.post(f"/v1/crowd/submissions/{sid}/resolve",
                      headers=REVIEWER, json={"ok": True})
        assert r.status_code == 409

    def test_daily_cap_stops_payment_until_next_day(self, http):
        """40 оплаченных сканов в день — потолок (daily_cap_scans): 41-й в
        тот же день отклоняется cap_exceeded, назавтра приём открывается
        снова. Сканы размазаны по часам, чтобы не задеть часовой карантин —
        капы независимы и проверяются по отдельности."""
        cid = _contributor(http)
        for i in range(40):
            hour, minute = divmod(i, 4)
            r = _submit(http, cid, f"sha-day-{i}",
                        at=f"2026-08-03T{hour:02d}:{minute * 12:02d}:00+00:00")
            assert r.json()["state"] == "ACCEPTED", r.json()
        over = _submit(http, cid, "sha-day-41", at="2026-08-03T12:00:00+00:00")
        assert over.json()["state"] == "REJECTED"
        assert over.json()["reject_reason"] == "cap_exceeded"

        next_day = _submit(http, cid, "sha-day-42", at="2026-08-04T08:00:00+00:00")
        assert next_day.json()["state"] == "ACCEPTED"
        # Деньги — ровно за 41 принятый (40 вчера + 1 сегодня), не за присланное.
        balance = http.get(f"/v1/crowd/contributors/{cid}/ledger",
                           headers=REVIEWER).json()["balance_cents_eur"]
        assert balance == 41 * 500


class TestCampaigns:
    def test_budget_is_hard_ceiling(self, http):
        cid = _contributor(http)
        camp = http.post("/v1/crowd/campaigns", headers=REVIEWER, json={
            "title": "Уплотнения август", "budget_cents_eur": 700}).json()["id"]

        first = _submit(http, cid, "sha-c1", campaign_id=camp)
        assert first.json()["state"] == "ACCEPTED"
        second = _submit(http, cid, "sha-c2", campaign_id=camp)
        assert second.json()["reject_reason"] == "campaign_budget_exhausted"

        card = http.get(f"/v1/crowd/campaigns/{camp}", headers=OPERATOR).json()
        assert card["spent_cents_eur"] == 500
        assert card["remaining_cents_eur"] == 200

    def test_paused_campaign_stops_accepting(self, http):
        cid = _contributor(http)
        camp = http.post("/v1/crowd/campaigns", headers=REVIEWER, json={
            "title": "Пауза", "budget_cents_eur": 5000}).json()["id"]
        http.post(f"/v1/crowd/campaigns/{camp}/state", headers=REVIEWER,
                  json={"state": "PAUSED"})
        r = _submit(http, cid, "sha-p1", campaign_id=camp)
        assert r.json()["reject_reason"] == "campaign_inactive"


class TestMoneyIntegrity:
    def test_clawback_is_a_separate_line_and_suspends(self, http):
        cid = _contributor(http)
        sid = _submit(http, cid, "sha-fraud").json()["id"]
        r = http.post(f"/v1/crowd/submissions/{sid}/clawback",
                      headers=REVIEWER, json={"reason": "фото с экрана"})
        assert r.json()["amount_cents_eur"] == -500

        ledger = http.get(f"/v1/crowd/contributors/{cid}/ledger",
                          headers=REVIEWER).json()
        # История не переписана: начисление осталось, сторно — отдельной строкой.
        assert [e["kind"] for e in ledger["entries"]] == ["ACCRUAL", "CLAWBACK"]
        assert ledger["balance_cents_eur"] == 0
        # Участник приостановлен — новый скан контур не принимает.
        assert _submit(http, cid, "sha-after").status_code == 409

    def test_payout_crypto_rail_disabled_by_default(self, http):
        cid = _contributor(http, crypto_wallet="0xabc")
        _submit(http, cid, "sha-pay1")
        r = http.post(f"/v1/crowd/contributors/{cid}/payout",
                      headers=REVIEWER, json={"rail": "CRYPTO"})
        assert r.status_code == 409
        assert "НПД или ГПХ" in r.json()["detail"]["message"]

    def test_payout_npd_zeroes_balance(self, http):
        cid = _contributor(http, npd_inn="123456789012")
        _submit(http, cid, "sha-pay2")
        r = http.post(f"/v1/crowd/contributors/{cid}/payout",
                      headers=REVIEWER, json={"rail": "NPD"})
        assert r.json()["amount_cents_eur"] == -500
        balance = http.get(f"/v1/crowd/contributors/{cid}/ledger",
                           headers=REVIEWER).json()["balance_cents_eur"]
        assert balance == 0


class TestCrowdSessionToReward:
    """Связка протокола kv_crowd с движком вознаграждений: quality_score
    сессии становится quality_score сабмишена, и порог 0.6 согласован —
    что автопринял протокол, то движок оплачивает, и наоборот."""

    def _session_score(self, scores):
        import json as _json
        import pathlib

        from app.protocol import SessionContext, StepResult, auto_accept

        doc = _json.loads((pathlib.Path(__file__).resolve().parents[2] / "docs"
                           / "schemas" / "examples" / "ri" / "kv_crowd.json")
                          .read_text(encoding="utf-8"))
        ctx = SessionContext(results={
            sid: StepResult(step_id=sid, status="COMPLETED",
                            data={"length_mm": 52} if sid == "c_dims" else {},
                            quality_score=s)
            for sid, s in scores.items()
        })
        return auto_accept(doc, ctx), ctx.quality_score

    def test_auto_accepted_session_is_paid(self, http):
        accepted, score = self._session_score(
            {"c_overview": 0.8, "c_mark": 0.8, "c_dims": 0.8})
        assert accepted is True

        cid = _contributor(http)
        r = _submit(http, cid, "sha-kv-ok", quality=score)
        assert r.json()["state"] == "ACCEPTED"

    def test_below_threshold_session_is_neither_accepted_nor_paid(self, http):
        """Один порог в обоих контурах: скан, который протокол отправил на
        ручную проверку, движок вознаграждений тоже не оплачивает молча."""
        accepted, score = self._session_score(
            {"c_overview": 0.5, "c_mark": 0.5, "c_dims": 0.5})
        assert accepted is False

        cid = _contributor(http)
        r = _submit(http, cid, "sha-kv-low", quality=score)
        assert r.json()["state"] == "REJECTED"
        assert r.json()["reject_reason"] == "quality"


class TestRolesAndAudit:
    def test_operator_cannot_touch_money_endpoints(self, http):
        assert http.post("/v1/crowd/contributors", headers=OPERATOR, json={
            "display_name": "x", "phone_hash": "y"}).status_code == 403
        assert http.get("/v1/crowd/contributors/any/ledger",
                        headers=OPERATOR).status_code == 403

    def test_money_moves_are_audited(self, http):
        cid = _contributor(http)
        _submit(http, cid, "sha-audit")
        actions = [e["action"] for e in
                   main_module.STORE.list_audit("t-internal")]
        assert "crowd.contributor.register" in actions
        assert "crowd.submit" in actions