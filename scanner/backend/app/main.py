"""HTTP-слой платформы «КВАНТ Скан».

Контракт зафиксирован в `docs/api/openapi.yaml`; здесь его исполняемая часть.

Три принципа, ради которых стоит читать этот файл:
  * приём событий отделён от их обработки — тяжёлое уходит в воркеры (§08.4);
  * ответ по каждому событию отдельно, батч не падает целиком (§04.4);
  * идемпотентность на всех входных точках: client_event_id, Idempotency-Key,
    (asset_id, номер чанка).
"""

from __future__ import annotations

import base64
import os
import uuid
from typing import Any, Literal

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Path, Request, Response
from pydantic import BaseModel, Field

from .acceptance import AcceptancePipeline, AcceptanceService, TransportUnavailable
from .directories import DirectoryError, load_materials_directory, load_series_directory
from .model_registry import TenantDataPolicy
from .protocol import auto_accept, check_compatibility, is_complete
from .projections import fold
from .store import Store, StoredAsset, utcnow

app = FastAPI(
    title="КВАНТ Скан API",
    version="1.0.0",
    description="Бэкенд платформы сбора первичных данных о единицах товара на складе.",
)

STORE = Store()

DEFAULT_CHUNK_SIZE = 1 << 20  # 1 МиБ; в проде выбирается по замеру скорости


class NullTransport:
    """Без ключа LLM-плечо честно недоступно: приёмка деградирует в
    MANUAL_REVIEW с причиной, а не притворяется, что модель посмотрела."""

    def complete(self, **_: Any):
        raise TransportUnavailable("ANTHROPIC_API_KEY не настроен")


def _make_transport():
    if os.environ.get("ANTHROPIC_API_KEY"):
        from .acceptance import AnthropicTransport

        return AnthropicTransport()
    return NullTransport()


ACCEPTANCE_PIPELINE = AcceptancePipeline(_make_transport())


