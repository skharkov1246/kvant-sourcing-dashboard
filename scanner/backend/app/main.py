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
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .acceptance import AcceptancePipeline, AcceptanceService, TransportUnavailable
from .directories import (
    DirectoryError,
    load_brand_directory,
    load_materials_directory,
    load_media_directory,
    load_series_directory,
    load_si_directory,
    load_sortament_directory,
    load_std_directory,
)
from .export import registry_row
from .model_registry import TenantDataPolicy
from .protocol import auto_accept, check_compatibility, is_complete
from .projections import fold
from .si_check import instrument_flags
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
    state = fold(session_id, events)
    AcceptanceService(ACCEPTANCE_PIPELINE, STORE).process(
        tenant_id, session_id, protocol, state.to_context(),
        TenantDataPolicy(tenant_id=tenant_id, retention_days=30),
    )
    # Проверка СИ (Р-08) — детерминированное правило СТРОЖЕ автоприёма:
    # телефон мог месяц не синхронизировать справочник, сервер проверяет то,
    # что реально пришло. Нарушение отправляет сессию контролёру даже при
    # идеальном вердикте правил и модели.
    completed_at = next(
        (e.get("server_ts") or e.get("device_ts")
         for e in events if e["type"] == "session.completed"), None,
    )
    if completed_at:
        flags = instrument_flags(state, completed_at[:7])
        if flags:
            STORE.enqueue_review(tenant_id, session_id, flags)


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

    if "verdicts" in body.scopes:
        # Оператор видит судьбу своих сессий: «принято/брак/пересъёмка»
        # приезжает тем же pull, что и задания, — без отдельного канала.
        # task_id — чтобы устройство показало комментарий контролёра у
        # задания, не восстанавливая связь сессия→задание самостоятельно.
        for verdict in STORE.list_verdicts(principal.tenant_id):
            changes.append({"type": "verdict.upsert", "data": {
                "session_id": verdict["session_id"],
                "task_id": _session_task_id(principal.tenant_id, verdict["session_id"]),
                "verdict": verdict["verdict"],
                "verdict_comment": verdict["verdict_comment"],
                "resolved_at": verdict["resolved_at"],
            }})

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
        _spawn_service_tasks(principal.tenant_id, session_id)
    return {"results": results, "server_time": utcnow()}


# Служебные исходы kv_no_id → задачи с дедлайном в рабочих днях (Р-11 п.3,
# Р-13 п.5.1). Значения — это ИСХОД работы у детали; задачу создаёт сервер.
_SERVICE_OUTCOME_DEADLINES_BD = {
    "БРЕНД НЕ ОПРЕДЕЛЁН": 10,
    "УСТАНОВКА НЕ ОПРЕДЕЛЕНА": 15,
}


def _spawn_service_tasks(tenant_id: str, session_id: str) -> None:
    import datetime as dt

    from .buffer import WorkCalendar

    events = STORE.session_events(tenant_id, session_id)
    state = fold(session_id, events)
    res = state.results.get("n_outcome")
    outcome = res.data.get("outcome") if res and isinstance(res.data, dict) else None
    days = _SERVICE_OUTCOME_DEADLINES_BD.get(outcome or "")
    if days is None:
        return
    # Идемпотентность по (система, ссылка): повтор батча не плодит задачи.
    external_ref = f"{session_id}:{outcome}"
    if STORE.find_task_by_external(tenant_id, "kvant-scan", external_ref):
        return
    due = WorkCalendar().add_business_days(dt.date.today(), days)
    STORE.upsert_task({
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "site_id": None,
        "external_system": "kvant-scan",
        "external_ref": external_ref,
        # Повторный заход на идентификацию после отработки задачи идёт тем же
        # маршрутом kv_no_id — либо деталь получает бренд и уходит в router.
        "protocol_code": "kv_no_id",
        "protocol_version": None,
        "subject": {"kind": "service_followup", "outcome": outcome,
                    "source_session": session_id},
        "priority": 50,
        "due_at": due.isoformat(),
        "assignee_user_id": None,
        "assignee_pool": "brand_owners" if days == 10 else "site_managers",
        "state": "NEW",
        "created_at": utcnow(),
    })


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
    "sortament": load_sortament_directory,
    # Имена совпадают с source-полями протоколов — маппинг не нужен.
    "media_directory": load_media_directory,
    "brand_directory": load_brand_directory,
    "std_directory": load_std_directory,
    "si_directory": load_si_directory,
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
    # Динамические (снапшоты из внешних систем) — раньше файловых: справочник
    # поставщиков обновляется CRM-опросом, а не релизом данных.
    dynamic = STORE.get_dynamic_directory(name)
    if dynamic is not None:
        etag = f'"{name}-v{dynamic["version"]}"'
        if if_none_match == etag:
            return Response(status_code=304, headers={"ETag": etag})
        response.headers["ETag"] = etag
        return dynamic

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


