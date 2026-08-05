"""Промышленное хранилище: PostgreSQL по `schema.sql`.

Интерфейс повторяет эталонный `store.py` — роутеры не знают, какое хранилище
под ними (общий набор тестов гоняется на обоих, см. conftest). Ключевые
инварианты здесь исполняет БД, а не питон:

  * I-2 — UNIQUE (tenant_id, client_event_id): повтор ловится констрейнтом,
    а не проверкой в коде, и потому переживает горизонтальное масштабирование;
  * дедуп медиа — UNIQUE (tenant_id, sha256);
  * идемпотентность приёма заданий — PRIMARY KEY (tenant_id, key).

API оперирует строковыми идентификаторами внешнего мира (код тенанта
«t-internal», session_id устройства). Здесь они детерминированно отображаются
в UUID (uuid5), а обратные ответы реконструируют исходные строки — внешний
контракт не замечает подмены хранилища.

Сессии, задания и карточки, на которые ссылаются события/активы/трассировка,
создаются заглушками при первом упоминании: приём отделён от обработки (§08.4),
и событие не должно отвергаться из-за того, что регистрация сессии ещё едет
в соседнем канале синхронизации.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .store import StoredAsset, _payload_digest, utcnow

_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # RFC 4122 namespace


def _u5(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "|".join(parts)))


def _as_uuid(value: str, *scope: str) -> str:
    """Строка внешнего мира → UUID: валидный UUID проходит как есть,
    прочее отображается детерминированно."""
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return _u5(*scope, value)


class PgStore:
    """Синхронная реализация поверх psycopg3. Одно соединение, autocommit:
    каждая операция — атомарный стейтмент, конфликты решают констрейнты."""

    def __init__(self, dsn: str) -> None:
        self._conn = psycopg.connect(dsn, autocommit=True)
        self._tenant_cache: dict[str, str] = {}

    # ── Служебное ────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Полная очистка данных (для тестов). Схему не трогает."""
        self._conn.execute(
            "TRUNCATE directory_snapshot, export_queue, review_queue, trace_conflict, trace_link, item_record, code_read, "
            "measurement, asset_chunk, asset, session_event, dead_letter_event, "
            "scan_session, inbound_idempotency, scan_task, capture_protocol, "
            "device, app_user, site, tenant_grant, tenant CASCADE"
        )
        self._conn.execute("TRUNCATE audit.entry")
        self._tenant_cache.clear()

    def _tenant(self, code: str) -> str:
        cached = self._tenant_cache.get(code)
        if cached:
            return cached
        # Вид тенанта в API-слое не передаётся — реальная регистрация тенантов
        # идёт админ-контуром; здесь эвристика для служебного get-or-create.
        kind = "internal" if code in ("internal", "t-internal") else "customer"
        row = self._conn.execute(
            "INSERT INTO tenant (code, name, kind) VALUES (%s, %s, %s) "
            "ON CONFLICT (code) DO UPDATE SET name = tenant.name RETURNING id",
            (code, code, kind),
        ).fetchone()
        self._tenant_cache[code] = str(row[0])
        return self._tenant_cache[code]

    def _tenant_code(self, tenant_uuid: str) -> str:
        for code, tid in self._tenant_cache.items():
            if tid == tenant_uuid:
                return code
        row = self._conn.execute("SELECT code FROM tenant WHERE id = %s", (tenant_uuid,)).fetchone()
        return row[0] if row else tenant_uuid

    def _stub_user(self, tenant_uuid: str, name: str) -> str:
        uid = _u5("user", tenant_uuid, name)
        self._conn.execute(
            "INSERT INTO app_user (id, tenant_id, ext_id, display_name) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (uid, tenant_uuid, name, name),
        )
        return uid

    def _stub_device(self, tenant_uuid: str) -> str:
        did = _u5("device", tenant_uuid)
        self._conn.execute(
            "INSERT INTO device (id, tenant_id, platform, model, app_version, schema_version) "
            "VALUES (%s, %s, 'android', 'stub', '0.0.0', 1) ON CONFLICT (id) DO NOTHING",
            (did, tenant_uuid),
        )
        return did

    def _stub_task(self, tenant_uuid: str) -> str:
        tid = _u5("task-stub", tenant_uuid)
        self._conn.execute(
            "INSERT INTO scan_task (id, tenant_id, protocol_code, subject, state) "
            "VALUES (%s, %s, 'unknown', '{}', 'NEW') ON CONFLICT (id) DO NOTHING",
            (tid, tenant_uuid),
        )
        return tid

    def _session(self, tenant_uuid: str, session_text: str) -> str:
        sid = _as_uuid(session_text, "session", tenant_uuid)
        self._conn.execute(
            "INSERT INTO scan_session (id, tenant_id, task_id, device_id, user_id, "
            " protocol_code, protocol_version, state, started_at, ext_ref) "
            "VALUES (%s, %s, %s, %s, %s, 'unknown', 0, 'ACTIVE', now(), %s) "
            "ON CONFLICT (id) DO NOTHING",
            (sid, tenant_uuid, self._stub_task(tenant_uuid),
             self._stub_device(tenant_uuid), self._stub_user(tenant_uuid, "ingest"),
             session_text),
        )
        return sid

    def list_sessions(self, tenant_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT ext_ref FROM scan_session "
            "WHERE tenant_id = %s AND ext_ref IS NOT NULL "
            "ORDER BY started_at, ext_ref",
            (self._tenant(tenant_id),),
        ).fetchall()
        return [r[0] for r in rows]

    # ── Протоколы ────────────────────────────────────────────────────────────

    def put_protocol(self, protocol: dict[str, Any]) -> None:
        scope = protocol.get("scope", "global")
        if protocol.get("status") == "active":
            # Семантика указателя активной версии: новая active вытесняет старую.
            self._conn.execute(
                "UPDATE capture_protocol SET status = 'deprecated' "
                "WHERE code = %s AND scope = %s AND status = 'active'",
                (protocol["code"], scope),
            )
        try:
            self._conn.execute(
                "INSERT INTO capture_protocol (code, version, scope, status, "
                " schema_version, document, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (protocol["code"], protocol["version"], scope,
                 protocol.get("status", "draft"), protocol["schema_version"],
                 Jsonb(protocol), _u5("user", "publisher")),
            )
        except psycopg.errors.UniqueViolation:
            raise ValueError(
                f"Версия протокола {protocol['code']}@{protocol['version']} уже опубликована"
            ) from None

    def get_protocol(self, code: str, version: int | None = None) -> dict[str, Any] | None:
        if version is None:
            row = self._conn.execute(
                "SELECT document FROM capture_protocol WHERE code = %s AND status = 'active'",
                (code,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT document FROM capture_protocol WHERE code = %s AND version = %s",
                (code, version),
            ).fetchone()
        return row[0] if row else None

    def list_protocols(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT document FROM capture_protocol").fetchall()
        return [r[0] for r in rows]

    # ── Задания ──────────────────────────────────────────────────────────────

    def upsert_task(self, task: dict[str, Any]) -> None:
        tenant_uuid = self._tenant(task["tenant_id"])
        task["updated_at"] = utcnow()
        self._conn.execute(
            "INSERT INTO scan_task (id, tenant_id, external_system, external_ref, "
            " protocol_code, protocol_version, subject, priority, due_at, "
            " assignee_pool, state, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (id) DO UPDATE SET state = EXCLUDED.state, "
            " priority = EXCLUDED.priority, updated_at = EXCLUDED.updated_at",
            (task["id"], tenant_uuid, task.get("external_system"),
             task.get("external_ref"), task["protocol_code"],
             task.get("protocol_version"), Jsonb(task["subject"]),
             task.get("priority", 100), task.get("due_at"),
             task.get("assignee_pool"), task["state"],
             task.get("created_at") or utcnow(), task["updated_at"]),
        )

    _TASK_COLS = ("id, external_system, external_ref, protocol_code, protocol_version, "
                  "subject, priority, due_at, assignee_pool, state, created_at, updated_at")

    def _task_row(self, row: tuple, tenant_code: str) -> dict[str, Any]:
        (tid, ext_sys, ext_ref, pcode, pver, subject, priority,
         due_at, pool, state, created, updated) = row
        return {
            "id": str(tid), "tenant_id": tenant_code, "site_id": None,
            "external_system": ext_sys, "external_ref": ext_ref,
            "protocol_code": pcode, "protocol_version": pver,
            "subject": subject, "priority": priority,
            "due_at": due_at.isoformat() if due_at else None,
            "assignee_user_id": None, "assignee_pool": pool, "state": state,
            "created_at": created.isoformat(), "updated_at": updated.isoformat(),
        }

    def get_task(self, tenant_id: str, task_id: str) -> dict[str, Any] | None:
        try:
            uuid.UUID(task_id)
        except ValueError:
            return None
        row = self._conn.execute(
            f"SELECT {self._TASK_COLS} FROM scan_task WHERE id = %s AND tenant_id = %s",
            (task_id, self._tenant(tenant_id)),
        ).fetchone()
        return self._task_row(row, tenant_id) if row else None

    def list_tasks(self, tenant_id: str, **filters: Any) -> list[dict[str, Any]]:
        clauses, params = ["tenant_id = %s"], [self._tenant(tenant_id)]
        for key, value in filters.items():
            if value is not None:
                clauses.append(f"{key} = %s")
                params.append(value)
        rows = self._conn.execute(
            f"SELECT {self._TASK_COLS} FROM scan_task WHERE {' AND '.join(clauses)} "
            "ORDER BY priority, created_at",
            params,
        ).fetchall()
        return [self._task_row(r, tenant_id) for r in rows]

    def find_task_by_external(self, tenant_id: str, system: str, ref: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            f"SELECT {self._TASK_COLS} FROM scan_task "
            "WHERE tenant_id = %s AND external_system = %s AND external_ref = %s",
            (self._tenant(tenant_id), system, ref),
        ).fetchone()
        return self._task_row(row, tenant_id) if row else None

    def remember_idempotency(self, tenant_id: str, key: str, task_id: str) -> None:
        self._conn.execute(
            "INSERT INTO inbound_idempotency (tenant_id, key, task_id) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (self._tenant(tenant_id), key, task_id),
        )

    def lookup_idempotency(self, tenant_id: str, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT task_id FROM inbound_idempotency WHERE tenant_id = %s AND key = %s",
            (self._tenant(tenant_id), key),
        ).fetchone()
        return str(row[0]) if row else None

    # ── События ──────────────────────────────────────────────────────────────

    def append_event(self, tenant_id: str, event: dict[str, Any]) -> str:
        tenant_uuid = self._tenant(tenant_id)
        session_uuid = self._session(tenant_uuid, event["session_id"])
        eid = _as_uuid(event["client_event_id"], "event", tenant_uuid)
        try:
            cur = self._conn.execute(
                "INSERT INTO session_event (tenant_id, session_id, seq, "
                " client_event_id, type, payload, device_ts) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (tenant_id, client_event_id) DO NOTHING",
                (tenant_uuid, session_uuid, event["seq"], eid, event["type"],
                 Jsonb(event.get("payload") or {}), event["device_ts"]),
            )
        except psycopg.errors.UniqueViolation:
            # PK (session_id, seq): тот же порядковый номер с другим содержимым —
            # это баг клиента, а не повтор доставки (§04.5).
            return "conflicting_duplicate"
        if cur.rowcount == 1:
            return "accepted"
        stored = self._conn.execute(
            "SELECT type, seq, payload FROM session_event "
            "WHERE tenant_id = %s AND client_event_id = %s",
            (tenant_uuid, eid),
        ).fetchone()
        same = _payload_digest(event) == _payload_digest(
            {"type": stored[0], "seq": stored[1], "payload": stored[2]}
        )
        return "duplicate" if same else "conflicting_duplicate"

    def session_events(self, tenant_id: str, session_id: str, since_seq: int = -1) -> list[dict[str, Any]]:
        tenant_uuid = self._tenant(tenant_id)
        session_uuid = _as_uuid(session_id, "session", tenant_uuid)
        rows = self._conn.execute(
            "SELECT client_event_id, seq, type, payload, device_ts, server_ts "
            "FROM session_event WHERE tenant_id = %s AND session_id = %s AND seq > %s "
            "ORDER BY seq",
            (tenant_uuid, session_uuid, since_seq),
        ).fetchall()
        return [
            {
                "client_event_id": str(r[0]), "session_id": session_id,
                "seq": r[1], "type": r[2], "payload": r[3],
                "device_ts": r[4].isoformat(), "server_ts": r[5].isoformat(),
            }
            for r in rows
        ]

    # ── Медиа ────────────────────────────────────────────────────────────────

    def init_asset(self, asset: StoredAsset) -> tuple[StoredAsset, bool]:
        tenant_uuid = self._tenant(asset.tenant_id)
        existing = self._conn.execute(
            "SELECT id, upload_state FROM asset WHERE tenant_id = %s AND sha256 = %s",
            (tenant_uuid, bytes.fromhex(asset.sha256)),
        ).fetchone()
        if existing:
            found = self.get_asset(str(existing[0]))
            return found, True
        session_uuid = self._session(tenant_uuid, asset.session_id)
        self._conn.execute(
            "INSERT INTO asset (id, tenant_id, session_id, step_id, kind, mime, "
            " bytes, sha256, capture_meta, upload_state) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')",
            (asset.id, tenant_uuid, session_uuid, asset.step_id, asset.kind,
             asset.mime, asset.bytes, bytes.fromhex(asset.sha256),
             Jsonb(asset.capture_meta)),
        )
        return asset, False

    def get_asset(self, asset_id: str) -> StoredAsset | None:
        try:
            uuid.UUID(asset_id)
        except ValueError:
            return None
        # session_id наружу — внешний реф сессии, не внутренний UUID: ключ
        # файла в бакете обязан быть одинаковым на обоих хранилищах.
        row = self._conn.execute(
            "SELECT a.id, t.code, s.ext_ref, a.step_id, a.kind, a.mime, "
            " a.bytes, a.sha256, a.capture_meta, a.upload_state "
            "FROM asset a JOIN tenant t ON t.id = a.tenant_id "
            "JOIN scan_session s ON s.id = a.session_id WHERE a.id = %s",
            (asset_id,),
        ).fetchone()
        if row is None:
            return None
        chunks = {
            r[0] for r in self._conn.execute(
                "SELECT n FROM asset_chunk WHERE asset_id = %s", (asset_id,)
            ).fetchall()
        }
        return StoredAsset(
            id=str(row[0]), tenant_id=row[1], session_id=str(row[2]),
            step_id=row[3], kind=row[4], mime=row[5], bytes=row[6],
            sha256=row[7].hex(), chunk_size=1 << 20,
            received_chunks=chunks, capture_meta=row[8], upload_state=row[9],
        )

    def put_chunk(self, asset_id: str, n: int, data: bytes) -> None:
        # Идемпотентно по (asset_id, n): повторная загрузка чанка перезаписывает.
        self._conn.execute(
            "INSERT INTO asset_chunk (asset_id, n, data) VALUES (%s,%s,%s) "
            "ON CONFLICT (asset_id, n) DO UPDATE SET data = EXCLUDED.data",
            (asset_id, n, data),
        )
        self._conn.execute(
            "UPDATE asset SET upload_state = 'uploading' WHERE id = %s AND upload_state = 'pending'",
            (asset_id,),
        )

    def asset_payload(self, asset_id: str) -> bytes | None:
        """Собранные байты подтверждённого файла — для оффлоада в облако."""
        state = self._conn.execute(
            "SELECT upload_state, bytes FROM asset WHERE id = %s", (asset_id,)
        ).fetchone()
        if state is None or state[0] != "verified":
            return None
        rows = self._conn.execute(
            "SELECT data FROM asset_chunk WHERE asset_id = %s ORDER BY n", (asset_id,)
        ).fetchall()
        return b"".join(bytes(r[0]) for r in rows)[: state[1]]

    def complete_asset(self, asset_id: str) -> dict[str, Any]:
        meta = self._conn.execute(
            "SELECT bytes, sha256 FROM asset WHERE id = %s", (asset_id,)
        ).fetchone()
        total_bytes, expected_sha = meta[0], meta[1].hex()
        chunk_size = 1 << 20
        expected_chunks = (total_bytes + chunk_size - 1) // chunk_size
        rows = self._conn.execute(
            "SELECT n, data FROM asset_chunk WHERE asset_id = %s ORDER BY n", (asset_id,)
        ).fetchall()
        have = {r[0] for r in rows}
        missing = [n for n in range(expected_chunks) if n not in have]
        if missing:
            return {"status": "missing_chunks", "missing_chunks": missing}
        payload = b"".join(bytes(r[1]) for r in rows)[:total_bytes]
        if hashlib.sha256(payload).hexdigest() != expected_sha:
            # Повтор целиком, а не тихий приём (I-1).
            self._conn.execute("DELETE FROM asset_chunk WHERE asset_id = %s", (asset_id,))
            self._conn.execute(
                "UPDATE asset SET upload_state = 'pending' WHERE id = %s", (asset_id,))
            return {"status": "checksum_mismatch"}
        self._conn.execute(
            "UPDATE asset SET upload_state = 'verified' WHERE id = %s", (asset_id,))
        return {"status": "verified"}

    # ── Трассировка ──────────────────────────────────────────────────────────

    def _stub_item(self, tenant_uuid: str, item_text: str) -> str:
        """Карточка позиции одна на платформу (без тенантного скоупа в UUID):
        заявленная заказчиком и подтверждённая контролёром ссылки обязаны
        висеть на ОДНОЙ карточке — иначе сверка declared↔verified невозможна.
        tenant_id карточки — тенант первого создателя (ON CONFLICT сохраняет)."""
        iid = _as_uuid(item_text, "item")
        self._conn.execute(
            "INSERT INTO item_record (id, ext_ref, tenant_id, task_id, session_id, provenance) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (iid, item_text, tenant_uuid, self._stub_task(tenant_uuid),
             self._session(tenant_uuid, f"item:{item_text}"),
             Jsonb({"source": "trace_stub"})),
        )
        return iid

    def add_trace_link(self, item_id: str, link: dict[str, Any]) -> dict[str, Any]:
        declaring_tenant = self._tenant(link["declared_by_tenant_id"])
        item_uuid = self._stub_item(declaring_tenant, item_id)
        self._conn.execute(
            "INSERT INTO trace_link (id, tenant_id, item_id, code_type, code_value, "
            " supplier_name, supplier_inn, supplier_ref, origin_country, "
            " declared_by_tenant_id, declared_by_user_id, confidence, evidence_asset_ids) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (link["id"], declaring_tenant, item_uuid, link["code_type"],
             link["code_value"], link.get("supplier_name"), link.get("supplier_inn"),
             link.get("supplier_ref"), link.get("origin_country"),
             declaring_tenant,
             self._stub_user(declaring_tenant, link["declared_by_user_id"]),
             link["confidence"],
             [_as_uuid(a, "asset", declaring_tenant)
              for a in link.get("evidence_asset_ids") or []]),
        )
        return link

    # ── Многоуровневый бренд (§06.4) ─────────────────────────────────────────

    def add_brand_ref(self, item_id: str, ref: dict[str, Any]) -> dict[str, Any]:
        from .brand_chain import CONFIDENCE

        tenant = self._tenant(ref["tenant_id"])
        item_uuid = self._stub_item(tenant, item_id)
        existing = self._conn.execute(
            "SELECT id, confidence FROM brand_ref WHERE item_id = %s AND role = %s "
            "AND brand = %s AND article_norm IS NOT DISTINCT FROM %s",
            (item_uuid, ref["role"], ref["brand"], ref.get("article_norm")),
        ).fetchone()
        if existing:
            # Более высокое доверие вытесняет более низкое (см. Store).
            if CONFIDENCE.index(ref["confidence"]) < CONFIDENCE.index(existing[1]):
                self._conn.execute(
                    "UPDATE brand_ref SET confidence = %s, article = %s, level = %s "
                    "WHERE id = %s",
                    (ref["confidence"], ref.get("article"), ref.get("level"), existing[0]),
                )
            return ref
        self._conn.execute(
            "INSERT INTO brand_ref (tenant_id, item_id, brand, article, article_norm, "
            " role, confidence, level, evidence_asset_ids) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (tenant, item_uuid, ref["brand"], ref.get("article"),
             ref.get("article_norm"), ref["role"], ref["confidence"], ref.get("level"),
             [_as_uuid(a, "asset", tenant) for a in ref.get("evidence_asset_ids") or []]),
        )
        return ref

    def brand_refs_of(self, item_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT brand, article, article_norm, role, confidence, level, "
            " evidence_asset_ids FROM brand_ref WHERE item_id = %s ORDER BY created_at",
            (_as_uuid(item_id, "item"),),
        ).fetchall()
        return [
            {"brand": r[0], "article": r[1], "article_norm": r[2], "role": r[3],
             "confidence": r[4], "level": r[5],
             "evidence_asset_ids": [str(a) for a in (r[6] or [])]}
            for r in rows
        ]

    def set_installation(self, item_id: str, nodes: list[dict[str, Any]],
                         key: str | None = None) -> None:
        item_uuid = _as_uuid(item_id, "item")
        tenant = self._conn.execute(
            "SELECT tenant_id FROM item_record WHERE id = %s", (item_uuid,)
        ).fetchone()
        if tenant is None:
            return
        self._conn.execute("DELETE FROM installation_node WHERE item_id = %s", (item_uuid,))
        for node in nodes:
            self._conn.execute(
                "INSERT INTO installation_node (tenant_id, item_id, level, brand, "
                " designation, position, serial, installation_key) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant[0], item_uuid, node["level"], node.get("brand"),
                 node.get("designation"), node.get("position"), node.get("serial"),
                 key if node["level"] == "equipment" else None),
            )

    def items_of_installation(self, tenant_id: str, key: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT i.ext_ref FROM installation_node n "
            "JOIN item_record i ON i.id = n.item_id "
            "WHERE n.tenant_id = %s AND n.installation_key = %s",
            (self._tenant(tenant_id), key),
        ).fetchall()
        return sorted(r[0] for r in rows if r[0])

    def list_installations(self, tenant_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT installation_key, count(DISTINCT item_id) FROM installation_node "
            "WHERE tenant_id = %s AND installation_key IS NOT NULL "
            "GROUP BY installation_key ORDER BY installation_key",
            (self._tenant(tenant_id),),
        ).fetchall()
        return [{"key": r[0], "items": r[1]} for r in rows]

    def installation_of(self, item_id: str) -> list[dict[str, Any]]:
        # Порядок цепочки — часть контракта (установка → узел → деталь),
        # а не случайность выборки: memory отдаёт её отсортированной.
        rows = self._conn.execute(
            "SELECT level, brand, designation, position, serial FROM installation_node "
            "WHERE item_id = %s ORDER BY CASE level WHEN 'equipment' THEN 1 "
            " WHEN 'assembly' THEN 2 ELSE 3 END",
            (_as_uuid(item_id, "item"),),
        ).fetchall()
        return [{"level": r[0], "brand": r[1], "designation": r[2], "position": r[3],
                 "serial": r[4]} for r in rows]

    def find_items_by_article(self, tenant_id: str, article_norm: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT i.ext_ref FROM brand_ref b "
            "JOIN item_record i ON i.id = b.item_id "
            "WHERE b.tenant_id = %s AND b.article_norm = %s",
            (self._tenant(tenant_id), article_norm),
        ).fetchall()
        return sorted(r[0] for r in rows if r[0])

    def item_trace(self, item_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT l.id, l.code_type, l.code_value, l.supplier_name, l.supplier_inn, "
            " l.supplier_ref, l.origin_country, t.code, u.ext_id, "
            " l.confidence, l.evidence_asset_ids, l.created_at "
            "FROM trace_link l "
            "JOIN tenant t ON t.id = l.declared_by_tenant_id "
            "JOIN app_user u ON u.id = l.declared_by_user_id "
            "WHERE l.item_id = %s ORDER BY l.created_at",
            (_as_uuid(item_id, "item"),),
        ).fetchall()
        return [
            {
                "id": str(r[0]), "code_type": r[1], "code_value": r[2],
                "supplier_name": r[3], "supplier_inn": r[4], "supplier_ref": r[5],
                "origin_country": r[6], "declared_by_tenant_id": r[7],
                "declared_by_user_id": r[8], "confidence": r[9],
                "evidence_asset_ids": [str(a) for a in (r[10] or [])],
                "created_at": r[11].isoformat(),
            }
            for r in rows
        ]

    # ── Очередь ручного ревью ────────────────────────────────────────────────

    _REVIEW_COLUMNS = ("SELECT session_ref, reasons, enqueued_at, resolved_at, "
                       " verdict, verdict_by, verdict_comment FROM review_queue ")

    @staticmethod
    def _review_row(r: tuple) -> dict[str, Any]:
        return {
            "session_id": r[0],
            "reasons": r[1],
            "enqueued_at": r[2].isoformat(),
            "resolved_at": r[3].isoformat() if r[3] else None,
            "verdict": r[4],
            "verdict_by": r[5],
            "verdict_comment": r[6],
        }

    def enqueue_review(self, tenant_id: str, session_id: str, reasons: list[str]) -> dict[str, Any]:
        t = self._tenant(tenant_id)
        sid = self._session(t, session_id)
        existing = self._conn.execute(
            "SELECT reasons, resolved_at FROM review_queue "
            "WHERE tenant_id = %s AND session_id = %s", (t, sid),
        ).fetchone()
        if existing and existing[1] is None:
            # Повторное срабатывание приёмки — причины сливаются без дублей.
            merged = list(dict.fromkeys([*existing[0], *reasons]))
            self._conn.execute(
                "UPDATE review_queue SET reasons = %s "
                "WHERE tenant_id = %s AND session_id = %s",
                (Jsonb(merged), t, sid),
            )
        else:
            # Новая запись или переоткрытие после REWORK: вердикт очищается.
            self._conn.execute(
                "INSERT INTO review_queue (tenant_id, session_id, session_ref, reasons) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, session_id) DO UPDATE SET "
                " reasons = EXCLUDED.reasons, enqueued_at = now(), resolved_at = NULL, "
                " verdict = NULL, verdict_by = NULL, verdict_comment = NULL",
                (t, sid, session_id, Jsonb(list(dict.fromkeys(reasons)))),
            )
        return self.get_review(tenant_id, session_id)

    def get_review(self, tenant_id: str, session_id: str) -> dict[str, Any] | None:
        t = self._tenant(tenant_id)
        row = self._conn.execute(
            self._REVIEW_COLUMNS + "WHERE tenant_id = %s AND session_id = %s",
            (t, _as_uuid(session_id, "session", t)),
        ).fetchone()
        return self._review_row(row) if row else None

    def list_review_queue(self, tenant_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            self._REVIEW_COLUMNS +
            "WHERE tenant_id = %s AND resolved_at IS NULL ORDER BY enqueued_at",
            (self._tenant(tenant_id),),
        ).fetchall()
        return [self._review_row(r) for r in rows]

    def list_verdicts(self, tenant_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            self._REVIEW_COLUMNS +
            "WHERE tenant_id = %s AND resolved_at IS NOT NULL ORDER BY resolved_at",
            (self._tenant(tenant_id),),
        ).fetchall()
        return [self._review_row(r) for r in rows]

    # ── Аудит (§07.4): audit.entry — только INSERT, UPDATE/DELETE отозваны ──

    def audit(self, tenant_id: str, actor: str, action: str, target: str,
              details: dict[str, Any] | None = None) -> None:
        t = self._tenant(tenant_id)
        self._conn.execute(
            "INSERT INTO audit.entry (tenant_id, actor_id, action, target, details) "
            "VALUES (%s, %s, %s, %s, %s)",
            (t, self._stub_user(t, actor), action, target, Jsonb(details or {})),
        )

    def list_audit(self, tenant_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT e.at, u.ext_id, e.action, e.target, e.details "
            "FROM audit.entry e JOIN app_user u ON u.id = e.actor_id "
            "WHERE e.tenant_id = %s ORDER BY e.id",
            (self._tenant(tenant_id),),
        ).fetchall()
        return [
            {"at": r[0].isoformat(), "tenant_id": tenant_id, "actor": r[1],
             "action": r[2], "target": r[3], "details": r[4]}
            for r in rows
        ]

    # ── Динамические справочники (снапшоты из внешних систем) ────────────────

    def put_dynamic_directory(self, name: str, sections: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_dynamic_directory(name)
        if existing and existing["sections"] == sections:
            return existing
        version = (existing["version"] + 1) if existing else 1
        doc = {"directory": name, "version": version, "sections": sections}
        self._conn.execute(
            "INSERT INTO directory_snapshot (name, version, doc) VALUES (%s, %s, %s) "
            "ON CONFLICT (name) DO UPDATE SET version = EXCLUDED.version, "
            " doc = EXCLUDED.doc, updated_at = now()",
            (name, version, Jsonb(doc)),
        )
        return doc

    def get_dynamic_directory(self, name: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT doc FROM directory_snapshot WHERE name = %s", (name,),
        ).fetchone()
        return row[0] if row else None

    # ── Очередь экспорта (I-8) ───────────────────────────────────────────────

    @staticmethod
    def _export_row(r: tuple) -> dict[str, Any]:
        return {
            "session_id": r[0],
            "state": r[1],
            "attempts": r[2],
            "next_attempt_at": r[3].isoformat(),
            "last_error": r[4],
            "created_at": r[5].isoformat(),
            "confirmed_at": r[6].isoformat() if r[6] else None,
        }

    _EXPORT_COLUMNS = ("SELECT session_ref, state, attempts, next_attempt_at, "
                       " last_error, created_at, confirmed_at FROM export_queue ")

    def enqueue_export(self, tenant_id: str, session_id: str) -> dict[str, Any]:
        t = self._tenant(tenant_id)
        sid = self._session(t, session_id)
        self._conn.execute(
            "INSERT INTO export_queue (tenant_id, session_id, session_ref) "
            "VALUES (%s, %s, %s) ON CONFLICT (tenant_id, session_id) DO NOTHING",
            (t, sid, session_id),
        )
        return self.get_export(tenant_id, session_id)

    def get_export(self, tenant_id: str, session_id: str) -> dict[str, Any] | None:
        t = self._tenant(tenant_id)
        row = self._conn.execute(
            self._EXPORT_COLUMNS + "WHERE tenant_id = %s AND session_id = %s",
            (t, _as_uuid(session_id, "session", t)),
        ).fetchone()
        return self._export_row(row) if row else None

    def due_exports(self, tenant_id: str, now: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            self._EXPORT_COLUMNS +
            "WHERE tenant_id = %s AND state = 'PENDING' AND next_attempt_at <= %s::timestamptz "
            "ORDER BY created_at",
            (self._tenant(tenant_id), now),
        ).fetchall()
        return [self._export_row(r) for r in rows]

    def confirm_export(self, tenant_id: str, session_id: str) -> None:
        t = self._tenant(tenant_id)
        self._conn.execute(
            "UPDATE export_queue SET state = 'CONFIRMED', confirmed_at = now(), "
            " last_error = NULL "
            "WHERE tenant_id = %s AND session_id = %s AND state = 'PENDING'",
            (t, _as_uuid(session_id, "session", t)),
        )

    def defer_export(self, tenant_id: str, session_id: str, attempts: int,
                     next_attempt_at: str, error: str) -> None:
        t = self._tenant(tenant_id)
        self._conn.execute(
            "UPDATE export_queue SET attempts = %s, next_attempt_at = %s::timestamptz, "
            " last_error = %s "
            "WHERE tenant_id = %s AND session_id = %s AND state = 'PENDING'",
            (attempts, next_attempt_at, error, t, _as_uuid(session_id, "session", t)),
        )

    def dead_letter_export(self, tenant_id: str, session_id: str, error: str) -> None:
        t = self._tenant(tenant_id)
        self._conn.execute(
            "UPDATE export_queue SET state = 'DEAD_LETTER', last_error = %s "
            "WHERE tenant_id = %s AND session_id = %s AND state = 'PENDING'",
            (error, t, _as_uuid(session_id, "session", t)),
        )

    def resolve_review(
        self, tenant_id: str, session_id: str, verdict: str, by: str, comment: str | None = None,
    ) -> dict[str, Any] | None:
        """None — записи нет или она уже решена: решение первого контролёра
        не перезаписывается молча (эндпоинт отдаёт 409)."""
        t = self._tenant(tenant_id)
        row = self._conn.execute(
            "UPDATE review_queue SET verdict = %s, verdict_by = %s, "
            " verdict_comment = %s, resolved_at = now() "
            "WHERE tenant_id = %s AND session_id = %s AND resolved_at IS NULL "
            "RETURNING session_ref, reasons, enqueued_at, resolved_at, "
            " verdict, verdict_by, verdict_comment",
            (verdict, by, comment, t, _as_uuid(session_id, "session", t)),
        ).fetchone()
        return self._review_row(row) if row else None
