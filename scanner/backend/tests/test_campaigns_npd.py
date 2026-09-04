"""Кампании с бюджетами и выплаты НПД с чеком (docs/15 §3, §5).

Смысл набора: бюджет кампании — жёсткий потолок, а не рекомендация; выплата
НПД не существует без чека, и половинного состояния «деньги ушли, чека нет»
не бывает ни при каком отказе провайдера.
"""

import datetime as dt

import pytest

from app.crowd import Campaign, Contributor, CrowdEngine, CrowdError, RewardPolicy, Submission
from app.npd import NpdPayoutService, NpdStatusInvalid, ProviderUnavailable, Receipt

T0 = dt.datetime(2026, 7, 28, 10, 0)


def _contributor(**kw) -> Contributor:
    defaults = dict(display_name="Иван", phone_hash="ph-1", npd_inn="500100732259")
    defaults.update(kw)
    return Contributor(**defaults)


def _sub(c, sha, campaign=None, minutes=0, **kw) -> Submission:
    defaults = dict(contributor_id=c.id, content_sha256=sha, quality_score=0.9,
                    campaign_id=campaign, submitted_at=T0 + dt.timedelta(minutes=minutes))
    defaults.update(kw)
    return Submission(**defaults)


class TestCampaignBudget:
    def _engine_with_campaign(self, budget_cents):
        engine = CrowdEngine(RewardPolicy(hourly_flag_threshold=100))
        campaign = engine.create_campaign(Campaign(
            title="Уплотнения: горнодобыча, июль", budget_cents_eur=budget_cents))
        return engine, campaign

    def test_budget_is_a_hard_ceiling(self):
        """Бюджет 10 € = ровно 2 оплаченных скана по 5 €; третий не платится."""
        engine, campaign = self._engine_with_campaign(1000)
        c = engine.register(_contributor())
        assert engine.submit(_sub(c, "s1", campaign.id)).state == "ACCEPTED"
        assert engine.submit(_sub(c, "s2", campaign.id, minutes=1)).state == "ACCEPTED"
        third = engine.submit(_sub(c, "s3", campaign.id, minutes=2))
        assert third.state == "REJECTED"
        assert third.reject_reason == "campaign_budget_exhausted"
        assert engine.campaign_spent_cents(campaign.id) == 1000

    def test_bonus_respects_campaign_budget(self):
        """Бонус за поставщика не пробивает потолок кампании."""
        engine, campaign = self._engine_with_campaign(600)  # 5 € скан + 1 € запаса
        c = engine.register(_contributor())
        sub = engine.submit(_sub(c, "s1", campaign.id,
                                 supplier_claim={"name": "Ningbo"}))
        assert sub.state == "ACCEPTED"
        with pytest.raises(CrowdError, match="Бюджет кампании исчерпан"):
            engine.confirm_new_supplier(sub.id)

    def test_paused_campaign_rejects_new_scans(self):
        engine, campaign = self._engine_with_campaign(10_000)
        c = engine.register(_contributor())
        engine.set_campaign_state(campaign.id, "PAUSED")
        sub = engine.submit(_sub(c, "s1", campaign.id))
        assert sub.state == "REJECTED" and sub.reject_reason == "campaign_inactive"

    def test_closed_campaign_cannot_reopen(self):
        engine, campaign = self._engine_with_campaign(10_000)
        engine.set_campaign_state(campaign.id, "CLOSED")
        with pytest.raises(CrowdError, match="не открывается"):
            engine.set_campaign_state(campaign.id, "ACTIVE")

    def test_unknown_campaign_is_rejected_not_free_money(self):
        engine = CrowdEngine()
        c = engine.register(_contributor())
        sub = engine.submit(_sub(c, "s1", campaign="ghost"))
        assert sub.state == "REJECTED" and sub.reject_reason == "unknown_campaign"

    def test_clawback_does_not_refill_budget(self):
        """Клобэк возвращает деньги с баланса мошенника, но бюджет кампании
        не пополняет: операционный расход на фрод уже случился."""
        engine, campaign = self._engine_with_campaign(1000)
        c = engine.register(_contributor())
        sub = engine.submit(_sub(c, "s1", campaign.id))
        engine.submit(_sub(c, "s2", campaign.id, minutes=1))
        engine.clawback(sub.id, reason="фото экрана")
        assert engine.campaign_spent_cents(campaign.id) == 1000  # не 500

    def test_off_campaign_scans_unaffected(self):
        """Скан без кампании живёт по общим правилам — бюджеты его не касаются."""
        engine, _ = self._engine_with_campaign(0)
        c = engine.register(_contributor())
        assert engine.submit(_sub(c, "s1")).state == "ACCEPTED"


