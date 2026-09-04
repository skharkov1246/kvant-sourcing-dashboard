package ru.kvant.scan.trace

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import ru.kvant.scan.sync.TransportException

/**
 * Очередь заявок трассировки — офлайн-надёжность по образцу медиа-канала
 * (I-1: введённое кладовщиком не теряется). Форма кладёт заявку СЮДА, а не
 * шлёт напрямую: без сети заявка ждёт в очереди и уезжает следующим
 * проходом синка. Запись покидает PENDING только после 201 сервера.
 *
 * Идемпотентность — по содержанию заявки (itemId+codeType+codeValue):
 * повторный тап «Отправить» не плодит дублей ни в очереди, ни на сервере.
 */
data class PendingTraceLink(
    val draft: TraceLinkDraft,
    val state: State = State.PENDING,
    val attempts: Int = 0,
    val lastError: String? = null,
) {
    enum class State { PENDING, DONE }
}

interface TraceLinkQueue {
    /** Идемпотентно по (itemId, codeType, codeValue). */
    suspend fun enqueue(draft: TraceLinkDraft)

    suspend fun pending(): List<PendingTraceLink>

    suspend fun markDone(draft: TraceLinkDraft)

    suspend fun markFailed(draft: TraceLinkDraft, error: String)

    /** Доставлена ли заявка с таким содержанием (для ответа формы). */
    suspend fun isDelivered(draft: TraceLinkDraft): Boolean
}

class InMemoryTraceLinkQueue : TraceLinkQueue {
    private val mutex = Mutex()
    private val byKey = linkedMapOf<Triple<String, String, String>, PendingTraceLink>()

    private fun key(d: TraceLinkDraft) = Triple(d.itemId, d.codeType, d.codeValue)

    override suspend fun enqueue(draft: TraceLinkDraft): Unit = mutex.withLock {
        if (key(draft) !in byKey) byKey[key(draft)] = PendingTraceLink(draft)
    }

    override suspend fun pending(): List<PendingTraceLink> = mutex.withLock {
        byKey.values.filter { it.state == PendingTraceLink.State.PENDING }
    }

    override suspend fun markDone(draft: TraceLinkDraft): Unit = mutex.withLock {
        byKey[key(draft)]?.let {
            byKey[key(draft)] = it.copy(state = PendingTraceLink.State.DONE)
        }
    }

    override suspend fun markFailed(draft: TraceLinkDraft, error: String): Unit =
        mutex.withLock {
            byKey[key(draft)]?.let {
                byKey[key(draft)] = it.copy(attempts = it.attempts + 1, lastError = error)
            }
        }

    override suspend fun isDelivered(draft: TraceLinkDraft): Boolean = mutex.withLock {
        byKey[key(draft)]?.state == PendingTraceLink.State.DONE
    }
}

/**
 * Воркер очереди: пытается доставить каждую PENDING-заявку. Сетевая ошибка
 * оставляет заявку в очереди с причиной — доставит следующий проход
 * (его зовёт и submit формы, и штатный pull-цикл синка).
 */
class TraceLinkWorker(
    private val queue: TraceLinkQueue,
    private val send: suspend (TraceLinkDraft) -> Boolean,
) {
    data class Report(val sent: Int = 0, val failed: Int = 0)

    suspend fun runOnce(): Report {
        var report = Report()
        for (entry in queue.pending()) {
            try {
                if (send(entry.draft)) {
                    queue.markDone(entry.draft)
                    report = report.copy(sent = report.sent + 1)
                } else {
                    queue.markFailed(entry.draft, "сервер ответил не 201")
                    report = report.copy(failed = report.failed + 1)
                }
            } catch (e: TransportException) {
                queue.markFailed(entry.draft, e.message ?: "сеть недоступна")
                report = report.copy(failed = report.failed + 1)
            }
        }
        return report
    }
}
