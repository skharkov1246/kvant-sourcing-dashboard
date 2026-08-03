package ru.kvant.scan.sync

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Служебное состояние синхронизации (курсор pull и подобное) — key/value,
 * переживающее рестарт приложения. Пустой стор — не ошибка: первый pull без
 * курсора честно качает всё с начала.
 *
 * In-memory реализация — для дев-стенда и тестов; SQLDelight-драйвер
 * (таблица sync_state) встанет на этот же интерфейс при сборке APK,
 * граф не изменится — тот же приём, что с TaskStore и MediaQueue.
 */
interface SyncStateStore {
    suspend fun get(key: String): String?

    suspend fun put(key: String, value: String)

    companion object {
        /** Курсор pull-канала (§04.1): непрозрачен, хранится как есть. */
        const val PULL_CURSOR = "pull_cursor"
    }
}

class InMemorySyncStateStore : SyncStateStore {
    private val mutex = Mutex()
    private val values = mutableMapOf<String, String>()

    override suspend fun get(key: String): String? = mutex.withLock { values[key] }

    override suspend fun put(key: String, value: String): Unit = mutex.withLock {
        values[key] = value
    }
}
