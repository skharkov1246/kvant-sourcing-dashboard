"""Краудсорсинг «скан за 5 €»: каждый тест — вектор фрода или правило выплат.

Смысл набора: оплата за скан превращает каждый пробел контроля в статью
расходов. Дедуп, потолки, карантин скорости и клобэк — не«фичи», а то, что
отличает базу данных от насоса для мусора (docs/15 §4).
"""

import datetime as dt

import pytest

from app.crowd import (
    Contributor,
    CrowdEngine,
    CrowdError,
    RewardPolicy,
    Submission,
)

T0 = dt.datetime(2026, 7, 28, 10, 0)


def _contributor(**kw) -> Contributor:
    defaults = dict(display_name="Иван", phone_hash="ph-1", npd_inn="500100732259")
    defaults.update(kw)
    return Contributor(**defaults)


def _sub(c, sha="sha-1", minutes=0, **kw) -> Submission:
    defaults = dict(contributor_id=c.id, content_sha256=sha, quality_score=0.9,
                    submitted_at=T0 + dt.timedelta(minutes=minutes))
    defaults.update(kw)
    return Submission(**defaults)


@pytest.fixture()
def engine() -> CrowdEngine:
    return CrowdEngine()


class TestAccrual:
    def test_accepted_unique_scan_accrues_5_eur(self, engine):
        """C-1: принятый уникальный скан = 500 евроцентов в реестре."""
        c = engine.register(_contributor())
        sub = engine.submit(_sub(c))
        assert sub.state == "ACCEPTED"
        assert engine.balance_cents(c.id) == 500

    def test_same_file_is_never_paid_twice(self, engine):
        """Дедуп по sha256: повторная отправка того же файла — отказ без начисления."""
        c = engine.register(_contributor())
        engine.submit(_sub(c, sha="same"))
        dup = engine.submit(_sub(c, sha="same", minutes=5))
        assert dup.state == "REJECTED" and dup.reject_reason == "duplicate"
        assert engine.balance_cents(c.id) == 500

    def test_perceptual_duplicate_is_rejected(self, engine):
        """Пересъёмка того же кадра (другой файл, тот же phash) — главный
        вектор фрода при оплате за скан."""
        c = engine.register(_contributor())
        engine.submit(_sub(c, sha="a", phash="ph-x"))
        near = engine.submit(_sub(c, sha="b", phash="ph-x", minutes=3))
        assert near.state == "REJECTED" and near.reject_reason == "duplicate"

    def test_similar_phash_within_threshold_is_duplicate(self, engine):
        """Сдвиг кадрирования меняет пару бит aHash — равенство хешей такое
        пропустило бы; hamming-дистанция до порога тарифа ловит."""
        c = engine.register(_contributor())
        engine.submit(_sub(c, sha="a", phash="00000000000000ff"))
        # 4 бита разницы (f0 vs ff в младшем байте) — в пределах порога 6.
        near = engine.submit(_sub(c, sha="b", phash="00000000000000f0", minutes=3))
        assert near.state == "REJECTED" and near.reject_reason == "duplicate"

    def test_distant_phash_is_a_different_frame(self, engine):
        """Другая деталь = десятки различающихся бит — принимается."""
        c = engine.register(_contributor())
        engine.submit(_sub(c, sha="a", phash="00000000000000ff"))
        far = engine.submit(_sub(c, sha="b", phash="ffffffff00000000", minutes=3))
        assert far.state == "ACCEPTED"

    def test_phash_threshold_is_policy_not_constant(self):
        """Порог — данные тарифа (C-3): ноль превращает похожесть в строгое
        равенство, и близкий кадр проходит."""
        engine = CrowdEngine(RewardPolicy(phash_hamming_threshold=0))
        c = engine.register(_contributor())
        engine.submit(_sub(c, sha="a", phash="00000000000000ff"))
        near = engine.submit(_sub(c, sha="b", phash="00000000000000f0", minutes=3))
        assert near.state == "ACCEPTED"

    def test_low_quality_scan_is_not_paid(self, engine):
        c = engine.register(_contributor())
        bad = engine.submit(_sub(c, quality_score=0.3))
        assert bad.state == "REJECTED" and bad.reject_reason == "quality"
        assert engine.balance_cents(c.id) == 0

    def test_supplier_bonus_only_after_confirmation(self, engine):
        """C-4: заявка о субпоставщике — declared; бонус приходит только после
        перекрёстного подтверждения, а не в момент скана."""
        c = engine.register(_contributor())
        sub = engine.submit(_sub(c, supplier_claim={"name": "Ningbo Seals Ltd"}))
        assert engine.balance_cents(c.id) == 500  # бонуса ещё нет
        engine.confirm_new_supplier(sub.id)
        assert engine.balance_cents(c.id) == 800

    def test_bonus_without_claim_is_refused(self, engine):
        c = engine.register(_contributor())
        sub = engine.submit(_sub(c))
        with pytest.raises(CrowdError, match="нет заявки"):
            engine.confirm_new_supplier(sub.id)


