package ru.kvant.scan.sync

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import ru.kvant.scan.domain.Instant

/**
 * Эталонная реализация [OutboxStore] в памяти.
 *
 * Двойное назначение: рабочее хранилище до подключения платформенного
 * драйвера SQLDelight и — главное — закреплённая тестами семантика выбора
 * партии, которой обязана следовать SQLDelight-реализация (outbox_event.sq).
 *
 * Правило партии — «голова сессии держит хвост»:
 *   * внутри сессии события уходят строго по seq — сервер сворачивает лог,
 *     и дыра в середине ломает проекцию;
 *   * если голова сессии в полёте, на backoff-паузе или в карантине —
 *     вся сессия пропускается в этом раунде;
 *   * между сессиями порядок свободный: одна залипшая сессия не блокирует
 *     остальные (§04.3).
 */
class InMemoryOutboxStore : OutboxStore {

    private val mutex = Mutex()
    private val entries = linkedMapOf<String, OutboxEntry>()  // порядок постановки

    override suspend fun enqueue(event: ClientEvent): Unit = mutex.withLock {
        // Повтор того же client_event_id — идемпотентный no-op (I-2 на устройстве).
        if (event.clientEventId !in entries) {
            entries[event.clientEventId] = OutboxEntry(event)
        }
    }

    override suspend fun nextBatch(limit: Int, now: Instant): List<OutboxEntry> = mutex.withLock {
        val batch = mutableListOf<OutboxEntry>()
        for ((_, sessionEntries) in entries.values.groupBy { it.event.sessionId }) {
            for (entry in sessionEntries.sortedBy { it.event.seq }) {
                val headBlocks = when {
                    entry.state == OutboxState.DEAD_LETTER -> true  // хвост в карантин вслед за головой
                    entry.state == OutboxState.IN_FLIGHT -> true    // ждём результат головы
                    entry.nextAttemptAt > now -> true               // backoff головы держит хвост
                    else -> false
                }
                if (headBlocks) break
                batch += entry
                if (batch.size == limit) return batch
            }
        }
        return batch
    }

    override suspend fun markInFlight(ids: List<String>): Unit = mutex.withLock {
        for (id in ids) {
            entries[id]?.let { entries[id] = it.copy(state = OutboxState.IN_FLIGHT) }
        }
    }

    override suspend fun remove(ids: List<String>): Unit = mutex.withLock {
        ids.forEach(entries::remove)
    }

    override suspend fun reschedule(
        id: String, attempts: Int, nextAttemptAt: Instant, error: SyncError?,
    ): Unit = mutex.withLock {
        entries[id]?.let {
            entries[id] = it.copy(
                state = OutboxState.PENDING,
                attempts = attempts,
                nextAttemptAt = nextAttemptAt,
                lastError = error,
            )
        }
    }

    override suspend fun moveToDeadLetter(id: String, error: SyncError): Unit = mutex.withLock {
        // Не удаляется: мёртвые письма — материал для поддержки (§04.4).
        entries[id]?.let {
            entries[id] = it.copy(state = OutboxState.DEAD_LETTER, lastError = error)
        }
    }

    override suspend fun depth(): Int = mutex.withLock {
        entries.values.count { it.state != OutboxState.DEAD_LETTER }
    }
}
