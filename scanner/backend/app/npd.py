"""Выплаты НПД с чеком — провайдеро-независимый контур (docs/15 §5).

Прямое партнёрство с API «Мой налог» — месяцы согласований с ФНС, поэтому
рабочая интеграция идёт через платформу-посредника (Консоль.Про / Qugo /
Jump.Finance — выбор владельца). Этот модуль фиксирует ИНВАРИАНТЫ выплаты,
одинаковые для любого провайдера; адаптер конкретного API — тонкий класс,
реализующий ReceiptProvider.

Инварианты:
  N-1  выплата НПД без чека не существует: чек регистрируется у провайдера
       ДО списания баланса; отказ провайдера отменяет выплату целиком —
       баланс участника не трогается
  N-2  курс €→₽ фиксируется на момент выплаты и попадает и в чек, и в
       строку реестра — участник видит сумму в рублях до подтверждения
  N-3  идемпотентность по паре (contributor, баланс-снимок): повтор после
       потери ответа не создаёт второй чек — провайдер обязан принимать
       наш idempotency_key
  N-4  статус самозанятого проверяется перед выплатой: слетевший статус
       (превышен лимит 2,4 млн ₽/год, снятие с учёта) — отказ с причиной,
       а не чек в пустоту
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from .crowd import CrowdEngine, CrowdError


class ProviderUnavailable(Exception):
    """Сеть/5xx у провайдера: выплата откладывается, баланс не тронут."""


class NpdStatusInvalid(Exception):
    """Участник больше не самозанятый — выплата НПД невозможна."""


@dataclass
class Receipt:
    receipt_id: str
    url: str                    # ссылка на чек «Мой налог» — хранится в реестре
    amount_rub_kopecks: int


class ReceiptProvider(Protocol):
    """Контракт платформы-посредника. Реализации: KonsolProvider,
    QugoProvider, ... — тонкие адаптеры, вся логика здесь."""

    def check_npd_status(self, inn: str) -> bool: ...

    def register_income(self, *, inn: str, amount_rub_kopecks: int,
                        description: str, idempotency_key: str) -> Receipt: ...


@dataclass
class PayoutResult:
    receipt: Receipt
    amount_cents_eur: int
    fx_rate_rub_per_eur: float


class NpdPayoutService:
    """Оркестратор выплаты: статус → чек → списание. Порядок жёсткий (N-1):
    сначала документ, потом деньги — половинного состояния не бывает."""

    def __init__(self, engine: CrowdEngine, provider: ReceiptProvider) -> None:
        self._engine = engine
        self._provider = provider

    def payout(self, contributor_id: str, *, fx_rate_rub_per_eur: float,
               idempotency_key: str | None = None) -> PayoutResult:
        contributor = self._engine.contributors.get(contributor_id)
        if contributor is None:
            raise CrowdError(f"Участник {contributor_id} не зарегистрирован")
        if not contributor.npd_inn:
            raise CrowdError("Рельса НПД требует ИНН самозанятого")

        balance = self._engine.balance_cents(contributor_id)
        if balance <= 0:
            raise CrowdError("Баланс пуст — выплачивать нечего")
        if fx_rate_rub_per_eur <= 0:
            raise CrowdError(f"Недопустимый курс: {fx_rate_rub_per_eur}")

        # N-4: слетевший статус ловится до чека, не после
        if not self._provider.check_npd_status(contributor.npd_inn):
            raise NpdStatusInvalid(
                f"ИНН {contributor.npd_inn}: статус самозанятого не подтверждён "
                f"(лимит дохода или снятие с учёта) — выплата остановлена")

        amount_rub_kopecks = round(balance * fx_rate_rub_per_eur)  # центы×курс = копейки
        key = idempotency_key or f"{contributor_id}:{len(self._engine.ledger)}"

        # N-1: чек ДО списания; ProviderUnavailable пролетает наверх —
        # баланс не тронут, повтор безопасен благодаря idempotency_key (N-3)
        receipt = self._provider.register_income(
            inn=contributor.npd_inn,
            amount_rub_kopecks=amount_rub_kopecks,
            description="Вознаграждение за принятые сканы (краудсорсинг КВАНТ Скан)",
            idempotency_key=key,
        )

        entry = self._engine.create_payout(contributor_id, "NPD")
        entry.note = (f"rail=NPD receipt={receipt.receipt_id} "
                      f"fx={fx_rate_rub_per_eur} rub_kopecks={amount_rub_kopecks} "
                      f"url={receipt.url}")
        return PayoutResult(receipt=receipt, amount_cents_eur=balance,
                            fx_rate_rub_per_eur=fx_rate_rub_per_eur)