class TestAntiFraud:
    def test_daily_cap_stops_payment_not_scanning(self, engine):
        """Потолок дня: 41-й скан не оплачивается (cap_exceeded)."""
        eng = CrowdEngine(RewardPolicy(daily_cap_scans=2, hourly_flag_threshold=100))
        c = eng.register(_contributor())
        eng.submit(_sub(c, sha="s1"))
        eng.submit(_sub(c, sha="s2", minutes=90))
        third = eng.submit(_sub(c, sha="s3", minutes=180))
        assert third.state == "REJECTED" and third.reject_reason == "cap_exceeded"
        assert eng.balance_cents(c.id) == 1000

    def test_velocity_burst_goes_to_audit_not_rejection(self, engine):
        """Быстрая серия — карантин FLAGGED, а не отказ: стеллаж однотипных
        коробок бывает честным. Деньги — только после вердикта аудитора."""
        eng = CrowdEngine(RewardPolicy(hourly_flag_threshold=3))
        c = eng.register(_contributor())
        for i in range(3):
            eng.submit(_sub(c, sha=f"s{i}", minutes=i))
        burst = eng.submit(_sub(c, sha="s-burst", minutes=4))
        assert burst.state == "FLAGGED"
        assert eng.balance_cents(c.id) == 1500  # за burst не начислено

        eng.resolve_flagged(burst.id, verdict_ok=True)
        assert eng.balance_cents(c.id) == 2000

    def test_clawback_reverses_accrual_and_suspends(self, engine):
        """C-2: клобэк — отдельная строка реестра, история не переписывается;
        участник уходит в SUSPENDED и больше не сдаёт сканы."""
        c = engine.register(_contributor())
        sub = engine.submit(_sub(c))
        engine.clawback(sub.id, reason="фото экрана монитора")
        assert engine.balance_cents(c.id) == 0
        assert [e.kind for e in engine.ledger] == ["ACCRUAL", "CLAWBACK"]
        with pytest.raises(CrowdError, match="SUSPENDED"):
            engine.submit(_sub(c, sha="new"))

    def test_acceptance_rate_tracks_history(self, engine):
        c = engine.register(_contributor())
        engine.submit(_sub(c, sha="ok"))
        engine.submit(_sub(c, sha="ok"))  # дубликат → rejected
        assert c.acceptance_rate == 0.5


class TestPayouts:
    def test_npd_payout_zeroes_balance(self, engine):
        c = engine.register(_contributor())
        engine.submit(_sub(c))
        entry = engine.create_payout(c.id, "NPD")
        assert entry.amount_cents_eur == -500
        assert engine.balance_cents(c.id) == 0

    def test_crypto_rail_is_disabled_by_default(self, engine):
        """C-5: крипто-рельса существует в модели, но для РФ выключена —
        включение только явным решением владельца по юрисдикции (docs/15 §5)."""
        c = engine.register(_contributor(crypto_wallet="0xabc"))
        engine.submit(_sub(c))
        with pytest.raises(CrowdError, match="ФЗ-259"):
            engine.create_payout(c.id, "CRYPTO")
        entry = engine.create_payout(c.id, "CRYPTO", crypto_rail_enabled=True)
        assert entry.amount_cents_eur == -500

    def test_rail_requires_matching_requisites(self, engine):
        c = engine.register(_contributor(npd_inn=None))
        engine.submit(_sub(c))
        with pytest.raises(CrowdError, match="НПД"):
            engine.create_payout(c.id, "NPD")
        with pytest.raises(CrowdError, match="ГПХ"):
            engine.create_payout(c.id, "GPH")

    def test_empty_balance_is_not_paid(self, engine):
        c = engine.register(_contributor())
        with pytest.raises(CrowdError, match="Баланс пуст"):
            engine.create_payout(c.id, "NPD")


