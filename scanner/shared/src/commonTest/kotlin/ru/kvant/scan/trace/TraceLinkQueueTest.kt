package ru.kvant.scan.trace

import kotlinx.coroutines.test.runTest
import ru.kvant.scan.sync.TransportException
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * Офлайн-надёжность заявок трассировки: без сети заявка ждёт в очереди
 * (не теряется — I-1), повторный тап не плодит дублей, доставленное
 * не отправляется повторно.
 */
class TraceLinkQueueTest {

    private val draft = TraceLinkDraft(
        itemId = "itm-s-1", codeType = "gtin", codeValue = "4601234567890",
        supplierName = "Ningbo", supplierRef = "102",
    )

    @Test
    fun `повторный тап Отправить не плодит дублей в очереди`() = runTest {
        val queue = InMemoryTraceLinkQueue()
        queue.enqueue(draft)
        queue.enqueue(draft.copy())  // то же содержание — та же заявка
        assertEquals(1, queue.pending().size)
    }

    @Test
    fun `успешная доставка помечает DONE и не шлётся повторно`() = runTest {
        val queue = InMemoryTraceLinkQueue()
        var calls = 0
        val worker = TraceLinkWorker(queue) { calls++; true }

        queue.enqueue(draft)
        assertEquals(1, worker.runOnce().sent)
        assertTrue(queue.isDelivered(draft))

        // Второй проход — очередь пуста, сервер не беспокоим.
        assertEquals(0, worker.runOnce().sent)
        assertEquals(1, calls)
    }

    @Test
    fun `обрыв сети оставляет заявку в очереди - восстановление доставляет`() = runTest {
        val queue = InMemoryTraceLinkQueue()
        var online = false
        val worker = TraceLinkWorker(queue) {
            if (!online) throw TransportException("нет сети")
            true
        }

        queue.enqueue(draft)
        assertEquals(1, worker.runOnce().failed)
        assertFalse(queue.isDelivered(draft))
        val stuck = queue.pending().single()
        assertEquals(1, stuck.attempts)
        assertEquals("нет сети", stuck.lastError)

        online = true  // сеть вернулась — штатный проход синка доставляет
        assertEquals(1, worker.runOnce().sent)
        assertTrue(queue.isDelivered(draft))
    }

    @Test
    fun `не-201 от сервера - failed с причиной а не тихое DONE`() = runTest {
        val queue = InMemoryTraceLinkQueue()
        val worker = TraceLinkWorker(queue) { false }
        queue.enqueue(draft)
        assertEquals(1, worker.runOnce().failed)
        assertFalse(queue.isDelivered(draft))
        assertEquals("сервер ответил не 201", queue.pending().single().lastError)
    }

    @Test
    fun `разные заявки на один item живут в очереди независимо`() = runTest {
        val queue = InMemoryTraceLinkQueue()
        queue.enqueue(draft)
        queue.enqueue(draft.copy(codeType = "serial", codeValue = "SN-1"))
        assertEquals(2, queue.pending().size)
    }
}
