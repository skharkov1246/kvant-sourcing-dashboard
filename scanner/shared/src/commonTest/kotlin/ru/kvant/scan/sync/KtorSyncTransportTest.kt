package ru.kvant.scan.sync

import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.MockRequestHandleScope
import io.ktor.client.engine.mock.respond
import io.ktor.client.request.HttpRequestData
import io.ktor.client.request.HttpResponseData
import io.ktor.content.TextContent
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import ru.kvant.scan.domain.Instant
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * Транспорт против мока сервера: заголовки тенанта, ISO-конвертация
 * device_ts на границе, разбор ответов, честные TransportException.
 */
class KtorSyncTransportTest {

    private val config = SyncConfig(
        baseUrl = "https://api.test",
        tenantId = "t-internal",
        userId = "u-1",
        deviceId = "d-1",
        capabilities = Json.parseToJsonElement(
            """{"schema_version": 1, "app_version": "0.1.0"}"""
        ).jsonObject,
    )

    private fun transport(
        handler: suspend MockRequestHandleScope.(HttpRequestData) -> HttpResponseData,
    ) = KtorSyncTransport(HttpClient(MockEngine { request -> handler(request) }), config)

    private fun event(seq: Long) = ClientEvent(
        clientEventId = "e-$seq",
        sessionId = "s-1",
        seq = seq,
        type = EventType.STEP_COMPLETED,
        deviceTs = Instant(1_753_700_000_000),  // 2025-07-28T…Z
    )

    @Test
    fun `sendEvents шлёт заголовки тенанта и ISO device_ts`() = runTest {
        var captured: HttpRequestData? = null
        val transport = transport { request ->
            captured = request
            respond(
                """{"results": [{"client_event_id": "e-1", "status": "accepted"}]}""",
                headers = headersOf(HttpHeaders.ContentType, "application/json"),
            )
        }
        val results = transport.sendEvents(listOf(event(1)))

        assertEquals(EventAck.ACCEPTED, results.single().status)
        val request = captured ?: error("запрос не отправлен")
        assertEquals("https://api.test/v1/sync/events", request.url.toString())
        assertEquals("t-internal", request.headers["X-Tenant-Id"])
        val sent = Json.parseToJsonElement((request.body as TextContent).text).jsonObject
        val deviceTs = sent["events"]!!.jsonArray.single()
            .jsonObject["device_ts"]!!.jsonPrimitive.content
        // Ядро хранит epoch ms — на провод уходит ISO-8601 (контракт API).
        assertTrue(deviceTs.startsWith("2025-07-28T"), deviceTs)
    }

    @Test
    fun `rejected с ошибкой доезжает до движка целиком`() = runTest {
        val transport = transport {
            respond(
                """{"results": [{"client_event_id": "e-1", "status": "rejected",
                    "error": {"code": "conflicting_duplicate",
                              "message": "конфликт", "retryable": false}}]}""",
                headers = headersOf(HttpHeaders.ContentType, "application/json"),
            )
        }
        val result = transport.sendEvents(listOf(event(1))).single()
        assertEquals(EventAck.REJECTED, result.status)
        assertEquals("conflicting_duplicate", result.error?.code)
        assertEquals(false, result.error?.retryable)
    }

    @Test
    fun `HTTP 500 - TransportException а не разбор мусора`() = runTest {
        val transport = transport {
            respond("упал", HttpStatusCode.InternalServerError)
        }
        assertFailsWith<TransportException> { transport.sendEvents(listOf(event(1))) }
    }

    @Test
    fun `сетевая ошибка - TransportException`() = runTest {
        val transport = transport { throw RuntimeException("нет сети") }
        assertFailsWith<TransportException> { transport.pull(null) }
    }

    @Test
    fun `pull отправляет capabilities и разбирает дельту`() = runTest {
        var captured: HttpRequestData? = null
        val transport = transport { request ->
            captured = request
            respond(
                """{"changes": [
                      {"type": "task.upsert", "data": {"id": "t-1",
                       "protocol_code": "kv_router", "state": "NEW"}}],
                    "next_cursor": "c-2", "has_more": true}""",
                headers = headersOf(HttpHeaders.ContentType, "application/json"),
            )
        }
        val result = transport.pull("c-1")

        val sent = Json.parseToJsonElement(
            (captured!!.body as TextContent).text).jsonObject
        assertEquals("c-1", sent["cursor"]!!.jsonPrimitive.content)
        assertEquals("1", sent["capabilities"]!!.jsonObject["schema_version"]!!
            .jsonPrimitive.content)
        assertEquals(1, result.changes.size)
        assertEquals("task.upsert", result.changes.single().type)
        assertEquals("c-2", result.nextCursor)
        assertTrue(result.hasMore)
    }

    @Test
    fun `fetchDirectory без кэша не шлёт If-None-Match и берёт ETag из ответа`() = runTest {
        var captured: HttpRequestData? = null
        val transport = transport { request ->
            captured = request
            respond(
                """{"directory": "standard_series", "version": 2, "sections": {}}""",
                headers = headersOf(
                    HttpHeaders.ContentType to listOf("application/json"),
                    HttpHeaders.ETag to listOf("\"standard_series-v2\""),
                ),
            )
        }
        val response = transport.fetchDirectory("standard_series", etag = null)
        assertNull(captured!!.headers["If-None-Match"])
        assertEquals("\"standard_series-v2\"", response.etag)
        assertTrue(response.body!!.contains("standard_series"))
    }

    @Test
    fun `fetchDirectory с актуальным кэшем получает 304 без тела`() = runTest {
        val transport = transport { request ->
            assertEquals("\"standard_series-v2\"", request.headers["If-None-Match"])
            respond("", HttpStatusCode.NotModified)
        }
        val response = transport.fetchDirectory("standard_series", "\"standard_series-v2\"")
        assertTrue(response.notModified)
        assertNull(response.body)
    }
}
