package ru.kvant.scan.integration

import io.ktor.client.HttpClient
import io.ktor.client.engine.cio.CIO
import io.ktor.client.request.get
import io.ktor.client.request.header
import io.ktor.client.statement.bodyAsText
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import ru.kvant.scan.domain.Instant
import ru.kvant.scan.sync.ClientEvent
import ru.kvant.scan.sync.EventAck
import ru.kvant.scan.sync.EventType
import ru.kvant.scan.sync.KtorSyncTransport
import ru.kvant.scan.sync.SyncConfig
import java.net.Socket
import java.util.UUID
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * Смок против ЖИВОГО бэкенда (uvicorn на 127.0.0.1:8077): устройство и
 * сервер собраны из одного PR, но до этого теста никогда не разговаривали
 * реальным HTTP. Проверяется весь путь: событие через KtorSyncTransport →
 * приёмка (без ключа честно llm_unavailable) → очередь контролёра; плюс
 * живой ETag-цикл справочника.
 *
 * Без запущенного сервера тест честно выходит с пометкой — это интеграция,
 * а не юнит; поднять сервер: `python -m uvicorn app.main:app --port 8077`.
 */
class LiveBackendSmokeTest {

    private val baseUrl = "http://127.0.0.1:8077"

    private fun serverIsUp(): Boolean = try {
        Socket("127.0.0.1", 8077).use { true }
    } catch (_: Exception) {
        false
    }

    private fun transport(client: HttpClient) = KtorSyncTransport(
        client,
        SyncConfig(
            baseUrl = baseUrl,
            tenantId = "t-internal",
            userId = "u-smoke",
            deviceId = "d-smoke",
            capabilities = Json.parseToJsonElement(
                """{"schema_version": 1, "app_version": "0.1.0"}"""
            ).jsonObject,
        ),
    )

    @Test
    fun `событие с устройства доезжает до очереди контролёра живым HTTP`() = runTest {
        if (!serverIsUp()) {
            println("SMOKE SKIPPED: uvicorn на :8077 не запущен")
            return@runTest
        }
        val client = HttpClient(CIO)
        val transport = transport(client)
        val sessionId = "s-smoke-${UUID.randomUUID()}"

        val results = transport.sendEvents(listOf(
            ClientEvent(
                clientEventId = UUID.randomUUID().toString(),
                sessionId = sessionId, seq = 1,
                type = EventType.SESSION_STARTED,
                deviceTs = Instant(System.currentTimeMillis()),
            ),
            ClientEvent(
                clientEventId = UUID.randomUUID().toString(),
                sessionId = sessionId, seq = 2,
                type = EventType.SESSION_COMPLETED,
                deviceTs = Instant(System.currentTimeMillis()),
            ),
        ))
        assertEquals(listOf(EventAck.ACCEPTED, EventAck.ACCEPTED), results.map { it.status })

        // Сервер без ключа LLM обязан отдать сессию человеку, не потерять.
        val queue = client.get("$baseUrl/v1/review/queue") {
            header("X-Tenant-Id", "t-internal")
            header("X-User-Id", "u-rev")
            header("X-Roles", "operator,reviewer")
        }.bodyAsText()
        val sessions = Json.parseToJsonElement(queue).jsonObject["items"]!!
            .jsonArray.map { it.jsonObject["session_id"]!!.jsonPrimitive.content }
        assertTrue(sessionId in sessions, "сессии нет в очереди контролёра: $sessions")
    }

    @Test
    fun `живой ETag-цикл справочника - вторая загрузка обходится в 304`() = runTest {
        if (!serverIsUp()) {
            println("SMOKE SKIPPED: uvicorn на :8077 не запущен")
            return@runTest
        }
        val transport = transport(HttpClient(CIO))
        val first = transport.fetchDirectory("standard_series", etag = null)
        assertFalse(first.notModified)
        assertTrue(first.body!!.contains("gost6636_ra40_decade"))
        val second = transport.fetchDirectory("standard_series", first.etag)
        assertTrue(second.notModified)
    }
}
