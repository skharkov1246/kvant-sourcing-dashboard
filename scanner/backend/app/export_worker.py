"""Воркер очереди экспорта (§09.4, инвариант I-8).

Доставляет принятые результаты во внешнюю систему через коннектор.
Судьба записи:
  * подтверждение → CONFIRMED, задание переводится в EXPORTED;
  * временный отказ → возврат в очередь с экспоненциальным backoff;
  * исчерпание попыток или невосстановимая ошибка → DEAD_LETTER + задание
    ОСТАЁТСЯ ACCEPTED: «закрыто в приложении, потеряно в CRM» — худший
    инцидент, поэтому терять результат нельзя даже мёртвым письмом.

Запуск — периодический (cron/планировщик); один прогон обрабатывает все
созревшие записи тенанта и никогда не бросает исключений доставки наружу.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from connectors.bitrix import BitrixError, BitrixUnavailable

from .export import registry_row
from .store import utcnow


@dataclass
class ExportWorker:
    store: Any
    connector: Any
    field_map: dict[str, str] = field(default_factory=dict)
    max_attempts: int = 5
    base_delay_s: float = 60.0

    def run_once(self, tenant_id: str, now: str | None = None) -> dict[str, int]:
        """Один прогон: все созревшие записи тенанта. Возвращает счётчики."""
        now = now or utcnow()
        stats = {"confirmed": 0, "deferred": 0, "dead_lettered": 0}
        for entry in self.store.due_exports(tenant_id, now):
            session_id = entry["session_id"]
            row = registry_row(self.store, tenant_id, session_id)
            if row is None or not row.get("external_ref"):
                # Запись без адреса доставки — ошибка постановки, не сети.
                self.store.dead_letter_export(
                    tenant_id, session_id, "нет external_ref — некуда доставлять")
                stats["dead_lettered"] += 1
                continue
            try:
                self.connector.push_result(row, field_map=self.field_map)
            except BitrixUnavailable as exc:
                attempts = entry["attempts"] + 1
                if attempts >= self.max_attempts:
                    # I-8: задание НЕ трогаем — оно остаётся ACCEPTED.
                    self.store.dead_letter_export(tenant_id, session_id, str(exc))
                    self.store.audit(tenant_id, "export-worker", "export.dead_letter",
                                     session_id, {"error": str(exc), "attempts": attempts})
                    stats["dead_lettered"] += 1
                else:
                    self.store.defer_export(
                        tenant_id, session_id, attempts,
                        _plus_seconds(now, self.base_delay_s * (2 ** (attempts - 1))),
                        str(exc),
                    )
                    stats["deferred"] += 1
                continue
            except BitrixError as exc:
                # Конфигурационная ошибка ретраями не лечится.
                self.store.dead_letter_export(tenant_id, session_id, str(exc))
                stats["dead_lettered"] += 1
                continue
            self.store.confirm_export(tenant_id, session_id)
            self._mark_task_exported(tenant_id, row)
            self.store.audit(tenant_id, "export-worker", "export.confirmed",
                             session_id, {"external_ref": row.get("external_ref")})
            stats["confirmed"] += 1
        return stats

    def _mark_task_exported(self, tenant_id: str, row: dict[str, Any]) -> None:
        task = self.store.get_task(tenant_id, row.get("task_id") or "")
        if task and task.get("state") == "ACCEPTED":
            task["state"] = "EXPORTED"
            self.store.upsert_task(task)


def _plus_seconds(now_iso: str, seconds: float) -> str:
    return (dt.datetime.fromisoformat(now_iso) + dt.timedelta(seconds=seconds)).isoformat()