@app.put("/v1/directories/{name}", tags=["inbound"])
def put_directory(
    name: str,
    body: dict[str, Any] = Body(...),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Загрузка снапшота динамического справочника (поллер CRM).

    Версию считает сервер и растит только при изменении контента —
    ежепятиминутный опрос без изменений не сбрасывает 304-кэш устройств.
    Файловые справочники этим путём не перекрываются: имена файловых
    заняты загрузчиками и динамическая запись под ними запрещена.
    """
    _require_reviewer(principal)
    if name in _DIRECTORIES:
        raise HTTPException(
            status_code=409,
            detail={"code": "reserved_name", "message": "Имя занято файловым справочником"},
        )
    sections = body.get("sections")
    if not isinstance(sections, dict) or not sections:
        raise HTTPException(status_code=422, detail={"code": "bad_sections", "message": "Нужен объект sections"})
    return STORE.put_dynamic_directory(name, sections)


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


@app.get("/v1/review/queue/summary", tags=["review"])
def review_queue_summary(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    """Бейдж дашборда контролёра: сколько открыто и почему.

    Категория причины — префикс до двоеточия («llm_flag», «llm_unavailable»,
    «правила протокола не дали автоприём»): контролёр видит, чем занята
    очередь, не открывая её.
    """
    _require_reviewer(principal)
    items = STORE.list_review_queue(principal.tenant_id)
    by_reason: dict[str, int] = {}
    for entry in items:
        for reason in entry["reasons"]:
            key = reason.split(":", 1)[0].strip()
            by_reason[key] = by_reason.get(key, 0) + 1
    return {
        "open": len(items),
        "by_reason": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])),
        "oldest_enqueued_at": items[0]["enqueued_at"] if items else None,
    }


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
    # Аудит §07.4: вердикт — решение с последствиями (двигает задание,
    # открывает экспорт) и обязан быть восстановим: кто, что, когда.
    STORE.audit(principal.tenant_id, principal.user_id, "review.verdict",
                session_id, {"verdict": body.verdict, "comment": body.comment})
    _apply_verdict_to_task(principal.tenant_id, session_id, body.verdict)
    return entry


def _session_task_id(tenant_id: str, session_id: str) -> str | None:
    events = STORE.session_events(tenant_id, session_id)
    return next(
        (e["payload"].get("task_id") for e in events if e["type"] == "session.started"),
        None,
    )


def _apply_verdict_to_task(tenant_id: str, session_id: str, verdict: str) -> None:
    """Вердикт двигает жизненный цикл задания.

    REWORK возвращает задание в состояние REWORK — на следующем sync_pull
    оно снова окажется на устройстве. ACCEPTED и REJECTED — терминальные
    состояния жизненного цикла задания (schema.sql: scan_task.state); брак —
    тоже результат: он фиксируется в реестре, а не переснимается бесконечно.
    Сессия без задания (ad-hoc скан) ничего не двигает.
    """
    task_id = _session_task_id(tenant_id, session_id)
    if not task_id:
        return
    task = STORE.get_task(tenant_id, task_id)
    if task is None:
        return
    task["state"] = verdict  # вердикты — подмножество состояний задания
    STORE.upsert_task(task)
    # I-8: принятый результат задания из внешней системы едет обратно через
    # очередь экспорта; EXPORTED задание станет только после подтверждения.
    if verdict == "ACCEPTED" and task.get("external_system"):
        STORE.enqueue_export(tenant_id, session_id)


@app.get("/review/dashboard", include_in_schema=False, response_class=HTMLResponse)
def review_dashboard(principal: Principal = Depends(current_principal)) -> HTMLResponse:
    """Мини-дашборд контролёра: сводка + очередь + кнопки вердиктов.

    Дев-инструмент до промышленного OIDC: страница рендерится сервером по
    тем же заголовкам тенанта, что и API; кнопки шлют вердикт fetch'ем с
    теми же заголовками (тенант/пользователь берутся из query, по умолчанию
    локальная сборка). Один самодостаточный HTML без сборки фронта —
    в стиле дашборда репозитория.
    """
    import html as _html

    _require_reviewer(principal)
    items = STORE.list_review_queue(principal.tenant_id)

    by_reason: dict[str, int] = {}
    for entry in items:
        for reason in entry["reasons"]:
            key = reason.split(":", 1)[0].strip()
            by_reason[key] = by_reason.get(key, 0) + 1
    summary_html = "".join(
        f"<span class='pill'>{_html.escape(k)}: {v}</span>"
        for k, v in sorted(by_reason.items(), key=lambda kv: -kv[1])
    ) or "<span class='pill ok'>очередь пуста</span>"

    rows = "".join(
        "<tr><td class='sid'>{sid}</td><td>{reasons}</td><td class='ts'>{ts}</td>"
        "<td class='actions'>"
        "<button onclick=\"verdict('{sid}','ACCEPTED')\">Принять</button>"
        "<button class='warn' onclick=\"verdict('{sid}','REWORK')\">Пересъёмка</button>"
        "<button class='bad' onclick=\"verdict('{sid}','REJECTED')\">Брак</button>"
        "</td></tr>".format(
            sid=_html.escape(entry["session_id"]),
            reasons="<br>".join(_html.escape(r) for r in entry["reasons"]),
            ts=_html.escape(str(entry["enqueued_at"])[:19]),
        )
        for entry in items
    )

    page = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>КВАНТ Скан — очередь ревью</title>
<style>
body{font:14px/1.45 system-ui,sans-serif;margin:24px;background:#f6f7f9;color:#1c2430}
h1{font-size:18px;margin:0 0 12px}
.pill{display:inline-block;background:#e4e9f2;border-radius:12px;padding:2px 10px;margin:0 6px 6px 0;font-size:12px}
.pill.ok{background:#d9efd9}
table{border-collapse:collapse;width:100%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.08)}
th,td{padding:8px 12px;border-bottom:1px solid #e7eaf0;text-align:left;vertical-align:top}
th{background:#eef1f6;font-weight:600}
.sid{font-family:ui-monospace,monospace;font-size:12px}
.ts{white-space:nowrap;color:#68758a;font-size:12px}
.actions button{margin-right:6px;padding:4px 10px;border:1px solid #c6cedb;border-radius:6px;background:#fff;cursor:pointer}
.actions button:hover{background:#eef1f6}
.actions .warn{border-color:#d9a000}.actions .bad{border-color:#c0392b}
#msg{margin:10px 0;color:#c0392b}
</style></head><body>
<h1>Очередь ручного ревью — открыто: __OPEN__</h1>
<div>__SUMMARY__</div><div id="msg"></div>
<table><thead><tr><th>Сессия</th><th>Причины</th><th>Поставлено</th><th>Вердикт</th></tr></thead>
<tbody>__ROWS__</tbody></table>
<script>
const q = new URLSearchParams(location.search);
const H = {"Content-Type": "application/json",
           "X-Tenant-Id": q.get("tenant") || "__TENANT__",
           "X-User-Id": q.get("user") || "__USER__",
           "X-Roles": "operator,reviewer"};
async function verdict(sid, v) {
  const comment = v === "ACCEPTED" ? null : (prompt("Комментарий к вердикту:") || null);
  const r = await fetch(`/v1/review/${encodeURIComponent(sid)}/verdict`,
    {method: "POST", headers: H, body: JSON.stringify({verdict: v, comment})});
  if (r.ok) { location.reload(); }
  else { document.getElementById("msg").textContent =
           `Ошибка ${r.status}: ${(await r.json()).detail?.message || ""}`; }
}
</script></body></html>"""
    page = (page.replace("__OPEN__", str(len(items)))
                .replace("__SUMMARY__", summary_html)
                .replace("__ROWS__", rows)
                .replace("__TENANT__", _html.escape(principal.tenant_id))
                .replace("__USER__", _html.escape(principal.user_id)))
    return HTMLResponse(page)


# ─────────────────────────────────────────────────────────────────────────────
# Экспорт реестра (Р-13)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/v1/export/sessions", tags=["export"])
def export_sessions(
    date_from: str | None = None,
    date_to: str | None = None,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Строки реестра по завершённым сессиям — материал для выгрузки в
    Bitrix/Google Sheets (Р-13). Пока JSON; форматы таблиц — на стороне
    коннекторов.

    В выгрузку попадают только завершённые сессии: реестр — про результаты,
    незаконченная съёмка результатом не является. Фильтр date_from/date_to —
    по серверному времени события завершения (ISO 8601).
    """
    _require_reviewer(principal)
    rows: list[dict[str, Any]] = []
    for session_id in STORE.list_sessions(principal.tenant_id):
        row = registry_row(STORE, principal.tenant_id, session_id)
        if row is None:
            continue
        if date_from and row["completed_at"] < date_from:
            continue
        if date_to and row["completed_at"] > date_to:
            continue
        rows.append(row)
    return {"items": rows, "count": len(rows)}


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


@app.get("/health/ready", include_in_schema=False)
def health_ready(response: Response) -> dict[str, Any]:
    """Readiness для деплоя: жив ли каждый зависимый контур.

    Отличие от /health: тот отвечает «процесс запущен», этот — «можно
    подавать трафик». Балансировщик снимает под с ротации по 503 отсюда,
    не дожидаясь пятисоток у операторов.
    """
    checks: dict[str, Any] = {}

    try:
        checks["directories"] = {
            "ok": True,
            "series_version": load_series_directory()["version"],
            "materials_version": load_materials_directory()["version"],
        }
    except DirectoryError as e:
        checks["directories"] = {"ok": False, "error": str(e)}

    try:
        checks["protocols"] = {"ok": True, "active": len(STORE.list_protocols())}
    except Exception as e:  # noqa: BLE001 — readiness обязан пережить любой сбой
        checks["protocols"] = {"ok": False, "error": str(e)}

    # Хранилище: для PgStore — живой SELECT 1, для памяти — тривиально ок.
    try:
        conn = getattr(STORE, "_conn", None)
        if conn is not None:
            conn.execute("SELECT 1")
        checks["store"] = {"ok": True, "kind": type(STORE).__name__}
    except Exception as e:  # noqa: BLE001
        checks["store"] = {"ok": False, "error": str(e)}

    ready = all(c.get("ok") for c in checks.values())
    if not ready:
        response.status_code = 503
    return {"ready": ready, "checks": checks, "time": utcnow()}
