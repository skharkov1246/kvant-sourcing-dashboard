package ru.kvant.scan.sync

import kotlinx.coroutines.test.runTest
import ru.kvant.scan.domain.Instant
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * Закреплённая семантика очереди: «голова сессии держит хвост, сессии не
 * держат друг друга». SQLDelight-реализация обязана проходить этот же набор.
 */
class OutboxStoreTest {

    private val now = Instant(1_000_000)

    private fun event(session: String, seq: Long) = ClientEvent(
        clientEventId = "$session-$seq",
        sessionId = session,
        seq = seq,
        type = EventType.STEP_COMPLETED,
        deviceTs = Instant(seq),
    )

    private suspend fun OutboxStore.fill(session: String, count: Int) {
        (1..count).forEach { enqueue(event(session, it.toLong())) }
    }

    @Test
    fun `внутри сессии партия идёт строго по seq`() = runTest {
        val store = InMemoryOutboxStore()
        // Постановка вразнобой — порядок задаёт seq, а не время enqueue.
        store.enqueue(event("s1", 3))
        store.enqueue(event("s1", 1))
        store.enqueue(event("s1", 2))
        val batch = store.nextBatch(10, now)
        assertEquals(listOf(1L, 2L, 3L), batch.map { it.event.seq })
    }

    @Test
    fun `повторный enqueue того же события - идемпотентный no-op`() = runTest {
        val store = InMemoryOutboxStore()
        store.enqueue(event("s1", 1))
        store.enqueue(event("s1", 1))
        assertEquals(1, store.depth())
    }

    @Test
    fun `событие в полёте держит хвост своей сессии`() = runTest {
        val store = InMemoryOutboxStore()
        store.fill("s1", 3)
        store.markInFlight(listOf("s1-1"))
        // Дыра в середине лога сломала бы проекцию на сервере — хвост ждёт.
        assertEquals(emptyList(), store.nextBatch(10, now).map { it.event.clientEventId })
    }

    @Test
    fun `backoff головы держит хвост но не другие сессии`() = runTest {
        val store = InMemoryOutboxStore()
        store.fill("s1", 2)
        store.fill("s2", 2)
        store.reschedule("s1-1", attempts = 1, nextAttemptAt = Instant(now.epochMillis + 60_000),
            error = SyncError("network", "обрыв", retryable = true))
        val ids = store.nextBatch(10, now).map { it.event.clientEventId }
        // Залипшая s1 пропущена целиком, s2 уходит полностью (§04.3).
        assertEquals(listOf("s2-1", "s2-2"), ids)
    }

    @Test
    fun `после паузы backoff событие возвращается в партию`() = runTest {
        val store = InMemoryOutboxStore()
        store.fill("s1", 1)
        store.reschedule("s1-1", attempts = 1, nextAttemptAt = Instant(now.epochMillis + 60_000),
            error = SyncError("network", "обрыв", retryable = true))
        val later = Instant(now.epochMillis + 61_000)
        assertEquals(listOf("s1-1"), store.nextBatch(10, later).map { it.event.clientEventId })
        assertEquals(1, store.nextBatch(10, later).first().attempts)
    }

    @Test
    fun `мёртвое письмо кварантинит хвост сессии и не удаляется`() = runTest {
        val store = InMemoryOutboxStore()
        store.fill("s1", 3)
        store.moveToDeadLetter("s1-1", SyncError("conflicting_duplicate", "конфликт", retryable = false))
        // Хвост не уходит: сервер без s1-1 не свернёт s1-2/s1-3 корректно.
        assertEquals(emptyList(), store.nextBatch(10, now))
        // Глубина очереди мёртвые письма не считает, но запись жива для поддержки.
        assertEquals(2, store.depth())
    }

    @Test
    fun `подтверждённые события уходят из очереди`() = runTest {
        val store = InMemoryOutboxStore()
        store.fill("s1", 2)
        store.markInFlight(listOf("s1-1", "s1-2"))
        store.remove(listOf("s1-1", "s1-2"))
        assertEquals(0, store.depth())
        assertTrue(store.nextBatch(10, now).isEmpty())
    }

    @Test
    fun `лимит партии режет по количеству а не по сессиям`() = runTest {
        val store = InMemoryOutboxStore()
        store.fill("s1", 3)
        val batch = store.nextBatch(2, now)
        assertEquals(listOf(1L, 2L), batch.map { it.event.seq })
    }
}
