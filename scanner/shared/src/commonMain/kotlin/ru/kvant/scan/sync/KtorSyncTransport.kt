package ru.kvant.scan.sync

import io.ktor.client.HttpClient
import io.ktor.client.request.HttpRequestBuilder
import io.ktor.client.request.get
import io.ktor.client.request.header
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.client.statement.HttpResponse
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.HttpStatusCode
import io.ktor.http.contentType
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

/**
 * Конфигурация подключения устройства к серверу.
 *
 * Заголовки тенанта — контракт локальной сборки бэкенда (main.py,
 * current_principal); в промышленной сборке их заменяет OIDC-токен в
 * Authorization, поле [bearerToken] уже предусмотрено.
 */
data class SyncConfig(
    val baseUrl: String,
    val tenantId: String,
    val userId: String,
    val deviceId: String,
    val roles: String = "operator",
    val bearerToken: String? = null,
    /** Тело capabilities для /v1/sync/pull (§03.7: фильтрует сервер, не клиент). */
    val capabilities: JsonObject,
)

/**
 * Боевая реализация [SyncTransport] на Ktor — одна на обе платформы
 * (движок клиента платформенный: OkHttp/Darwin, логика — здесь).
 *
 * Единственное преобразование формата: device_ts события хранится в ядре
 * как epoch-миллисекунды ([ru.kvant.scan.domain.Instant]), а контракт API
 * требует ISO-8601 — конвертация происходит на границе, как с миллиметрами
 * в обмере.
 */
class KtorSyncTransport(
    private val client: HttpClient,
    private val config: SyncConfig,
) : SyncTransport {

    private val json = Json { ignoreUnknownKeys = true }

    override suspend fun sendEvents(events: List<ClientEvent>): List<EventResult> {
        val body = buildJsonObject {
            put("device_id", config.deviceId)
            put("events", buildJsonArray { events.forEach { add(eventJson(it)) } })
        }
        val response = execute {
            client.post("${config.baseUrl}/v1/sync/events") {
                commonHeaders()
                contentType(ContentType.Application.Json)
                setBody(body.toString())
            }
        }
        val doc = json.parseToJsonElement(response.bodyAsText()).jsonObject
        return (doc["results"] ?: error("нет results в ответе")).jsonArray.map {
            json.decodeFromJsonElement(EventResult.serializer(), it)
        }
    }

    override suspend fun pull(cursor: String?): PullResult {
        val body = buildJsonObject {
            cursor?.let { put("cursor", it) }
            put("device_id", config.deviceId)
            put("capabilities", config.capabilities)
            put("scopes", buildJsonArray {
                add(JsonPrimitive("tasks")); add(JsonPrimitive("protocols"))
                add(JsonPrimitive("verdicts"))
            })
        }
        val response = execute {
            client.post("${config.baseUrl}/v1/sync/pull") {
                commonHeaders()
                contentType(ContentType.Application.Json)
                setBody(body.toString())
            }
        }
        val doc = json.parseToJsonElement(response.bodyAsText()).jsonObject
        return PullResult(
            changes = (doc["changes"]?.jsonArray ?: emptyList()).map {
                val change = it.jsonObject
                Change(
                    type = change["type"]?.jsonPrimitive?.content ?: "unknown",
                    data = change["data"]?.jsonObject ?: JsonObject(emptyMap()),
                )
            },
            nextCursor = doc["next_cursor"]?.jsonPrimitive?.content ?: "",
            hasMore = doc["has_more"]?.jsonPrimitive?.booleanOrNull ?: false,
        )
    }

    /**
     * Заявленная трассировка с устройства (POST /v1/items/{id}/trace).
     * Уровень доверия НЕ передаётся: сервер сам ставит declared/verified по
     * роли (I-7) — клиент физически не может заявить «проверено».
     */
    suspend fun addTraceLink(
        itemId: String,
        codeType: String,
        codeValue: String,
        supplierName: String? = null,
        supplierInn: String? = null,
        supplierRef: String? = null,
    ): Boolean {
        val body = buildJsonObject {
            put("code_type", codeType)
            put("code_value", codeValue)
            supplierName?.let { put("supplier_name", it) }
            supplierInn?.let { put("supplier_inn", it) }
            supplierRef?.let { put("supplier_ref", it) }
        }
        val response = execute {
            client.post("${config.baseUrl}/v1/items/$itemId/trace") {
                commonHeaders()
                contentType(ContentType.Application.Json)
                setBody(body.toString())
            }
        }
        return response.status.value == 201
    }

    override suspend fun fetchDirectory(name: String, etag: String?): DirectoryResponse {
        val response = execute(allowNotModified = true) {
            client.get("${config.baseUrl}/v1/directories/$name") {
                commonHeaders()
                etag?.let { header("If-None-Match", it) }
            }
        }
        if (response.status == HttpStatusCode.NotModified) {
            return DirectoryResponse(notModified = true, etag = etag)
        }
        return DirectoryResponse(
            notModified = false,
            etag = response.headers["ETag"],
            body = response.bodyAsText(),
        )
    }

    // ── Внутреннее ───────────────────────────────────────────────────────────

    private fun eventJson(event: ClientEvent): JsonObject {
        val encoded = json.encodeToJsonElement(ClientEvent.serializer(), event).jsonObject
        // Instant ядра — epoch ms; контракт API — ISO-8601 (openapi.yaml).
        val iso = kotlinx.datetime.Instant
            .fromEpochMilliseconds(event.deviceTs.epochMillis).toString()
        return JsonObject(encoded + ("device_ts" to JsonPrimitive(iso)))
    }

    private fun HttpRequestBuilder.commonHeaders() {
        config.bearerToken?.let { header("Authorization", "Bearer $it") }
        header("X-Tenant-Id", config.tenantId)
        header("X-User-Id", config.userId)
        header("X-Roles", config.roles)
    }

    private suspend fun execute(
        allowNotModified: Boolean = false,
        block: suspend () -> HttpResponse,
    ): HttpResponse {
        val response = try {
            block()
        } catch (e: Exception) {
            // Любая сетевая/TLS-ошибка — ретраится движком синхронизации.
            throw TransportException(e.message ?: "сеть недоступна", e)
        }
        if (allowNotModified && response.status == HttpStatusCode.NotModified) return response
        if (response.status.value !in 200..299) {
            throw TransportException("HTTP ${response.status.value} от сервера синхронизации")
        }
        return response
    }
}
