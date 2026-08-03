package ru.kvant.scan.integration

import io.ktor.client.HttpClient
import io.ktor.client.engine.cio.CIO
import io.ktor.client.request.get
import io.ktor.client.request.header
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.contentType
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import ru.kvant.scan.domain.Instant
import ru.kvant.scan.sync.ClientEvent
import ru.kvant.scan.sync.EventAck
import ru.kvant.scan.sync.EventType
import ru.kvant.scan.sync.InMemoryTaskStore
import ru.kvant.scan.sync.KtorSyncTransport
import ru.kvant.scan.sync.PullApplier
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
                """{"schema_version": 1, "app_version": "0.1.0",
                    "platform": "android", "max_accuracy_class": "B"}"""
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
    fun `полный цикл оператора одним тестом - Bitrix до пересъёмки`() = runTest {
        if (!serverIsUp()) {
            println("SMOKE SKIPPED: uvicorn на :8077 не запущен")
            return@runTest
        }
        val client = HttpClient(CIO)
        val transport = transport(client)
        val externalRef = "deal-${UUID.randomUUID()}"

        // 1. «Bitrix» отдал задание (дев-сервер знает протокол pallet_general).
        val inbound = client.post("$baseUrl/v1/inbound/tasks") {
            header("X-Tenant-Id", "t-internal"); header("X-User-Id", "u-mgr")
            header("Idempotency-Key", UUID.randomUUID().toString())
            contentType(ContentType.Application.Json)
            setBody("""{"external_system": "bitrix24", "external_ref": "$externalRef",
                        "protocol_code": "pallet_general",
                        "subject": {"title": "Паллета насосов"}}""")
        }.bodyAsText()
        val taskId = Json.parseToJsonElement(inbound)
            .jsonObject["task_id"]!!.jsonPrimitive.content

        // 2. Устройство подтянуло задание штатным pull через транспорт.
        val store = InMemoryTaskStore()
        val applier = PullApplier(store)
        applier.apply(transport.pull(null))
        val pulled = store.tasks().first { it.id == taskId }
        assertEquals("NEW", pulled.state)
        assertEquals("Паллета насосов", pulled.title)

        // 3. Оператор снял сессию по заданию, сервер поставил её контролёру.
        val sessionId = "s-loop-${UUID.randomUUID()}"
        fun event(seq: Long, type: EventType, payload: String) = ClientEvent(
            clientEventId = UUID.randomUUID().toString(), sessionId = sessionId,
            seq = seq, type = type, deviceTs = Instant(System.currentTimeMillis()),
            payload = Json.parseToJsonElement(payload).jsonObject,
        )
        transport.sendEvents(listOf(
            event(1, EventType.SESSION_STARTED,
                """{"protocol": "pallet_general@7", "task_id": "$taskId"}"""),
            event(2, EventType.STEP_COMPLETED, """{"step_id": "overview"}"""),
            event(3, EventType.SESSION_COMPLETED, "{}"),
        )).forEach { assertEquals(EventAck.ACCEPTED, it.status) }

        // 4. Контролёр вернул на пересъёмку.
        val verdict = client.post("$baseUrl/v1/review/$sessionId/verdict") {
            header("X-Tenant-Id", "t-internal"); header("X-User-Id", "u-rev")
            header("X-Roles", "operator,reviewer")
            contentType(ContentType.Application.Json)
            setBody("""{"verdict": "REWORK", "comment": "переснять этикетку"}""")
        }
        assertEquals(200, verdict.status.value)

        // 5. Следующий pull устройства видит задание снова — уже как REWORK.
        applier.apply(transport.pull(null))
        assertEquals("REWORK", store.tasks().first { it.id == taskId }.state)
    }

    @Test
    fun `живая загрузка ассета - Kotlin sha256 против hashlib сервера`() = runTest {
        if (!serverIsUp()) {
            println("SMOKE SKIPPED: uvicorn на :8077 не запущен")
            return@runTest
        }
        val uploader = ru.kvant.scan.sync.AssetUploader(
            HttpClient(CIO),
            SyncConfig(
                baseUrl = baseUrl, tenantId = "t-internal",
                userId = "u-smoke", deviceId = "d-smoke",
                capabilities = Json.parseToJsonElement("""{"schema_version":1}""").jsonObject,
            ),
        )
        val bytes = ByteArray(3 * 1024 * 1024 + 17) { (it % 251).toByte() }  // >2 чанков
        val meta = ru.kvant.scan.sync.AssetUploader.Meta(
            "s-media-${UUID.randomUUID()}", "overview", "photo", "image/jpeg")

        // complete вернёт verified только если наш sha256 совпал с hashlib.
        val assetId = uploader.upload(meta, bytes)

        // Повтор тех же байт — мгновенный дедуп по контрольной сумме (§04.4).
        assertEquals(assetId, uploader.upload(meta, bytes))
    }

    @Test
    fun `MediaWorker вживую - файл из очереди доезжает и становится удаляемым`() = runTest {
        if (!serverIsUp()) {
            println("SMOKE SKIPPED: uvicorn на :8077 не запущен")
            return@runTest
        }
        val stack = ru.kvant.scan.sync.SyncStack(SyncConfig(
            baseUrl = baseUrl, tenantId = "t-internal",
            userId = "u-smoke", deviceId = "d-smoke",
            capabilities = Json.parseToJsonElement("""{"schema_version":1}""").jsonObject,
        ))
        val file = java.io.File.createTempFile("kvant-frame", ".jpg")
        file.writeBytes(ByteArray(64 * 1024) { (it % 199).toByte() })
        stack.mediaQueue.enqueue(ru.kvant.scan.sync.PendingMedia(
            localPath = file.absolutePath,
            sessionId = "s-mw-${UUID.randomUUID()}",
            stepId = "overview", kind = "photo", mime = "image/jpeg",
        ))

        val worker = ru.kvant.scan.sync.MediaWorker(stack.mediaQueue, stack.uploader) { path ->
            java.io.File(path).takeIf { it.exists() }?.readBytes()
        }
        val report = worker.runOnce()

        assertEquals(1, report.uploaded)
        // Файл стал удаляемым ТОЛЬКО после подтверждения сервера (I-1).
        assertEquals(listOf(file.absolutePath), stack.mediaQueue.deletablePaths())
        file.delete()
    }

    @Test
    fun `подсказки поставщиков вживую - pullOnce наполняет форму трассировки`() = runTest {
        if (!serverIsUp()) {
            println("SMOKE SKIPPED: uvicorn на :8077 не запущен")
            return@runTest
        }
        val stack = ru.kvant.scan.sync.SyncStack(SyncConfig(
            baseUrl = baseUrl, tenantId = "t-internal",
            userId = "u-smoke", deviceId = "d-smoke",
            capabilities = Json.parseToJsonElement(
                """{"schema_version": 1, "app_version": "0.1.0",
                    "platform": "android", "max_accuracy_class": "B"}"""
            ).jsonObject,
        ))
        // Один штатный проход pull-канала: задания, справочники, suppliers.
        stack.pullOnce()

        val form = stack.traceLinkForm()
        form.editSupplierQuery("уплотн")
        val found = form.suggestions()
        assertTrue(found.isNotEmpty(), "снапшот suppliers не доехал до формы")
        // Подсказка несёт ref на CRM — заявка ссылается на реального контрагента.
        assertTrue(found.all { it.ref != null })

        // Повторный проход — версия не изменилась, снапшот не откатился.
        stack.pullOnce()
        assertEquals(found.map { it.name },
            stack.traceLinkForm().apply { editSupplierQuery("уплотн") }.suggestions().map { it.name })
    }

    @Test
    fun `карточка товара вживую - принятая сессия видна с устройства с verified-кодами`() = runTest {
        if (!serverIsUp()) {
            println("SMOKE SKIPPED: uvicorn на :8077 не запущен")
            return@runTest
        }
        val client = HttpClient(CIO)
        val transport = transport(client)
        val sessionId = "s-card-${UUID.randomUUID()}"

        // 1. Оператор снял сессию с прочитанным кодом.
        fun event(seq: Long, type: EventType, payload: String) = ClientEvent(
            clientEventId = UUID.randomUUID().toString(), sessionId = sessionId,
            seq = seq, type = type, deviceTs = Instant(System.currentTimeMillis()),
            payload = Json.parseToJsonElement(payload).jsonObject,
        )
        transport.sendEvents(listOf(
            event(1, EventType.SESSION_STARTED, """{"protocol": "pallet_general@7"}"""),
            event(2, EventType.STEP_COMPLETED,
                """{"step_id": "overview", "quality_score": 0.9}"""),
            event(3, EventType.CODE_READ,
                """{"step_id": "code", "code_type": "gtin", "raw_value": "4601234567890"}"""),
            event(4, EventType.SESSION_COMPLETED, "{}"),
        )).forEach { assertEquals(EventAck.ACCEPTED, it.status) }

        // 2. До вердикта карточки нет — честный null, не пустышка.
        assertEquals(null, transport.fetchItem("itm-$sessionId"))

        // 3. Контролёр принял — сервер материализовал карточку.
        val verdict = client.post("$baseUrl/v1/review/$sessionId/verdict") {
            header("X-Tenant-Id", "t-internal"); header("X-User-Id", "u-rev")
            header("X-Roles", "operator,reviewer")
            contentType(ContentType.Application.Json)
            setBody("""{"verdict": "ACCEPTED", "comment": "ок"}""")
        }
        assertEquals(200, verdict.status.value)

        // 4. Устройство видит карточку: identity из кода, verified, качество.
        val card = transport.fetchItem("itm-$sessionId")!!
        assertEquals("4601234567890", card.identity["gtin"])
        assertTrue(card.trace.all { it.confidence == "verified" })
        assertEquals(sessionId, card.sessionId)
        assertEquals(0.9, card.qualityScore)
    }

    @Test
    fun `заявка трассировки вживую - через очередь до сервера и обратно в карточку`() = runTest {
        if (!serverIsUp()) {
            println("SMOKE SKIPPED: uvicorn на :8077 не запущен")
            return@runTest
        }
        val stack = ru.kvant.scan.sync.SyncStack(SyncConfig(
            baseUrl = baseUrl, tenantId = "t-internal",
            userId = "u-smoke", deviceId = "d-smoke",
            capabilities = Json.parseToJsonElement(
                """{"schema_version": 1, "app_version": "0.1.0",
                    "platform": "android", "max_accuracy_class": "B"}"""
            ).jsonObject,
        ))
        stack.pullOnce()  // снапшот suppliers для подсказок

        val itemId = "item-smoke-${UUID.randomUUID()}"
        val form = stack.traceLinkForm()
        form.editSupplierQuery("Ningbo")
        form.selectSupplier(form.suggestions().single())
        form.setCodeType("gtin")
        form.setCodeValue("4609876543210")

        // Сервер жив — submit через очередь доставляет сразу.
        assertTrue(form.submit(itemId), "заявка не доставлена при живом сервере")
        assertTrue(stack.traceLinks.pending().isEmpty())

        // Заявка доехала: карточка видит код и контрагента из CRM.
        val card = stack.fetchItemCard(itemId)!!
        assertEquals("4609876543210", card.identity["gtin"])
        assertEquals("Ningbo Seals Co", card.trace.single().supplierName)
    }

    @Test
    fun `крауд-контур вживую - принятый скан виден в балансе и остатке кампании`() = runTest {
        if (!serverIsUp()) {
            println("SMOKE SKIPPED: uvicorn на :8077 не запущен")
            return@runTest
        }
        val client = HttpClient(CIO)
        fun io.ktor.client.request.HttpRequestBuilder.reviewer() {
            header("X-Tenant-Id", "t-internal"); header("X-User-Id", "u-rev")
            header("X-Roles", "operator,reviewer")
            contentType(ContentType.Application.Json)
        }

        // Админ-сторона: участник и кампания на два скана.
        val contributor = Json.parseToJsonElement(
            client.post("$baseUrl/v1/crowd/contributors") {
                reviewer()
                setBody("""{"display_name": "Смок", "phone_hash": "ph-${UUID.randomUUID()}"}""")
            }.bodyAsText()).jsonObject["id"]!!.jsonPrimitive.content
        val campaign = Json.parseToJsonElement(
            client.post("$baseUrl/v1/crowd/campaigns") {
                reviewer()
                setBody("""{"title": "Смок-кампания", "budget_cents_eur": 1000}""")
            }.bodyAsText()).jsonObject["id"]!!.jsonPrimitive.content

        // Участник сдал скан.
        val submitted = client.post("$baseUrl/v1/crowd/submissions") {
            header("X-Tenant-Id", "t-internal"); header("X-User-Id", "u-crowd")
            header("X-Roles", "operator")
            contentType(ContentType.Application.Json)
            setBody("""{"contributor_id": "$contributor",
                        "content_sha256": "sha-${UUID.randomUUID()}",
                        "submitted_at": "2026-08-03T12:00:00+00:00",
                        "quality_score": 0.9, "campaign_id": "$campaign"}""")
        }
        assertEquals(201, submitted.status.value)

        // Читающая сторона участника — через CrowdApi ядра.
        val api = ru.kvant.scan.crowd.CrowdApi(client, SyncConfig(
            baseUrl = baseUrl, tenantId = "t-internal",
            userId = "u-rev", deviceId = "d-smoke", roles = "operator,reviewer",
            capabilities = Json.parseToJsonElement("""{"schema_version":1}""").jsonObject,
        ))
        val ledger = api.fetchLedger(contributor)!!
        assertEquals(500, ledger.balanceCents)
        val card = api.fetchCampaign(campaign)!!
        assertEquals(500, card.remainingCents)
        // Остатка хватает ровно на один скан — потом съёмка погаснет сама.
        assertTrue(card.allowsCapture(500))
        assertFalse(card.allowsCapture(501))
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
