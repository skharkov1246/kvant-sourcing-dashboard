package ru.kvant.scan.sync

import io.ktor.client.HttpClient
import io.ktor.client.request.HttpRequestBuilder
import io.ktor.client.request.get
import io.ktor.client.request.header
import io.ktor.client.request.post
import io.ktor.client.request.put
import io.ktor.client.request.setBody
import io.ktor.client.statement.HttpResponse
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.contentType
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import ru.kvant.scan.util.Sha256

/**
 * Медиа-канал: загрузка ассета чанками (§04.2, инвариант I-1).
 *
 * Протокол: init (сервер даёт asset_id и размер чанка; повтор того же
 * содержимого дедуплицируется по sha256 мгновенно) → PUT чанков (докачка
 * с места обрыва: перед отправкой спрашиваем /status и шлём только
 * недостающие) → complete (сервер сверяет sha256 целиком; несовпадение —
 * повтор, а не тихий приём битого файла).
 */
class AssetUploader(
    private val client: HttpClient,
    private val config: SyncConfig,
) {

    data class Meta(
        val sessionId: String,
        val stepId: String,
        val kind: String,   // photo | video | depth_map | point_cloud | audio
        val mime: String,
    )

    class IntegrityException(message: String) : Exception(message)

    private val json = Json { ignoreUnknownKeys = true }

    /** Возвращает asset_id. Повторный вызов с теми же байтами безопасен. */
    suspend fun upload(meta: Meta, bytes: ByteArray): String {
        require(bytes.isNotEmpty()) { "Пустой ассет не загружается" }
        val sha = Sha256.hex(bytes)

        val init = execute {
            client.post("${config.baseUrl}/v1/assets") {
                commonHeaders()
                contentType(ContentType.Application.Json)
                setBody(buildJsonObject {
                    put("session_id", meta.sessionId)
                    put("step_id", meta.stepId)
                    put("kind", meta.kind)
                    put("mime", meta.mime)
                    put("bytes", bytes.size)
                    put("sha256", sha)
                }.toString())
            }
        }
        val initDoc = json.parseToJsonElement(init.bodyAsText()).jsonObject
        val assetId = initDoc["asset_id"]!!.jsonPrimitive.content
        if (initDoc["deduplicated"]?.jsonPrimitive?.booleanOrNull == true) {
            // Тот же кадр уже на сервере — байты не гоняются (§04.4).
            return assetId
        }
        val chunkSize = initDoc["chunk_size"]!!.jsonPrimitive.intOrNull
            ?: throw TransportException("сервер не назвал размер чанка")

        // Докачка с места обрыва: только недостающие чанки.
        val received = receivedChunks(assetId)
        val total = (bytes.size + chunkSize - 1) / chunkSize
        for (n in 0 until total) {
            if (n in received) continue
            val from = n * chunkSize
            val to = minOf(from + chunkSize, bytes.size)
            execute {
                client.put("${config.baseUrl}/v1/assets/$assetId/chunks/$n") {
                    commonHeaders()
                    contentType(ContentType.Application.OctetStream)
                    setBody(bytes.copyOfRange(from, to))
                }
            }
        }

        val complete = execute {
            client.post("${config.baseUrl}/v1/assets/$assetId/complete") { commonHeaders() }
        }
        val status = json.parseToJsonElement(complete.bodyAsText())
            .jsonObject["status"]?.jsonPrimitive?.content
        if (status != "verified") {
            // checksum_mismatch: сервер сбросил приём — вызывающий повторяет
            // upload целиком, битый файл не притворяется принятым (I-1).
            throw IntegrityException("ассет не подтверждён сервером: $status")
        }
        return assetId
    }

    private suspend fun receivedChunks(assetId: String): Set<Int> {
        val response = execute {
            client.get("${config.baseUrl}/v1/assets/$assetId/status") { commonHeaders() }
        }
        val doc = json.parseToJsonElement(response.bodyAsText()).jsonObject
        return (doc["received_chunks"]?.jsonArray ?: emptyList())
            .mapNotNull { it.jsonPrimitive.intOrNull }.toSet()
    }

    private fun HttpRequestBuilder.commonHeaders() {
        config.bearerToken?.let { header("Authorization", "Bearer $it") }
        header("X-Tenant-Id", config.tenantId)
        header("X-User-Id", config.userId)
        header("X-Roles", config.roles)
    }

    private suspend fun execute(block: suspend () -> HttpResponse): HttpResponse {
        val response = try {
            block()
        } catch (e: Exception) {
            throw TransportException(e.message ?: "сеть недоступна", e)
        }
        if (response.status.value !in 200..299) {
            throw TransportException("HTTP ${response.status.value} от сервера медиа")
        }
        return response
    }
}
