"""Краудсорсинговый контур «скан за вознаграждение» — логика (docs/15).

Зеркало `schema_crowd.sql`. Принципы C-1…C-5 из шапки схемы; главный из них:
платим за ПРИНЯТЫЙ УНИКАЛЬНЫЙ скан, а не за присланный файл. 5 € за фото без
дедупа и приёмки — это не база данных, а насос для мусора.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field

BASE_SCAN_CENTS = 500  # 5 € эквивалент за принятый уникальный скан


class CrowdError(ValueError):
    """Нарушение контура вознаграждений, которое сервер отклоняет."""


@dataclass
class RewardPolicy:
    """Тариф — данные, не константы в коде (C-3)."""

    base_scan_cents_eur: int = BASE_SCAN_CENTS
    new_supplier_bonus_cents: int = 300
    daily_cap_scans: int = 40
    hourly_flag_threshold: int = 12
    min_quality_score: float = 0.6
    audit_sample_pct: int = 10


@dataclass
class Contributor:
    display_name: str
    phone_hash: str
    npd_inn: str | None = None
    gph_details: dict | None = None
    crypto_wallet: str | None = None
    status: str = "ACTIVE"
    accepted: int = 0
    rejected: int = 0
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def acceptance_rate(self) -> float:
        total = self.accepted + self.rejected
        return self.accepted / total if total else 1.0


@dataclass
class Submission:
    contributor_id: str
    content_sha256: str
    phash: str | None = None
    supplier_claim: dict | None = None
    quality_score: float | None = None
    submitted_at: dt.datetime = dt.datetime(2026, 1, 1)
    state: str = "RECEIVED"
    reject_reason: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class LedgerEntry:
    contributor_id: str
    kind: str  # ACCRUAL | BONUS | CLAWBACK | PAYOUT
    amount_cents_eur: int
    submission_id: str | None = None
    note: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


class CrowdEngine:
    """Жизненный цикл сабмишена и реестр вознаграждений.

    Дедуп здесь двухступенчатый: точный (sha256 — тот же файл) и перцептивный
    (phash — пересъёмка того же кадра/экрана). Порог похожести phash отдан
    вызывающей стороне: в проде это hamming-дистанция по кадрам, здесь —
    равенство строк.
    """

    def __init__(self, policy: RewardPolicy | None = None) -> None:
        self.policy = policy or RewardPolicy()
        self.contributors: dict[str, Contributor] = {}
        self.submissions: dict[str, Submission] = {}
        self.ledger: list[LedgerEntry] = []
        self._sha_index: set[str] = set()
        self._phash_index: set[str] = set()

    # — регистрация —

    def register(self, contributor: Contributor) -> Contributor:
        self.contributors[contributor.id] = contributor
        return contributor

    # — приём скана —

    def submit(self, sub: Submission) -> Submission:
        c = self._active_contributor(sub.contributor_id)
        self.submissions[sub.id] = sub

        if sub.content_sha256 in self._sha_index:
            return self._reject(sub, "duplicate")
        if sub.phash and sub.phash in self._phash_index:
            # Пересъёмка того же кадра — главный вектор фрода при оплате за скан
            return self._reject(sub, "duplicate")

        day = sub.submitted_at.date()
        paid_today = sum(
            1 for s in self.submissions.values()
            if s.contributor_id == c.id and s.state == "ACCEPTED"
            and s.submitted_at.date() == day
        )
        if paid_today >= self.policy.daily_cap_scans:
            return self._reject(sub, "cap_exceeded")

        hour_ago = sub.submitted_at - dt.timedelta(hours=1)
        last_hour = sum(
            1 for s in self.submissions.values()
            if s.contributor_id == c.id and s.submitted_at > hour_ago
        )
        if last_hour > self.policy.hourly_flag_threshold:
            # Не отказ, а карантин: быстрые серии бывают честными (стеллаж
            # однотипных коробок) — решает аудитор, деньги не начисляются.
            sub.state = "FLAGGED"
            sub.reject_reason = "velocity_audit"
            return sub

        if (sub.quality_score or 0.0) < self.policy.min_quality_score:
            return self._reject(sub, "quality")

        return self._accept(sub, c)

    def resolve_flagged(self, submission_id: str, verdict_ok: bool) -> Submission:
        sub = self.submissions[submission_id]
        if sub.state != "FLAGGED":
            raise CrowdError(f"Сабмишен {submission_id} не в карантине")
        c = self._active_contributor(sub.contributor_id)
        if verdict_ok:
            sub.reject_reason = None
            return self._accept(sub, c)
        return self._reject(sub, "fraud_suspect")

    def confirm_new_supplier(self, submission_id: str) -> LedgerEntry:
        """Бонус начисляется только после ПОДТВЕРЖДЕНИЯ нового субпоставщика
        перекрёстной проверкой — заявка участника остаётся declared (C-4)."""
        sub = self.submissions[submission_id]
        if sub.state != "ACCEPTED":
            raise CrowdError("Бонус только по принятому скану")
        if not sub.supplier_claim:
            raise CrowdError("В скане нет заявки о субпоставщике")
        return self._post(sub.contributor_id, "BONUS",
                          self.policy.new_supplier_bonus_cents, sub.id,
                          note="новый субпоставщик подтверждён")

    def clawback(self, submission_id: str, reason: str) -> LedgerEntry:
        """Постфактум-аудит нашёл фрод: начисление сторнируется ОТДЕЛЬНОЙ
        строкой (C-2), история не переписывается, участник — на разбор."""
        sub = self.submissions[submission_id]
        accrued = sum(
            e.amount_cents_eur for e in self.ledger
            if e.submission_id == submission_id and e.kind in ("ACCRUAL", "BONUS")
        )
        if accrued <= 0:
            raise CrowdError("По сабмишену нечего сторнировать")
        c = self.contributors[sub.contributor_id]
        c.status = "SUSPENDED"
        return self._post(sub.contributor_id, "CLAWBACK", -accrued, sub.id, note=reason)

    # — баланс и выплаты —

    def balance_cents(self, contributor_id: str) -> int:
        return sum(e.amount_cents_eur for e in self.ledger
                   if e.contributor_id == contributor_id)

    def create_payout(self, contributor_id: str, rail: str, *,
                      crypto_rail_enabled: bool = False) -> LedgerEntry:
        c = self._active_contributor(contributor_id)
        if rail == "CRYPTO":
            # Юр. ограничение (docs/15 §5): для резидентов РФ выплата в крипте
            # за услуги — вне правового поля; рельса существует в модели, но
            # включается явным решением владельца по конкретной юрисдикции.
            if not crypto_rail_enabled:
                raise CrowdError(
                    "Крипто-рельса выключена: для РФ выплата в криптовалюте за "
                    "услуги вне правового поля (ФЗ-259) — используйте НПД или ГПХ")
            if not c.crypto_wallet:
                raise CrowdError("У участника нет проверенного кошелька")
        elif rail == "NPD":
            if not c.npd_inn:
                raise CrowdError("Рельса НПД требует ИНН самозанятого")
        elif rail == "GPH":
            if not c.gph_details:
                raise CrowdError("Рельса ГПХ требует реквизитов договора")
        else:
            raise CrowdError(f"Неизвестная рельса выплаты: {rail}")

        amount = self.balance_cents(contributor_id)
        if amount <= 0:
            raise CrowdError("Баланс пуст — выплачивать нечего")
        return self._post(contributor_id, "PAYOUT", -amount, note=f"rail={rail}")

    # — внутреннее —

    def _accept(self, sub: Submission, c: Contributor) -> Submission:
        sub.state = "ACCEPTED"
        self._sha_index.add(sub.content_sha256)
        if sub.phash:
            self._phash_index.add(sub.phash)
        c.accepted += 1
        self._post(c.id, "ACCRUAL", self.policy.base_scan_cents_eur, sub.id)
        return sub

    def _reject(self, sub: Submission, reason: str) -> Submission:
        sub.state = "REJECTED"
        sub.reject_reason = reason
        c = self.contributors[sub.contributor_id]
        c.rejected += 1
        return sub

    def _post(self, contributor_id: str, kind: str, amount: int,
              submission_id: str | None = None, note: str | None = None) -> LedgerEntry:
        entry = LedgerEntry(contributor_id=contributor_id, kind=kind,
                            amount_cents_eur=amount, submission_id=submission_id,
                            note=note)
        self.ledger.append(entry)
        return entry

    def _active_contributor(self, contributor_id: str) -> Contributor:
        c = self.contributors.get(contributor_id)
        if c is None:
            raise CrowdError(f"Участник {contributor_id} не зарегистрирован")
        if c.status != "ACTIVE":
            raise CrowdError(f"Участник {contributor_id} в статусе {c.status}")
        return c