class TestAuditSample:
    """Выборочный постфактум-аудит (audit_sample_pct): детерминированная
    доля принятых уходит на проверку — без Math.random в денежном контуре."""

    def _wide_policy(self, pct):
        return RewardPolicy(audit_sample_pct=pct,
                            daily_cap_scans=10_000, hourly_flag_threshold=10_000)

    def test_sample_share_is_close_to_tariff(self):
        engine = CrowdEngine(self._wide_policy(10))
        c = engine.register(_contributor())
        for i in range(400):
            engine.submit(_sub(c, sha=f"sha-{i}", minutes=i))
        share = len(engine.audit_sample()) / 400
        assert 0.04 <= share <= 0.16, f"доля выборки {share} далека от 10%"

    def test_sampling_is_deterministic_by_id(self):
        """Та же заявка в двух движках — одна судьба: выборка от id, а не
        от случайности процесса."""
        first = CrowdEngine(self._wide_policy(10))
        second = CrowdEngine(self._wide_policy(10))
        c1 = first.register(_contributor())
        c2 = second.register(_contributor())
        for i in range(50):
            sid = f"fixed-id-{i}"
            first.submit(Submission(contributor_id=c1.id, content_sha256=f"a{i}",
                                    quality_score=0.9, submitted_at=T0, id=sid))
            second.submit(Submission(contributor_id=c2.id, content_sha256=f"a{i}",
                                     quality_score=0.9, submitted_at=T0, id=sid))
        assert ([s.id for s in first.audit_sample()]
                == [s.id for s in second.audit_sample()])

    def test_clamp_zero_and_hundred(self):
        none = CrowdEngine(self._wide_policy(0))
        c = none.register(_contributor())
        for i in range(20):
            none.submit(_sub(c, sha=f"z-{i}", minutes=i))
        assert none.audit_sample() == []

        every = CrowdEngine(self._wide_policy(100))
        c2 = every.register(_contributor())
        for i in range(20):
            every.submit(_sub(c2, sha=f"h-{i}", minutes=i))
        assert len(every.audit_sample()) == 20

    def test_rejected_scans_never_reach_audit(self):
        """Аудитим только оплаченное: отклонённый скан денег не получил,
        проверять его постфактум нечего."""
        engine = CrowdEngine(self._wide_policy(100))
        c = engine.register(_contributor())
        engine.submit(_sub(c, sha="same"))
        engine.submit(_sub(c, sha="same", minutes=1))  # дубль
        assert len(engine.audit_sample()) == 1


class TestNpdReceipts:
    """Чек самозанятого — часть выплаты НПД, когда провайдер подключён:
    выплата без чека при обязательных чеках — нарушение НПД, не деградация."""

    class _FakeReceipts:
        def __init__(self):
            self.issued = []

        def issue_receipt(self, inn, amount_cents_eur, description):
            self.issued.append((inn, amount_cents_eur))
            return "receipt-42"

    class _DeadReceipts:
        def issue_receipt(self, inn, amount_cents_eur, description):
            raise CrowdError("«Мой налог» недоступен")

    def test_without_provider_payout_works_as_before(self):
        """Текущий режим: чеки оформляются вручную вне системы."""
        engine = CrowdEngine()
        c = engine.register(_contributor())
        engine.submit(_sub(c))
        entry = engine.create_payout(c.id, "NPD")
        assert entry.note == "rail=NPD"

    def test_provider_issues_receipt_and_it_lands_in_ledger(self):
        receipts = self._FakeReceipts()
        engine = CrowdEngine(npd_receipts=receipts)
        c = engine.register(_contributor())
        engine.submit(_sub(c))
        entry = engine.create_payout(c.id, "NPD")
        assert receipts.issued == [("500100732259", 500)]
        assert entry.note == "rail=NPD чек=receipt-42"

    def test_failed_receipt_cancels_payout_entirely(self):
        """Чек ДО строки PAYOUT: упавший провайдер не оставляет в реестре
        выплату без чека, баланс не тронут."""
        engine = CrowdEngine(npd_receipts=self._DeadReceipts())
        c = engine.register(_contributor())
        engine.submit(_sub(c))
        with pytest.raises(CrowdError, match="Мой налог"):
            engine.create_payout(c.id, "NPD")
        assert engine.balance_cents(c.id) == 500
        assert all(e.kind != "PAYOUT" for e in engine.ledger)

    def test_unconfigured_stub_fails_honestly(self):
        """Флаг «чеки обязательны» до интеграции API — честный отказ с
        причиной, а не тихая выплата без чека."""
        from app.crowd import UnconfiguredNpdReceipts

        engine = CrowdEngine(npd_receipts=UnconfiguredNpdReceipts())
        c = engine.register(_contributor())
        engine.submit(_sub(c))
        with pytest.raises(CrowdError, match="не подключено"):
            engine.create_payout(c.id, "NPD")

    def test_receipt_only_for_npd_rail(self):
        """ГПХ-выплата провайдера чеков не касается — чек НПД про режим
        самозанятого, а не про выплаты вообще."""
        engine = CrowdEngine(npd_receipts=self._DeadReceipts())
        c = engine.register(_contributor(gph_details={"contract": "Д-1"}))
        engine.submit(_sub(c))
        entry = engine.create_payout(c.id, "GPH")
        assert entry.note == "rail=GPH"
