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
        self.brand_refs: dict[str, list[dict[str, Any]]] = {}
        self.installations: dict[str, list[dict[str, Any]]] = {}
        self.installation_keys: dict[str, str] = {}  # item_id → ключ установки
        self.geometry: dict[str, list[dict[str, Any]]] = {}
        self.review_queue: dict[tuple[str, str], dict[str, Any]] = {}  # (tenant_id, session_id)
        self.export_queue: dict[tuple[str, str], dict[str, Any]] = {}  # (tenant_id, session_id)
        self.audit_log: list[dict[str, Any]] = []  # append-only (§07.4)
        self.dynamic_directories: dict[str, dict[str, Any]] = {}

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

    def list_protocols(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.protocols.values())

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

    def list_sessions(self, tenant_id: str) -> list[str]:
        """Сессии тенанта в порядке появления первого события."""
        with self._lock:
            seen: dict[str, None] = {}
            for e in self.events:
                if e.tenant_id == tenant_id:
                    seen.setdefault(e.session_id)
            return list(seen)

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

    def get_asset(self, asset_id: str) -> StoredAsset | None:
        with self._lock:
            return self.assets.get(asset_id)

    def put_chunk(self, asset_id: str, n: int, data: bytes) -> None:
        with self._lock:
            asset = self.assets[asset_id]
            offset = n * asset.chunk_size
            if len(asset.data) < offset + len(data):
                asset.data.extend(bytearray(offset + len(data) - len(asset.data)))
            asset.data[offset:offset + len(data)] = data
            asset.received_chunks.add(n)
            asset.upload_state = "uploading"

    def asset_payload(self, asset_id: str) -> bytes | None:
        """Собранные байты подтверждённого файла — для оффлоада в облако.
        Контракт общий для обоих хранилищ: где лежат чанки — деталь стора."""
        with self._lock:
            asset = self.assets.get(asset_id)
            if asset is None or asset.upload_state != "verified":
                return None
            return bytes(asset.data[: asset.bytes])

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

    # ── Многоуровневый бренд (§06.4) ─────────────────────────────────────────

    def add_brand_ref(self, item_id: str, ref: dict[str, Any]) -> dict[str, Any]:
        """Идемпотентно по (роль, бренд, нормализованный артикул): повторная
        съёмка того же шильдика не плодит одинаковые пути закупки."""
        with self._lock:
            refs = self.brand_refs.setdefault(item_id, [])
            key = (ref["role"], ref["brand"], ref.get("article_norm"))
            for existing in refs:
                if (existing["role"], existing["brand"],
                        existing.get("article_norm")) == key:
                    # Более высокое доверие вытесняет более низкое: подтверждённый
                    # документом изготовитель сильнее догадки OCR.
                    from .brand_chain import CONFIDENCE

                    if CONFIDENCE.index(ref["confidence"]) < CONFIDENCE.index(
                            existing["confidence"]):
                        existing.update(ref)
                    return dict(existing)
            refs.append(ref)
            return dict(ref)

    def brand_refs_of(self, item_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.brand_refs.get(item_id, [])]

    def set_installation(self, item_id: str, nodes: list[dict[str, Any]],
                         key: str | None = None) -> None:
        with self._lock:
            self.installations[item_id] = [dict(n) for n in nodes]
            if key:
                self.installation_keys[item_id] = key

    # ── Геометрия: рулетка, LiDAR, фотограмметрия, сканер (§05.6) ────────────

    def add_geometry(self, item_id: str, artifact: dict[str, Any]) -> dict[str, Any]:
        """Только добавление: уточнение — новая запись с refined_from (I-5)."""
        with self._lock:
            self.geometry.setdefault(item_id, []).append(artifact)
            return dict(artifact)

    def geometry_of(self, item_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(a) for a in self.geometry.get(item_id, [])]

    def items_of_installation(self, tenant_id: str, key: str) -> list[str]:
        """Все детали одного обхода — по ключу физической установки."""
        with self._lock:
            return sorted(i for i, k in self.installation_keys.items() if k == key)

    def list_installations(self, tenant_id: str) -> list[dict[str, Any]]:
        with self._lock:
            counts: dict[str, int] = {}
            for key in self.installation_keys.values():
                counts[key] = counts.get(key, 0) + 1
            return [{"key": k, "items": v} for k, v in sorted(counts.items())]

    def installation_of(self, item_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(n) for n in self.installations.get(item_id, [])]

    def find_items_by_article(self, tenant_id: str, article_norm: str) -> list[str]:
        """Поиск по чужой накладной: артикул → карточки, где он встречается
        под ЛЮБЫМ брендом (в этом и смысл — найти изготовителя)."""
        with self._lock:
            return sorted(
                item_id for item_id, refs in self.brand_refs.items()
                if any(r.get("article_norm") == article_norm for r in refs)
            )

    def item_trace(self, item_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.trace_links.get(item_id, []))

    # ── Очередь ручного ревью ────────────────────────────────────────────────
    # Сюда попадают сессии с вердиктом MANUAL_REVIEW приёмки (acceptance.py).
    # Жизненный цикл: enqueue (идемпотентно, причины сливаются) → вердикт
    # контролёра. Повторная постановка после REWORK переоткрывает запись.

    def enqueue_review(self, tenant_id: str, session_id: str, reasons: list[str]) -> dict[str, Any]:
        with self._lock:
            key = (tenant_id, session_id)
            entry = self.review_queue.get(key)
            if entry and entry["resolved_at"] is None:
                # Повторное срабатывание приёмки — причины сливаются без дублей.
                entry["reasons"] = list(dict.fromkeys([*entry["reasons"], *reasons]))
            else:
                self.review_queue[key] = {
                    "session_id": session_id,
                    "reasons": list(dict.fromkeys(reasons)),
                    "enqueued_at": utcnow(),
                    "resolved_at": None,
                    "verdict": None,
                    "verdict_by": None,
                    "verdict_comment": None,
                }
            return dict(self.review_queue[key])

    def get_review(self, tenant_id: str, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self.review_queue.get((tenant_id, session_id))
            return dict(entry) if entry else None

    def list_review_queue(self, tenant_id: str) -> list[dict[str, Any]]:
        with self._lock:
            open_entries = [
                dict(e) for (t, _), e in self.review_queue.items()
                if t == tenant_id and e["resolved_at"] is None
            ]
            return sorted(open_entries, key=lambda e: e["enqueued_at"])

    def list_verdicts(self, tenant_id: str) -> list[dict[str, Any]]:
        """Решённые записи — для scope verdicts в sync_pull: оператор видит
        «принято/брак/пересъёмка» по своим сессиям."""
        with self._lock:
            resolved = [
                dict(e) for (t, _), e in self.review_queue.items()
                if t == tenant_id and e["resolved_at"] is not None
            ]
            return sorted(resolved, key=lambda e: e["resolved_at"])

    # ── Аудит (§07.4): только добавление, метода изменения нет намеренно ────

    def audit(self, tenant_id: str, actor: str, action: str, target: str,
              details: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.audit_log.append({
                "at": utcnow(),
                "tenant_id": tenant_id,
                "actor": actor,
                "action": action,
                "target": target,
                "details": details or {},
            })

    def list_audit(self, tenant_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self.audit_log if e["tenant_id"] == tenant_id]

    # ── Динамические справочники (снапшоты из внешних систем) ────────────────

    def put_dynamic_directory(self, name: str, sections: dict[str, Any]) -> dict[str, Any]:
        """Версия растёт ТОЛЬКО при изменении контента: ежепятиминутный опрос
        CRM без изменений не сбрасывает 304-кэш всех устройств."""
        with self._lock:
            existing = self.dynamic_directories.get(name)
            if existing and existing["sections"] == sections:
                return dict(existing)
            doc = {
                "directory": name,
                "version": (existing["version"] + 1) if existing else 1,
                "sections": sections,
            }
            self.dynamic_directories[name] = doc
            return dict(doc)

    def get_dynamic_directory(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            doc = self.dynamic_directories.get(name)
            return dict(doc) if doc else None

    # ── Очередь экспорта (I-8) ───────────────────────────────────────────────
    # Задание не закрыто, пока внешняя система не подтвердила приём (§09.4).
    # PENDING → CONFIRMED при подтверждении; DEAD_LETTER после исчерпания
    # попыток — задание при этом ОСТАЁТСЯ ACCEPTED, теряться результату нельзя.

    def enqueue_export(self, tenant_id: str, session_id: str) -> dict[str, Any]:
        with self._lock:
            key = (tenant_id, session_id)
            if key not in self.export_queue:
                self.export_queue[key] = {
                    "session_id": session_id,
                    "state": "PENDING",
                    "attempts": 0,
                    "next_attempt_at": utcnow(),
                    "last_error": None,
                    "created_at": utcnow(),
                    "confirmed_at": None,
                }
            return dict(self.export_queue[key])

    def get_export(self, tenant_id: str, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self.export_queue.get((tenant_id, session_id))
            return dict(entry) if entry else None

    def due_exports(self, tenant_id: str, now: str) -> list[dict[str, Any]]:
        with self._lock:
            due = [
                dict(e) for (t, _), e in self.export_queue.items()
                if t == tenant_id and e["state"] == "PENDING" and e["next_attempt_at"] <= now
            ]
            return sorted(due, key=lambda e: e["created_at"])

    def confirm_export(self, tenant_id: str, session_id: str) -> None:
        with self._lock:
            entry = self.export_queue.get((tenant_id, session_id))
            if entry and entry["state"] == "PENDING":
                entry.update(state="CONFIRMED", confirmed_at=utcnow(), last_error=None)

    def defer_export(self, tenant_id: str, session_id: str, attempts: int,
                     next_attempt_at: str, error: str) -> None:
        with self._lock:
            entry = self.export_queue.get((tenant_id, session_id))
            if entry and entry["state"] == "PENDING":
                entry.update(attempts=attempts, next_attempt_at=next_attempt_at,
                             last_error=error)

    def dead_letter_export(self, tenant_id: str, session_id: str, error: str) -> None:
        with self._lock:
            entry = self.export_queue.get((tenant_id, session_id))
            if entry and entry["state"] == "PENDING":
                entry.update(state="DEAD_LETTER", last_error=error)

    def resolve_review(
        self, tenant_id: str, session_id: str, verdict: str, by: str, comment: str | None = None,
    ) -> dict[str, Any] | None:
        """Вердикт по открытой записи; None — записи нет или она уже решена
        (уже решённую не перезаписываем: второй контролёр должен увидеть 409,
        а не молча затереть решение первого)."""
        with self._lock:
            entry = self.review_queue.get((tenant_id, session_id))
            if entry is None or entry["resolved_at"] is not None:
                return None
            entry.update(
                verdict=verdict, verdict_by=by, verdict_comment=comment, resolved_at=utcnow(),
            )
            return dict(entry)


def _payload_digest(event: dict[str, Any]) -> str:
    import json

    canonical = json.dumps(
        {"type": event["type"], "seq": event["seq"], "payload": event.get("payload") or {}},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
