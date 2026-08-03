"""Планировщик опроса Bitrix24 (docs/09 §9.2): портал → inbound платформы.

Отдельный процесс (`python -m connectors.bitrix_poller`), не часть API:
падение опроса не трогает приём событий с устройств. Интервал 300 с — из
docs/09; курсор персистентный, с перекрытием окна 60 с внутри коннектора.

Идемпотентность двухслойная и намеренно избыточная:
  * ключ Idempotency-Key детерминирован от external_ref — повтор прохода
    (перекрытие окна, рестарт процесса) даёт тот же ключ;
  * сервер дополнительно дедуплицирует по (external_system, external_ref).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .bitrix import BitrixConnector, BitrixUnavailable

POLL_INTERVAL_S = 300


class PlatformClient(Protocol):
    """POST /v1/inbound/tasks платформы; реализация — requests в бою,
    TestClient в тестах."""

    def submit_inbound(self, request: dict[str, Any], idempotency_key: str) -> dict[str, Any]: ...


@dataclass
class BitrixPoller:
    connector: BitrixConnector
    platform: PlatformClient
    cursor_file: Path

    def run_once(self) -> dict[str, int]:
        """Один проход опроса. Отказ портала — пропуск прохода, не падение:
        курсор не сдвигается, следующий проход заберёт то же окно."""
        cursor = self._read_cursor()
        try:
            requests, new_cursor = self.connector.pull_tasks(cursor)
        except BitrixUnavailable:
            return {"pulled": 0, "created": 0, "deduplicated": 0, "skipped_round": 1}

        stats = {"pulled": len(requests), "created": 0, "deduplicated": 0, "skipped_round": 0}
        for request in requests:
            key = f"bitrix24:{request['external_ref']}"
            result = self.platform.submit_inbound(request, idempotency_key=key)
            if result.get("deduplicated"):
                stats["deduplicated"] += 1
            else:
                stats["created"] += 1
        # Курсор сдвигается только после успешной доставки всего прохода:
        # упавшая середина списка не теряет хвост (следующий проход повторит).
        if new_cursor:
            self._write_cursor(new_cursor)
        return stats

    def _read_cursor(self) -> str | None:
        try:
            return self.cursor_file.read_text(encoding="utf-8").strip() or None
        except FileNotFoundError:
            return None

    def _write_cursor(self, cursor: str) -> None:
        self.cursor_file.parent.mkdir(parents=True, exist_ok=True)
        self.cursor_file.write_text(cursor, encoding="utf-8")


def _main() -> None:  # pragma: no cover — обвязка процесса, логика в run_once
    import os

    import requests

    class HttpPlatformClient:
        def __init__(self, base_url: str, tenant: str) -> None:
            self.base_url = base_url
            self.tenant = tenant

        def submit_inbound(self, request: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
            response = requests.post(
                f"{self.base_url}/v1/inbound/tasks",
                json=request,
                headers={
                    "X-Tenant-Id": self.tenant,
                    "X-User-Id": "bitrix-poller",
                    "Idempotency-Key": idempotency_key,
                },
                timeout=30,
            )
            response.raise_for_status()
            return response.json()

    class WebhookTransport:
        """POST {webhook}/{method} — форма вызова обкатана на эмуляторе."""

        def __init__(self, webhook_url: str) -> None:
            self.webhook_url = webhook_url.rstrip("/")

        def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
            try:
                response = requests.post(f"{self.webhook_url}/{method}", json=params, timeout=30)
            except requests.RequestException as exc:
                raise BitrixUnavailable(str(exc)) from exc
            if response.status_code >= 500:
                raise BitrixUnavailable(f"HTTP {response.status_code}")
            return response.json()

    poller = BitrixPoller(
        connector=BitrixConnector(transport=WebhookTransport(os.environ["BITRIX_WEBHOOK_URL"])),
        platform=HttpPlatformClient(
            os.environ.get("KVANT_PLATFORM_URL", "http://127.0.0.1:8077"),
            os.environ.get("KVANT_TENANT", "t-internal"),
        ),
        cursor_file=Path(os.environ.get("KVANT_BITRIX_CURSOR", ".bitrix_cursor")),
    )
    while True:
        stats = poller.run_once()
        print(f"bitrix poll: {stats}", flush=True)
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":  # pragma: no cover
    _main()
