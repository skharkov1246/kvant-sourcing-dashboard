package ru.kvant.scan.sync

import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import ru.kvant.scan.domain.Instant
import ru.kvant.scan.trace.TraceLinkDraft
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * Сводка «что ещё не уехало»: индикатор синхронизации считает все три
 * офлайн-канала и честно пустеет только когда устройство чисто (I-1).
 */
class SyncStatusTest {

    private fun stack() = SyncStack(
        SyncConfig(
            baseUrl = "http://127.0.0.1:1",  // сеть в тесте не трогается
            tenantId = "t-internal", userId = "u-1", deviceId = "d-1",
            capabilities = Json.parseToJsonElement("""{"schema_version":1}""").jsonObject,
        )
    )

    @Test
    fun `чистое устройство - нулевая сводка`() = runTest {
        val status = stack().syncStatus()
        assertTrue(status.clean)
        assertEquals(0, status.total)
    }

    @Test
    fun `каждый канал считается отдельно и в сумме`() = runTest {
        val stack = stack()
        stack.outbox.enqueue(ClientEvent(
            clientEventId = "e-1", sessionId = "s-1", seq = 1,
            type = EventType.SESSION_STARTED, deviceTs = Instant(0),
        ))
        stack.mediaQueue.enqueue(PendingMedia(
            localPath = "/tmp/frame.jpg", sessionId = "s-1",
            stepId = "overview", kind = "photo", mime = "image/jpeg",
        ))
        stack.traceLinks.enqueue(TraceLinkDraft(
            itemId = "itm-1", codeType = "gtin", codeValue = "460",
        ))

        val status = stack.syncStatus()
        assertEquals(1, status.outboxEvents)
        assertEquals(1, status.pendingMedia)
        assertEquals(1, status.pendingTraceLinks)
        assertEquals(3, status.total)
        assertFalse(status.clean)
    }

    @Test
    fun `доставка заявки уменьшает сводку`() = runTest {
        val stack = stack()
        val draft = TraceLinkDraft(itemId = "itm-1", codeType = "gtin", codeValue = "460")
        stack.traceLinks.enqueue(draft)
        assertEquals(1, stack.syncStatus().total)

        stack.traceLinks.markDone(draft)
        assertTrue(stack.syncStatus().clean)
    }
}
