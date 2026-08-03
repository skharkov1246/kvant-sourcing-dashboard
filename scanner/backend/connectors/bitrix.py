"""Коннектор Bitrix24 (docs/09 §9.2, §9.4, §9.5).

Тонкий адаптер между платформой и смарт-процессом «Запросы поставщикам»
(entityTypeId 166): приём заданий опросом с курсором и обратная запись
результата. Транспорт (HTTP-вебхук портала) — инъекция: в тестах FakeBitrix,
в бою — существующий bitrix_client.py репозитория (батчинг и троттлинг
2 rps там уже обкатаны).

Правила, ради которых модуль существует:
  * курсор с перекрытием окна 60 с — изменения на границе интервала не
    теряются, повторы гасятся идемпотентностью по external_ref (§9.2);
  * обратная запись идемпотентна по нашему session_id: маркер в комментарии
    таймлайна позволяет Bitrix-стороне распознать повтор (§9.5);
  * отказ портала — retry с экспоненциальным backoff; после исчерпания
    попыток исключение уходит вызывающему, задание остаётся ACCEPTED,
    а не «закрыто в приложении, потеряно в CRM» (инвариант I-8).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

ENTITY_TYPE_ID = 166
CURSOR_OVERLAP_S = 60


class BitrixUnavailable(RuntimeError):
    """Сеть/5xx/лимит портала — можно и нужно повторять."""


class BitrixError(RuntimeError):
    """Невосстановимая ошибка (неверный вебхук, нет прав): повтор бессмыслен."""


class BitrixTransport(Protocol):
    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class RetryPolicy:
    max_attempts: int = 4
    base_delay_s: float = 2.0

    def delays(self) -> list[float]:
        return [self.base_delay_s * (2 ** i) for i in range(self.max_attempts - 1)]


@dataclass
class BitrixConnector:
    transport: BitrixTransport
    entity_type_id: int = ENTITY_TYPE_ID
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    sleep: Callable[[float], None] = field(default=lambda _: None)
    # Категорию решает маршрутизатор на месте (Р-05), поэтому задание из CRM
    # по умолчанию уходит на kv_router, а не угадывает категорию по полям сделки.
    protocol_code: str = "kv_router"

    # ── Приём заданий (§9.2) ─────────────────────────────────────────────────

    def pull_tasks(self, cursor: str | None,
                   stage_id: str = "NEED_SCAN") -> tuple[list[dict[str, Any]], str | None]:
        """Опрос смарт-процесса: элементы стадии → заявки inbound.

        Возвращает (заявки для POST /v1/inbound/tasks, новый курсор).
        Повторы из перекрытия окна гасит идемпотентность по external_ref
        на нашей стороне — здесь дедупликации нет намеренно.
        """
        filter_: dict[str, Any] = {"stageId": [stage_id]}
        if cursor:
            filter_[">=updatedTime"] = _minus_overlap(cursor)
        response = self._call_with_retry("crm.item.list", {
            "entityTypeId": self.entity_type_id,
            "filter": filter_,
            "order": {"updatedTime": "asc"},
        })
        items = (response.get("result") or {}).get("items", [])
        requests = [self._to_inbound(item) for item in items]
        new_cursor = items[-1]["updatedTime"] if items else cursor
        return requests, new_cursor

    def _to_inbound(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "external_system": "bitrix24",
            "external_ref": str(item["id"]),
            "protocol_code": self.protocol_code,
            "subject": {
                "title": item.get("title"),
                "stage_id": item.get("stageId"),
                "company_id": item.get("companyId"),
            },
        }

    def pull_suppliers(self) -> list[dict[str, Any]]:
        """Справочник поставщиков из CRM (§9.5, crm.company.list):
        офлайн-подсказки поставщика в трассировке — устройство предлагает
        контрагентов из CRM, а не свободный ввод с опечатками."""
        response = self._call_with_retry("crm.company.list", {"select": ["id", "title", "ufInn"]})
        return [
            {"name": c.get("title"), "inn": c.get("ufInn"), "ref": str(c.get("id"))}
            for c in (response.get("result") or {}).get("items", [])
            if c.get("title")
        ]

    # ── Обратная запись (§9.4, §9.5) ─────────────────────────────────────────

    def push_result(self, row: dict[str, Any],
                    field_map: dict[str, str] | None = None) -> None:
        """Строка экспорта (/v1/export/sessions) → элемент смарт-процесса.

        Габариты и вердикт — в UF-поля (коды портала приходят конфигом
        field_map, они разные у каждого портала), сводка — комментарием в
        таймлайн с маркером [kvant-scan session_id], по которому повтор
        доставки отличим от нового результата.

        Бросает BitrixUnavailable после исчерпания попыток — вызывающий
        обязан оставить задание в ACCEPTED и вернуть строку в очередь (I-8).
        """
        item_id = row.get("external_ref")
        if not item_id:
            raise BitrixError("У строки экспорта нет external_ref элемента Bitrix")

        fields = self._map_fields(row, field_map or {})
        if fields:
            self._call_with_retry("crm.item.update", {
                "entityTypeId": self.entity_type_id,
                "id": int(item_id),
                "fields": fields,
            })
        self._call_with_retry("crm.timeline.comment.add", {
            "fields": {
                "ENTITY_ID": int(item_id),
                "ENTITY_TYPE": "DYNAMIC_%s" % self.entity_type_id,
                "COMMENT": self._render_comment(row),
            },
        })

    @staticmethod
    def _map_fields(row: dict[str, Any], field_map: dict[str, str]) -> dict[str, Any]:
        """Плоские значения строки экспорта → UF-коды портала.

        Маппится только то, что явно перечислено в конфиге: лишнее поле в
        CRM хуже отсутствующего — его кто-то начнёт считать правдой.
        """
        flat: dict[str, Any] = {
            "quality_score": row.get("quality_score"),
            "verdict": (row.get("review") or {}).get("verdict"),
            "codes": "; ".join(
                str(c.get("raw_value", "")) for c in row.get("codes", []) if c.get("raw_value")
            ) or None,
        }
        for m in row.get("measurements", []):
            for key in ("length_mm", "width_mm", "height_mm", "weight_kg"):
                if key in m and key not in flat:
                    flat[key] = m[key]
        return {
            uf_code: flat[our_key]
            for our_key, uf_code in field_map.items()
            if flat.get(our_key) is not None
        }

    @staticmethod
    def _render_comment(row: dict[str, Any]) -> str:
        review = row.get("review") or {}
        lines = [
            f"[kvant-scan {row['session_id']}] Результат сканирования",
            f"Протокол: {row.get('protocol')}",
            f"Качество: {row.get('quality_score')}",
        ]
        if review.get("verdict"):
            lines.append(f"Вердикт контролёра: {review['verdict']}"
                         + (f" — {review['verdict_comment']}" if review.get("verdict_comment") else ""))
        if row.get("codes"):
            lines.append("Коды: " + "; ".join(str(c.get("raw_value")) for c in row["codes"]))
        return "\n".join(lines)

    # ── Транспорт с повторами ────────────────────────────────────────────────

    def _call_with_retry(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        last: BitrixUnavailable | None = None
        for attempt, delay in enumerate([*self.retry.delays(), None]):
            try:
                return self.transport.call(method, params)
            except BitrixUnavailable as exc:
                last = exc
                if delay is None:
                    break
                self.sleep(delay)
        raise BitrixUnavailable(
            f"{method}: портал недоступен после {self.retry.max_attempts} попыток: {last}")


def _minus_overlap(cursor: str) -> str:
    """Курсор минус окно перекрытия (§9.2): граница интервала не теряет правок."""
    ts = dt.datetime.fromisoformat(cursor)
    return (ts - dt.timedelta(seconds=CURSOR_OVERLAP_S)).isoformat()