def _run_acceptance(tenant_id: str, session_id: str) -> None:
    """Приёмка по завершению сессии.

    Вызывается из приёма событий синхронно: с NullTransport это мгновенно.
    С живым ключом синхронный LLM-вызов в sync_events нарушил бы принцип
    «приём отделён от обработки» (§08.4) — при подключении ключа этот вызов
    уезжает в воркер, контракт (сессия оказывается в очереди) не меняется.
    """
    events = STORE.session_events(tenant_id, session_id)
    protocol_ref = next(
        (e["payload"].get("protocol") for e in events if e["type"] == "session.started"),
        None,
    )
    protocol = None
    if protocol_ref and "@" in protocol_ref:
        code, version = protocol_ref.split("@", 1)
        protocol = STORE.get_protocol(code, int(version))
    if protocol is None:
        # Решать нечего и не по чему — сразу к контролёру, а не в никуда.
        STORE.enqueue_review(tenant_id, session_id, ["протокол сессии не определён"])
        return
    ctx = fold(session_id, events).to_context()
    AcceptanceService(ACCEPTANCE_PIPELINE, STORE).process(
        tenant_id, session_id, protocol, ctx,
        TenantDataPolicy(tenant_id=tenant_id, retention_days=30),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Аутентификация (заглушка контракта)
# ─────────────────────────────────────────────────────────────────────────────

class Principal(BaseModel):
    tenant_id: str
    user_id: str
    device_id: str | None = None
    roles: list[str] = Field(default_factory=list)

    @property
    def is_external(self) -> bool:
        """Сотрудник заказчика: может заявлять трассировку, но не верифицировать."""
        return "reviewer" not in self.roles and "platform_admin" not in self.roles


def current_principal(
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_roles: str | None = Header(default=None, alias="X-Roles"),
) -> Principal:
    """Разбор субъекта.

    В промышленной сборке здесь проверка подписи OIDC-токена и выставление
    `app.tenant_id` в транзакции PostgreSQL (рубеж 1, §07.2). Для локального
    запуска и тестов принимаются заголовки — это не путь для прода и должно
    отключаться флагом среды.
    """
    if not (authorization or x_tenant_id):
        raise HTTPException(status_code=401, detail={"code": "unauthenticated", "message": "Нет учётных данных"})
    return Principal(
        tenant_id=x_tenant_id or "internal",
        user_id=x_user_id or "unknown",
        roles=(x_roles or "operator").split(","),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Модели запросов и ответов
# ─────────────────────────────────────────────────────────────────────────────

class DeviceCapabilities(BaseModel):
    schema_version: int
    app_version: str
    platform: Literal["android", "ios"] = "android"
    depth: Literal["lidar", "ar_depth", "none"] = "none"
    max_accuracy_class: Literal["A", "B", "C", "D"] = "D"
    camera_mp: float = 8.0
    free_storage_mb: int = 4096


class SyncPullRequest(BaseModel):
    cursor: str | None = None
    device_id: str
    capabilities: DeviceCapabilities
    scopes: list[str] = Field(default_factory=lambda: ["tasks", "protocols"])


class ClientEvent(BaseModel):
    client_event_id: str
    session_id: str
    seq: int
    type: str
    device_ts: str
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None


class EventBatchRequest(BaseModel):
    device_id: str
    events: list[ClientEvent] = Field(min_length=1, max_length=1000)


class AssetInitRequest(BaseModel):
    session_id: str
    step_id: str
    kind: Literal["photo", "video", "depth_map", "point_cloud", "audio"]
    mime: str
    bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capture_meta: dict[str, Any] = Field(default_factory=dict)


class InboundTaskRequest(BaseModel):
    external_system: str
    external_ref: str
    site_code: str | None = None
    protocol_code: str
    subject: dict[str, Any]
    priority: int = 100
    due_at: str | None = None
    assignee: dict[str, Any] | None = None


class TraceLinkInput(BaseModel):
    code_type: Literal["gtin", "sscc", "serial", "batch", "chestny_znak", "gtd", "custom"]
    code_value: str
    supplier_name: str | None = None
    supplier_inn: str | None = None
    supplier_ref: str | None = None
    origin_country: str | None = Field(default=None, min_length=2, max_length=2)
    evidence_asset_ids: list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Синхронизация
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/v1/sync/pull", tags=["sync"])
def sync_pull(body: SyncPullRequest, principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    """Дельта изменений с момента курсора.

    Возможности устройства используются здесь, а не на клиенте: несовместимость
    должна отсекаться при раздаче заданий, а не всплывать у кладовщика в проходе
    между стеллажами (§03.7, правило 4).
    """
    changes: list[dict[str, Any]] = []

    if "protocols" in body.scopes:
        for protocol in STORE.list_protocols():
            if protocol["schema_version"] <= body.capabilities.schema_version:
                changes.append({"type": "protocol.upsert", "data": protocol})

    if "tasks" in body.scopes:
        for task in STORE.list_tasks(principal.tenant_id):
            if task["state"] not in ("NEW", "ASSIGNED", "IN_PROGRESS", "REWORK"):
                continue
            protocol = STORE.get_protocol(task["protocol_code"], task.get("protocol_version"))
            if protocol is None:
                continue
            compat = check_compatibility(protocol, body.capabilities.schema_version)
            if not compat["ok"]:
                continue
            if not _device_can_measure(protocol, body.capabilities.max_accuracy_class):
                continue
            changes.append({"type": "task.upsert", "data": task})

    return {
        "changes": changes,
        "next_cursor": base64.urlsafe_b64encode(utcnow().encode()).decode(),
        "has_more": False,
        "server_time": utcnow(),
    }


def _device_can_measure(protocol: dict[str, Any], device_max_class: str) -> bool:
    """Класс D доступен всегда, поэтому задание отсекается только при жёстком block."""
    from .protocol import accuracy_at_least

    for step in protocol.get("steps", []):
        spec = step.get("measure")
        if not spec or not step.get("required", True):
            continue
        if spec.get("on_violation") != "block":
            continue
        if not accuracy_at_least(device_max_class, spec.get("min_accuracy_class", "C")):
            return False
    return True


@app.post("/v1/sync/events", tags=["sync"])
def sync_events(
    body: EventBatchRequest,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Приём батча событий.

    Валидируем, пишем в лог, отвечаем. Никакой тяжёлой работы здесь: пик в
    пересменку (вся смена одновременно поймала Wi-Fi) не должен ронять приём.
    """
    results: list[dict[str, Any]] = []
    completed_sessions: list[str] = []
    for event in body.events:
        outcome = STORE.append_event(principal.tenant_id, event.model_dump())
        if outcome == "accepted":
            results.append({"client_event_id": event.client_event_id, "status": "accepted"})
            if event.type == "session.completed":
                completed_sessions.append(event.session_id)
        elif outcome == "duplicate":
            # Повтор после потери ответа — успех, а не ошибка.
            results.append({"client_event_id": event.client_event_id, "status": "duplicate"})
        else:
            results.append({
                "client_event_id": event.client_event_id,
                "status": "rejected",
                "error": {
                    "code": "conflicting_duplicate",
                    "message": "Тот же client_event_id уже принят с другим содержимым",
                    "retryable": False,
                },
            })
    # Приёмка стартует после записи ВСЕГО батча: завершение может прийти
    # в одном батче с последними шагами (и даже раньше их — см. проекцию).
    for session_id in completed_sessions:
        _run_acceptance(principal.tenant_id, session_id)
    return {"results": results, "server_time": utcnow()}


@app.get("/v1/sync/sessions/{session_id}/log", tags=["sync"])
def session_log(
    session_id: str = Path(...),
    since_seq: int = -1,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Лог сессии для продолжения работы на другом устройстве (§04.6)."""
    events = STORE.session_events(principal.tenant_id, session_id, since_seq)
    if not events:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Сессия не найдена"})
    return {"session_id": session_id, "events": events}


@app.get("/v1/sessions/{session_id}/state", tags=["sync"])
def session_state(
    session_id: str = Path(...),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Проекция сессии + решение об автоприёме."""
    events = STORE.session_events(principal.tenant_id, session_id)
    if not events:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Сессия не найдена"})
    state = fold(session_id, events)
    ctx = state.to_context()

    protocol_ref = next(
        (e["payload"].get("protocol") for e in events if e["type"] == "session.started"),
        None,
    )
    protocol = None
    if protocol_ref and "@" in protocol_ref:
        code, version = protocol_ref.split("@", 1)
        protocol = STORE.get_protocol(code, int(version))

    return {
        "session_id": session_id,
        "state": state.state,
        "last_seq": state.last_seq,
        "quality_score": round(ctx.quality_score, 3),
        "steps": {k: v.status for k, v in state.results.items()},
        "asset_ids": sorted(state.asset_ids),
        "complete": is_complete(protocol, ctx) if protocol else None,
        "auto_accept": auto_accept(protocol, ctx) if protocol else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Медиа
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/v1/assets", status_code=201, tags=["assets"])
def init_asset(body: AssetInitRequest, principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    asset = StoredAsset(
        id=str(uuid.uuid4()),
        tenant_id=principal.tenant_id,
        session_id=body.session_id,
        step_id=body.step_id,
        kind=body.kind,
        mime=body.mime,
        bytes=body.bytes,
        sha256=body.sha256,
        chunk_size=DEFAULT_CHUNK_SIZE,
        capture_meta=body.capture_meta,
    )
    stored, deduplicated = STORE.init_asset(asset)
    return {
        "asset_id": stored.id,
        "chunk_size": stored.chunk_size,
        "deduplicated": deduplicated,
        "upload_url": None,
    }


@app.put("/v1/assets/{asset_id}/chunks/{n}", status_code=204, tags=["assets"])
async def put_chunk(request: Request, asset_id: str, n: int, _: Principal = Depends(current_principal)) -> Response:
    """Идемпотентно по паре (asset_id, n) — повторная загрузка чанка безопасна."""
    if STORE.get_asset(asset_id) is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Актив не найден"})
    STORE.put_chunk(asset_id, n, await request.body())
    return Response(status_code=204)


@app.post("/v1/assets/{asset_id}/complete", tags=["assets"])
def complete_asset(asset_id: str, _: Principal = Depends(current_principal)) -> dict[str, Any]:
    if STORE.get_asset(asset_id) is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Актив не найден"})
    return STORE.complete_asset(asset_id)


@app.get("/v1/assets/{asset_id}/status", tags=["assets"])
def asset_status(asset_id: str, _: Principal = Depends(current_principal)) -> dict[str, Any]:
    asset = STORE.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Актив не найден"})
    return {
        "asset_id": asset.id,
        "upload_state": asset.upload_state,
        "received_chunks": sorted(asset.received_chunks),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Задания и протоколы
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/v1/tasks", tags=["tasks"])
def list_tasks(state: str | None = None, principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    return {"items": STORE.list_tasks(principal.tenant_id, state=state), "next_cursor": None}


@app.get("/v1/tasks/{task_id}", tags=["tasks"])
def get_task(task_id: str, principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    task = STORE.get_task(principal.tenant_id, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Задание не найдено"})
    return task


@app.get("/v1/protocols/{code}/{version}", tags=["protocols"])
def get_protocol(code: str, version: int) -> dict[str, Any]:
    protocol = STORE.get_protocol(code, version)
    if protocol is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Протокол не найден"})
    return protocol


# Реестр справочников: имя → загрузчик. Новый справочник = строка здесь,
# файл в data/directories/ и ничего больше (Р-03 разд. 6).
_DIRECTORIES = {
    "standard_series": load_series_directory,
    "materials": load_materials_directory,
}


@app.get("/v1/directories/{name}", tags=["sync"])
def get_directory(
    name: str,
    response: Response,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    _: Principal = Depends(current_principal),
) -> Any:
    """Раздача справочника на устройство с валидацией по ETag.

    ETag считается из версии справочника: устройство на каждой синхронизации
    спрашивает с If-None-Match и в обычном случае получает 304 без тела —
    таблицы стандартов меняются редко, гонять их каждый раз незачем.
    """
    loader = _DIRECTORIES.get(name)
    if loader is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Справочник не найден"})
    try:
        doc = loader()
    except DirectoryError as e:
        # Сломанный справочник — ошибка развёртывания: честная 500, а не
        # тихая раздача устаревшей копии.
        raise HTTPException(status_code=500, detail={"code": "directory_broken", "message": str(e)})
    etag = f'"{name}-v{doc["version"]}"'
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return doc


@app.post("/v1/inbound/tasks", status_code=202, tags=["inbound"])
def inbound_task(
    body: InboundTaskRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Приём задания из внешней складской системы.

    Повторная доставка вебхука (а она будет — все шлют с ретраями) возвращает
    тот же task_id и не создаёт дубль (§09.2).
    """
    existing_id = STORE.lookup_idempotency(principal.tenant_id, idempotency_key)
    if existing_id:
        existing_task = STORE.get_task(principal.tenant_id, existing_id)
        return {"task_id": existing_id, "state": existing_task["state"], "deduplicated": True}

    existing = STORE.find_task_by_external(principal.tenant_id, body.external_system, body.external_ref)
    if existing:
        STORE.remember_idempotency(principal.tenant_id, idempotency_key, existing["id"])
        return {"task_id": existing["id"], "state": existing["state"], "deduplicated": True}

    protocol = STORE.get_protocol(body.protocol_code)
    if protocol is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "unknown_protocol", "message": "Нет активной версии протокола", "field": "protocol_code"},
        )

    task = {
        "id": str(uuid.uuid4()),
        "tenant_id": principal.tenant_id,
        "site_id": None,
        "external_system": body.external_system,
        "external_ref": body.external_ref,
        "protocol_code": body.protocol_code,
        "protocol_version": None,
        "subject": body.subject,
        "priority": body.priority,
        "due_at": body.due_at,
        "assignee_user_id": None,
        "assignee_pool": (body.assignee or {}).get("value"),
        "state": "NEW",
        "created_at": utcnow(),
    }
    STORE.upsert_task(task)
    STORE.remember_idempotency(principal.tenant_id, idempotency_key, task["id"])
    return {"task_id": task["id"], "state": task["state"], "deduplicated": False}


# ─────────────────────────────────────────────────────────────────────────────
# Очередь ручного ревью
# ─────────────────────────────────────────────────────────────────────────────

class ReviewVerdictRequest(BaseModel):
    verdict: Literal["ACCEPTED", "REJECTED", "REWORK"]
    comment: str | None = None


def _require_reviewer(principal: Principal) -> None:
    if principal.is_external:
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "Ревью доступно только роли reviewer"},
        )


@app.get("/v1/review/queue", tags=["review"])
def review_queue(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    """Открытые сессии на ручной проверке.

    Сюда сессии ставит приёмка (acceptance.py) вердиктом MANUAL_REVIEW —
    очередь и есть то место, где «правила решают, LLM только флажит»
    заканчивается решением человека.
    """
    _require_reviewer(principal)
    return {"items": STORE.list_review_queue(principal.tenant_id)}


@app.post("/v1/review/{session_id}/verdict", tags=["review"])
def review_verdict(
    session_id: str,
    body: ReviewVerdictRequest,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Вердикт контролёра. Повторный вердикт — 409: решение первого
    контролёра не перезаписывается молча."""
    _require_reviewer(principal)
    entry = STORE.resolve_review(
        principal.tenant_id, session_id, body.verdict, principal.user_id, body.comment,
    )
    if entry is None:
        existing = STORE.get_review(principal.tenant_id, session_id)
        if existing is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Сессии нет в очереди ревью"})
        raise HTTPException(
            status_code=409,
            detail={"code": "already_resolved", "message": f"Вердикт уже вынесен: {existing['verdict']}"},
        )
    return entry


# ─────────────────────────────────────────────────────────────────────────────
# Трассировка
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/v1/items/{item_id}/trace", status_code=201, tags=["items"])
def add_trace(
    item_id: str,
    body: TraceLinkInput,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Внесение трассировки.

    Уровень доверия НЕ принимается от клиента: внешний сотрудник заказчика
    заявляет (`declared`), верифицировать может только контролёр или машинная
    сверка с реестром (инвариант I-7, §06.1).
    """
    link = {
        "id": str(uuid.uuid4()),
        **body.model_dump(),
        "confidence": "declared" if principal.is_external else "verified",
        "declared_by_tenant_id": principal.tenant_id,
        "declared_by_user_id": principal.user_id,
        "created_at": utcnow(),
    }
    return STORE.add_trace_link(item_id, link)


@app.get("/v1/items/{item_id}/trace", tags=["items"])
def get_trace(item_id: str, _: Principal = Depends(current_principal)) -> dict[str, Any]:
    return {"item_id": item_id, "links": STORE.item_trace(item_id)}


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok", "time": utcnow()}
