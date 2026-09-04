package ru.kvant.scan.sync

import ru.kvant.scan.domain.Instant

/**
 * Движок синхронизации: три независимых канала (§04.2).
 *
 *  1. pull      — задания, протоколы, справочники (килобайты)
 *  2. события   — факты сессии (килобайты, идемпотентно)
 *  3. медиа     — кадры и видео (сотни мегабайт, чанками)
 *
 * Разделение критично: метаданные не должны застревать за гигабайтом видео.
 * Сервер узнаёт, что паллета обмерена, через секунды после появления сети;
 * кадры догружаются часами, если надо.
 */
class SyncEngine(
    private val outbox: OutboxStore,
    private val transport: SyncTransport,
    private val clock: () -> Instant,
    private val random: () -> Double,
    private val batchSize: Int = 200,
) {

    data class Report(
        val accepted: Int = 0,
        val duplicates: Int = 0,
        val quarantined: Int = 0,
        val retried: Int = 0,
        val remaining: Int = 0,
    )

    /**
     * Один проход отправки событий. Вызывается фоновым воркером; безопасен для
     * повторного вызова в любой момент.
     */
    suspend fun pushOnce(): Report {
        val now = clock()
        val batch = outbox.nextBatch(batchSize, now)
        if (batch.isEmpty()) return Report(remaining = outbox.depth())

        outbox.markInFlight(batch.map { it.event.clientEventId })

        val results = try {
            transport.sendEvents(batch.map { it.event })
        } catch (e: TransportException) {
            // Сетевая ошибка: вся партия возвращается в очередь с backoff.
            batch.forEach { entry ->
                outbox.reschedule(
                    id = entry.event.clientEventId,
                    attempts = entry.attempts + 1,
                    nextAttemptAt = Instant(now.epochMillis + Backoff.delayMs(entry.attempts, random())),
                    error = SyncError("transport", e.message ?: "network", retryable = true),
                )
            }
            return Report(retried = batch.size, remaining = outbox.depth())
        }

        var report = Report()
        val byId = batch.associateBy { it.event.clientEventId }
        val toRemove = mutableListOf<String>()

        for (result in results) {
            val entry = byId[result.clientEventId] ?: continue
            when (result.status) {
                EventAck.ACCEPTED -> {
                    toRemove += result.clientEventId
                    report = report.copy(accepted = report.accepted + 1)
                }
                // Повтор после потери ответа — норма, а не ошибка.
                EventAck.DUPLICATE -> {
                    toRemove += result.clientEventId
                    report = report.copy(duplicates = report.duplicates + 1)
                }
                EventAck.REJECTED -> {
                    val error = result.error ?: SyncError("unknown", "rejected without reason")
                    if (error.retryable) {
                        outbox.reschedule(
                            id = result.clientEventId,
                            attempts = entry.attempts + 1,
                            nextAttemptAt = Instant(
                                now.epochMillis + Backoff.delayMs(entry.attempts, random())
                            ),
                            error = error,
                        )
                        report = report.copy(retried = report.retried + 1)
                    } else {
                        // Карантин, а не удаление: событие остаётся доступным
                        // для выгрузки в поддержку (§04.3).
                        outbox.moveToDeadLetter(result.clientEventId, error)
                        report = report.copy(quarantined = report.quarantined + 1)
                    }
                }
            }
        }

        if (toRemove.isNotEmpty()) outbox.remove(toRemove)
        return report.copy(remaining = outbox.depth())
    }

    /**
     * Один проход pull-канала: страницы дельты применяются к локальному
     * состоянию до исчерпания has_more. Возвращает курсор для следующего
     * прохода — хранение курсора за вызывающей стороной (persistence-слой).
     */
    suspend fun pullOnce(cursor: String?, applier: PullApplier): String? {
        var current = cursor
        while (true) {
            val page = transport.pull(current)
            applier.apply(page)
            current = page.nextCursor
            if (!page.hasMore) return current
        }
    }
}

/** Транспортный слой. Реализация — Ktor-клиент в платформенном модуле. */
interface SyncTransport {
    suspend fun sendEvents(events: List<ClientEvent>): List<EventResult>
    suspend fun pull(cursor: String?): PullResult
    suspend fun fetchDirectory(name: String, etag: String?): DirectoryResponse
}

/** Ответ GET /v1/directories/{name}: 304 → notModified, 200 → etag + тело. */
data class DirectoryResponse(
    val notModified: Boolean,
    val etag: String? = null,
    val body: String? = null,
)

data class PullResult(
    val changes: List<Change>,
    val nextCursor: String,
    val hasMore: Boolean,
)

data class Change(val type: String, val data: kotlinx.serialization.json.JsonObject)

class TransportException(message: String, cause: Throwable? = null) : Exception(message, cause)
