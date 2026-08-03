package ru.kvant.scan.sync

import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import ru.kvant.scan.directory.DirectoryStore
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * Pull-канал: дельта сервера → локальное состояние списка заданий.
 * Неизвестные типы изменений игнорируются — аддитивность API (§09.7).
 */
class PullApplierTest {

    private fun obj(text: String) = Json.parseToJsonElement(text).jsonObject

    private fun taskJson(id: String, state: String = "NEW", priority: Int = 100,
                         due: String? = null, title: String = "Насос") = obj(
        """{"id": "$id", "protocol_code": "kv_router", "protocol_version": 1,
            "state": "$state", "priority": $priority,
            ${if (due != null) "\"due_at\": \"$due\"," else ""}
            "subject": {"title": "$title"}}"""
    )

    @Test
    fun `task upsert появляется в списке`() = runTest {
        val store = InMemoryTaskStore()
        val summary = PullApplier(store).apply(PullResult(
            changes = listOf(Change("task.upsert", taskJson("t-1"))),
            nextCursor = "c1", hasMore = false,
        ))
        assertEquals(1, summary.tasksApplied)
        val task = store.tasks().single()
        assertEquals("t-1", task.id)
        assertEquals("Насос", task.title)
    }

    @Test
    fun `повторный upsert замещает задание - REWORK виден сразу`() = runTest {
        val store = InMemoryTaskStore()
        val applier = PullApplier(store)
        applier.apply(PullResult(listOf(Change("task.upsert", taskJson("t-1"))), "c1", false))
        applier.apply(PullResult(
            listOf(Change("task.upsert", taskJson("t-1", state = "REWORK"))), "c2", false))
        assertEquals(listOf("REWORK"), store.tasks().map { it.state })
    }

    @Test
    fun `список отсортирован - срочные сверху`() = runTest {
        val store = InMemoryTaskStore()
        PullApplier(store).apply(PullResult(
            changes = listOf(
                Change("task.upsert", taskJson("t-late", priority = 100, due = "2026-08-20")),
                Change("task.upsert", taskJson("t-hot", priority = 50)),
                Change("task.upsert", taskJson("t-soon", priority = 100, due = "2026-08-05")),
            ),
            nextCursor = "c1", hasMore = false,
        ))
        assertEquals(listOf("t-hot", "t-soon", "t-late"), store.tasks().map { it.id })
    }

    @Test
    fun `protocol upsert сохраняется и доступен по коду и версии`() = runTest {
        val store = InMemoryTaskStore()
        val protocol = obj("""{"code": "kv_cat_g", "version": 3, "steps": []}""")
        PullApplier(store).apply(PullResult(
            listOf(Change("protocol.upsert", protocol)), "c1", false))
        assertNotNull(store.protocol("kv_cat_g", 3))
        assertNull(store.protocol("kv_cat_g", 2))
    }

    @Test
    fun `неизвестный тип изменения пропускается а не роняет синк`() = runTest {
        val store = InMemoryTaskStore()
        val summary = PullApplier(store).apply(PullResult(
            changes = listOf(
                Change("verdicts.upsert", obj("""{"whatever": true}""")),
                Change("task.upsert", taskJson("t-1")),
            ),
            nextCursor = "c1", hasMore = false,
        ))
        assertEquals(1, summary.skipped)
        assertEquals(1, summary.tasksApplied)
    }

    @Test
    fun `verdict upsert - оператор видит судьбу своей сессии`() = runTest {
        val store = InMemoryTaskStore()
        val summary = PullApplier(store).apply(PullResult(
            changes = listOf(Change("verdict.upsert", obj(
                """{"session_id": "s-9", "verdict": "REWORK",
                    "verdict_comment": "переснять этикетку",
                    "resolved_at": "2026-08-03T03:00:00+00:00"}"""))),
            nextCursor = "c1", hasMore = false,
        ))
        assertEquals(1, summary.verdictsApplied)
        val verdict = store.verdict("s-9")
        assertEquals("REWORK", verdict?.verdict)
        assertEquals("переснять этикетку", verdict?.comment)
        assertNull(store.verdict("s-unknown"))
    }

    @Test
    fun `битое задание без id пропускается со счётчиком`() = runTest {
        val store = InMemoryTaskStore()
        val summary = PullApplier(store).apply(PullResult(
            listOf(Change("task.upsert", obj("""{"state": "NEW"}"""))), "c1", false))
        assertEquals(1, summary.skipped)
        assertTrue(store.tasks().isEmpty())
    }
}

class PullLoopAndDirectoryTest {

    private class FakeTransport(
        private val pages: MutableList<PullResult> = mutableListOf(),
        private val directoryResponses: MutableList<DirectoryResponse> = mutableListOf(),
    ) : SyncTransport {
        val directoryRequests = mutableListOf<String?>()

        override suspend fun sendEvents(events: List<ClientEvent>): List<EventResult> =
            error("не используется в этом тесте")

        override suspend fun pull(cursor: String?): PullResult = pages.removeAt(0)

        override suspend fun fetchDirectory(name: String, etag: String?): DirectoryResponse {
            directoryRequests += etag
            return directoryResponses.removeAt(0)
        }
    }

    private fun engine(transport: SyncTransport) = SyncEngine(
        outbox = InMemoryOutboxStore(),
        transport = transport,
        clock = { ru.kvant.scan.domain.Instant(0) },
        random = { 0.5 },
    )

    @Test
    fun `pullOnce листает страницы до конца и возвращает курсор`() = runTest {
        val store = InMemoryTaskStore()
        val page = { id: String, cursor: String, more: Boolean ->
            PullResult(
                listOf(Change("task.upsert",
                    Json.parseToJsonElement(
                        """{"id": "$id", "protocol_code": "kv_router", "state": "NEW"}"""
                    ).jsonObject)),
                cursor, more,
            )
        }
        val transport = FakeTransport(mutableListOf(
            page("t-1", "c1", true), page("t-2", "c2", false)))
        val cursor = engine(transport).pullOnce(null, PullApplier(store))
        assertEquals("c2", cursor)
        assertEquals(listOf("t-1", "t-2"), store.tasks().map { it.id })
    }

    private val docV2 = """
        {"directory": "standard_series", "version": 2,
         "sections": {"oring_d2_iso3601": {"values": [1.78]}}}
    """.trimIndent()

    @Test
    fun `справочник качается с etag из кэша и применяется`() = runTest {
        val store = DirectoryStore()
        val transport = FakeTransport(directoryResponses = mutableListOf(
            DirectoryResponse(notModified = false, etag = "\"standard_series-v2\"", body = docV2)))
        assertTrue(DirectorySync(transport, store).syncOnce())
        assertEquals(2, store.current?.version)
        // Следующий запрос уйдёт уже с сохранённым ETag
        val transport2 = FakeTransport(directoryResponses = mutableListOf(
            DirectoryResponse(notModified = true)))
        val sync2 = DirectorySync(transport2, store)
        assertFalse(sync2.syncOnce())
        assertEquals("\"standard_series-v2\"", transport2.directoryRequests.single())
        assertEquals(2, store.current?.version)
    }
}
