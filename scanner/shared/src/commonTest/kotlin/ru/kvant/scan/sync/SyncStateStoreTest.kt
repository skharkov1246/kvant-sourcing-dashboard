package ru.kvant.scan.sync

import kotlinx.coroutines.test.runTest
import ru.kvant.scan.domain.Instant
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

/**
 * Персистентный курсор pull: рестарт приложения продолжает с сохранённого
 * места, а не перекачивает историю; первый запуск честно качает всё.
 */
class SyncStateStoreTest {

    @Test
    fun `пустой стор отдаёт null - первый pull качает всё с начала`() = runTest {
        assertNull(InMemorySyncStateStore().get(SyncStateStore.PULL_CURSOR))
    }

    @Test
    fun `курсор перезаписывается и читается назад`() = runTest {
        val store = InMemorySyncStateStore()
        store.put(SyncStateStore.PULL_CURSOR, "c-1")
        store.put(SyncStateStore.PULL_CURSOR, "c-2")
        assertEquals("c-2", store.get(SyncStateStore.PULL_CURSOR))
    }

    @Test
    fun `рестарт графа с сохранённым курсором не перекачивает историю`() = runTest {
        val state = InMemorySyncStateStore()
        val served = mutableListOf<String?>()
        val transport = object : SyncTransport {
            override suspend fun sendEvents(events: List<ClientEvent>): List<EventResult> =
                error("канал событий в тесте не используется")

            override suspend fun pull(cursor: String?): PullResult {
                served += cursor
                return PullResult(changes = emptyList(), nextCursor = "c-next", hasMore = false)
            }

            override suspend fun fetchDirectory(name: String, etag: String?): DirectoryResponse =
                error("канал справочников в тесте не используется")
        }

        fun engine() = SyncEngine(
            outbox = InMemoryOutboxStore(), transport = transport,
            clock = { Instant(0) }, random = { 0.5 },
        )
        suspend fun onePass(e: SyncEngine) {
            // Тот же порядок, что в SyncStack.pullOnce: применить, потом сохранить.
            val next = e.pullOnce(state.get(SyncStateStore.PULL_CURSOR),
                                  PullApplier(InMemoryTaskStore()))
            next?.let { state.put(SyncStateStore.PULL_CURSOR, it) }
        }

        onePass(engine())            // первый запуск — без курсора
        onePass(engine())            // «рестарт»: новый движок, тот же стор
        assertEquals(listOf(null, "c-next"), served)
    }
}
