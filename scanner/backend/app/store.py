"""Хранилище.

Здесь — эталонная реализация в памяти: она задаёт контракт и позволяет гонять
весь конвейер в тестах и на ноутбуке без базы. Промышленная реализация —
PostgreSQL по схеме `schema.sql` с RLS; интерфейс тот же, поэтому подмена
не затрагивает роутеры.

Ключевые инварианты, за которые отвечает именно хранилище:
  * I-2 — событие применяется ровно один раз: уникальность (tenant_id, client_event_id)
  * I-3 — сессия иммутабельна после SUBMITTED
  * I-6 — тенант не пересекает границу
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StoredEvent:
    tenant_id: str
    session_id: str
    seq: int
    client_event_id: str
    type: str
    payload: dict[str, Any]
    device_ts: str
    server_ts: str


@dataclass
class StoredAsset:
    id: str
    tenant_id: str
    session_id: str
    step_id: str
    kind: str
    mime: str
    bytes: int
    sha256: str
    chunk_size: int
    received_chunks: set[int] = field(default_factory=set)
    data: bytearray = field(default_factory=bytearray)
    upload_state: str = "pending"
    capture_meta: dict[str, Any] = field(default_factory=dict)


class Store:
    """Потокобезопасное хранилище в памяти."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.tasks: dict[str, dict[str, Any]] = {}
        self.protocols: dict[tuple[str, int], dict[str, Any]] = {}
        self.active_protocol: dict[str, int] = {}
        self.events: list[StoredEvent] = []
        self._event_index: set[tuple[str, str]] = set()  # (tenant_id, client_event_id)
        self._event_payload_hash: dict[tuple[str, str], str] = {}
        self.assets: dict[str, StoredAsset] = {}
        self._asset_by_hash: dict[tuple[str, str], str] = {}  # (tenant_id, sha256) -> asset_id
        self.idempotency: dict[tuple[str, str], str] = {}  # (tenant_id, key) -> task_id
        self.trace_links: dict[str, list[dict[str, Any]]] = {}

    # ── Протоколы ────────────────────────────────────────────────────────────

    def put_protocol(self, protocol: dict[str, Any]) -> None:
        with self._lock:
            key = (protocol["code"], protocol["version"])
            if key in self.protocols:
                # Версия иммутабельна после публикации (§03.2).
                raise ValueError(f"Версия протокола {key[0]}@{key[1]} уже опубликована")
            self.protocols[key] = protocol
            if protocol.get("status") == "active":
                self.active_protocol[protocol["code"]] = protocol["version"]

    def get_protocol(self, code: str, version: int | None = None) -> dict[str, Any] | None:
        with self._lock:
            if version is None:
                version = self.active_protocol.get(code)
                if version is None:
                    return None
            return self.protocols.get((code, version))

    # ── Задания ──────────────────────────────────────────────────────────────

    def upsert_task(self, task: dict[str, Any]) -> None:
        with self._lock:
            task["updated_at"] = utcnow()
            self.tasks[task["id"]] = task

    def get_task(self, tenant_id: str, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self.tasks.get(task_id)
            # Рубеж изоляции: чужой тенант не существует, а не «запрещён».
            if task is None or task["tenant_id"] != tenant_id:
                return None
            return task

    def list_tasks(self, tenant_id: str, **filters: Any) -> list[dict[str, Any]]:
        with self._lock:
            result = [t for t in self.tasks.values() if t["tenant_id"] == tenant_id]
            for key, value in filters.items():
                if value is not None:
                    result = [t for t in result if t.get(key) == value]
            return sorted(result, key=lambda t: (t["priority"], t["created_at"]))

    def find_task_by_external(self, tenant_id: str, system: str, ref: str) -> dict[str, Any] | None:
        with self._lock:
            for task in self.tasks.values():
                if (
                    task["tenant_id"] == tenant_id
                    and task.get("external_system") == system
                    and task.get("external_ref") == ref
                ):
                    return task
            return None

    def remember_idempotency(self, tenant_id: str, key: str, task_id: str) -> None:
        with self._lock:
            self.idempotency[(tenant_id, key)] = task_id

    def lookup_idempotency(self, tenant_id: str, key: str) -> str | None:
        with self._lock:
            return self.idempotency.get((tenant_id, key))

    # ── События ──────────────────────────────────────────────────────────────

    def append_event(self, tenant_id: str, event: dict[str, Any]) -> str:
        """Возвращает 'accepted', 'duplicate' или 'conflicting_duplicate'.

        Инвариант I-2: повторная доставка безопасна. Тот же client_event_id с
        другим payload — признак бага клиента, а не нормальный случай (§04.5).
        """
        with self._lock:
            key = (tenant_id, event["client_event_id"])
            digest = _payload_digest(event)
            if key in self._event_index:
                return "duplicate" if self._event_payload_hash.get(key) == digest else "conflicting_duplicate"

            self._event_index.add(key)
            self._event_payload_hash[key] = digest
            self.events.append(
                StoredEvent(
                    tenant_id=tenant_id,
                    session_id=event["session_id"],
                    seq=event["seq"],
                    client_event_id=event["client_event_id"],
                    type=event["type"],
                    payload=event.get("payload") or {},
                    device_ts=event["device_ts"],
                    server_ts=utcnow(),
                )
            )
            return "accepted"

    def session_events(self, tenant_id: str, session_id: str, since_seq: int = -1) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "client_event_id": e.client_event_id,
                    "session_id": e.session_id,
                    "seq": e.seq,
                    "type": e.type,
                    "payload": e.payload,
                    "device_ts": e.device_ts,
                    "server_ts": e.server_ts,
                }
                for e in self.events
                if e.tenant_id == tenant_id and e.session_id == session_id and e.seq > since_seq
            ]

    # ── Медиа ────────────────────────────────────────────────────────────────

    def init_asset(self, asset: StoredAsset) -> tuple[StoredAsset, bool]:
        """Возвращает (актив, дедуплицирован_ли).

        Дедупликация по (tenant_id, sha256): повторная отправка того же кадра
        не занимает место и завершается мгновенно (§04.4).
        """
        with self._lock:
            existing_id = self._asset_by_hash.get((asset.tenant_id, asset.sha256))
            if existing_id:
                return self.assets[existing_id], True
            self.assets[asset.id] = asset
            self._asset_by_hash[(asset.tenant_id, asset.sha256)] = asset.id
            return asset, False

    def put_chunk(self, asset_id: str, n: int, data: bytes) -> None:
        with self._lock:
            asset = self.assets[asset_id]
            offset = n * asset.chunk_size
            if len(asset.data) < offset + len(data):
                asset.data.extend(bytearray(offset + len(data) - len(asset.data)))
            asset.data[offset:offset + len(data)] = data
            asset.received_chunks.add(n)
            asset.upload_state = "uploading"

    def complete_asset(self, asset_id: str) -> dict[str, Any]:
        """Сверка контрольной суммы. Несовпадение — повтор целиком, а не тихий приём."""
        with self._lock:
            asset = self.assets[asset_id]
            expected_chunks = (asset.bytes + asset.chunk_size - 1) // asset.chunk_size
            missing = [n for n in range(expected_chunks) if n not in asset.received_chunks]
            if missing:
                return {"status": "missing_chunks", "missing_chunks": missing}
            payload = bytes(asset.data[: asset.bytes])
            if hashlib.sha256(payload).hexdigest() != asset.sha256:
                asset.upload_state = "pending"
                asset.received_chunks.clear()
                asset.data = bytearray()
                return {"status": "checksum_mismatch"}
            asset.upload_state = "verified"
            return {"status": "verified"}

    # ── Трассировка ──────────────────────────────────────────────────────────

    def add_trace_link(self, item_id: str, link: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.trace_links.setdefault(item_id, []).append(link)
            return link

    def item_trace(self, item_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.trace_links.get(item_id, []))


def _payload_digest(event: dict[str, Any]) -> str:
    import json

    canonical = json.dumps(
        {"type": event["type"], "seq": event["seq"], "payload": event.get("payload") or {}},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