class FakeProvider:
    def __init__(self, *, npd_ok=True, fail_register=False):
        self.npd_ok = npd_ok
        self.fail_register = fail_register
        self.register_calls = []

    def check_npd_status(self, inn):
        return self.npd_ok

    def register_income(self, *, inn, amount_rub_kopecks, description, idempotency_key):
        self.register_calls.append({"inn": inn, "amount": amount_rub_kopecks,
                                    "key": idempotency_key})
        if self.fail_register:
            raise ProviderUnavailable("504 от платформы")
        return Receipt(receipt_id="rcpt-1", url="https://lknpd.nalog.ru/rcpt-1",
                       amount_rub_kopecks=amount_rub_kopecks)


def _engine_with_balance(cents=500):
    engine = CrowdEngine()
    c = engine.register(_contributor())
    engine.submit(_sub(c, "s1"))
    assert engine.balance_cents(c.id) == cents
    return engine, c


class TestNpdPayout:
    def test_receipt_then_debit_with_fixed_fx(self):
        """N-1 + N-2: чек до списания; курс фиксируется в чеке и в реестре."""
        engine, c = _engine_with_balance()
        provider = FakeProvider()
        result = NpdPayoutService(engine, provider).payout(
            c.id, fx_rate_rub_per_eur=105.0)
        # 500 центов × 105 ₽/€ = 52500 копеек = 525 ₽
        assert result.receipt.amount_rub_kopecks == 52500
        assert engine.balance_cents(c.id) == 0
        payout_entry = engine.ledger[-1]
        assert "receipt=rcpt-1" in payout_entry.note
        assert "fx=105.0" in payout_entry.note

    def test_provider_failure_leaves_balance_untouched(self):
        """N-1: отказ провайдера отменяет выплату целиком — денег без чека нет."""
        engine, c = _engine_with_balance()
        service = NpdPayoutService(engine, FakeProvider(fail_register=True))
        with pytest.raises(ProviderUnavailable):
            service.payout(c.id, fx_rate_rub_per_eur=105.0)
        assert engine.balance_cents(c.id) == 500  # не списано
        assert all(e.kind != "PAYOUT" for e in engine.ledger)

    def test_lapsed_npd_status_stops_payout_before_receipt(self):
        """N-4: слетевший статус самозанятого — отказ ДО регистрации чека."""
        engine, c = _engine_with_balance()
        provider = FakeProvider(npd_ok=False)
        with pytest.raises(NpdStatusInvalid):
            NpdPayoutService(engine, provider).payout(c.id, fx_rate_rub_per_eur=105.0)
        assert provider.register_calls == []
        assert engine.balance_cents(c.id) == 500

    def test_idempotency_key_reaches_provider(self):
        """N-3: повтор после потери ответа не создаёт второй чек — ключ наш."""
        engine, c = _engine_with_balance()
        provider = FakeProvider()
        NpdPayoutService(engine, provider).payout(
            c.id, fx_rate_rub_per_eur=100.0, idempotency_key="pay-42")
        assert provider.register_calls[0]["key"] == "pay-42"

    def test_invalid_fx_rate_is_refused(self):
        engine, c = _engine_with_balance()
        with pytest.raises(CrowdError, match="курс"):
            NpdPayoutService(engine, FakeProvider()).payout(
                c.id, fx_rate_rub_per_eur=0)

    def test_empty_balance_never_reaches_provider(self):
        engine = CrowdEngine()
        c = engine.register(_contributor())
        provider = FakeProvider()
        with pytest.raises(CrowdError, match="Баланс пуст"):
            NpdPayoutService(engine, provider).payout(c.id, fx_rate_rub_per_eur=100.0)
        assert provider.register_calls == []
