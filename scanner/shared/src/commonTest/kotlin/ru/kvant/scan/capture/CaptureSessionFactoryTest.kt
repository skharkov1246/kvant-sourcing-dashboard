package ru.kvant.scan.capture

import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import ru.kvant.scan.domain.Instant
import ru.kvant.scan.protocol.CaptureProtocol
import ru.kvant.scan.protocol.ProtocolStatus
import ru.kvant.scan.sync.Change
import ru.kvant.scan.sync.EventType
import ru.kvant.scan.sync.InMemoryOutboxStore
import ru.kvant.scan.sync.InMemoryTaskStore
import ru.kvant.scan.sync.LocalTask
import ru.kvant.scan.sync.PullApplier
import ru.kvant.scan.sync.PullResult
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * Фабрика сессии: от задания из pull до событий в outbox — весь локальный
 * цикл оператора без единого платформенного класса.
 */
class CaptureSessionFactoryTest {

    private val json = Json { ignoreUnknownKeys = true }

    private fun protocolDoc(version: Int = 3) = json.encodeToJsonElement(
        CaptureProtocol.serializer(),
        CaptureProtocol(
            schemaVersion = 1, code = "kv_router", version = version,
            status = ProtocolStatus.ACTIVE, scope = "global",
            title = "Маршрутизатор",
            steps = listOf(
                ru.kvant.scan.protocol.CaptureStep(
                    id = "overview", kind = ru.kvant.scan.protocol.StepKind.PHOTO, title = "Общий вид"),
                ru.kvant.scan.protocol.CaptureStep(
                    id = "confirm", kind = ru.kvant.scan.protocol.StepKind.CONFIRM, title = "Подтверждение"),
            ),
        ),
    ).jsonObject

    private fun task(version: Int? = null) = LocalTask(
        id = "t-1", protocolCode = "kv_router", protocolVersion = version,
        state = "NEW", priority = 100, dueAt = null, title = "Насос",
        raw = json.parseToJsonElement("""{"id": "t-1"}""").jsonObject,
    )

    @Test
    fun `полный локальный цикл - задание из pull до событий в outbox`() = runTest {
        val tasks = InMemoryTaskStore()
        // Протокол и задание приезжают штатным pull-каналом.
        PullApplier(tasks).apply(PullResult(
            changes = listOf(Change("protocol.upsert", protocolDoc())),
            nextCursor = "c1", hasMore = false,
        ))
        val outbox = InMemoryOutboxStore()
        var n = 0L
        val controller = CaptureSessionFactory.forTask(
            task = task(version = 3), tasks = tasks, sessionId = "s-1",
            outbox = outbox, clock = { Instant(1000) }, newEventId = { "e-${++n}" },
        )
        controller.start()
        controller.completeStep("overview")
        controller.completeStep("confirm")
        controller.completeSession()

        val events = outbox.nextBatch(10, Instant(2000)).map { it.event }
        // session.started несёт контекст для сервера: protocol-ref и task_id.
        val started = events.first { it.type == EventType.SESSION_STARTED }
        assertEquals("kv_router@3", started.payload["protocol"]!!.jsonPrimitive.content)
        assertEquals("t-1", started.payload["task_id"]!!.jsonPrimitive.content)
        assertEquals(EventType.SESSION_COMPLETED, events.last().type)
        assertTrue(controller.state().isComplete)
    }

    @Test
    fun `задание без пина версии берёт свежайший синхронизированный протокол`() = runTest {
        val tasks = InMemoryTaskStore()
        PullApplier(tasks).apply(PullResult(
            changes = listOf(
                Change("protocol.upsert", protocolDoc(version = 2)),
                Change("protocol.upsert", protocolDoc(version = 5)),
            ),
            nextCursor = "c1", hasMore = false,
        ))
        val outbox = InMemoryOutboxStore()
        var n = 0L
        val controller = CaptureSessionFactory.forTask(
            task = task(version = null), tasks = tasks, sessionId = "s-2",
            outbox = outbox, clock = { Instant(1000) }, newEventId = { "e-${++n}" },
        )
        controller.start()
        val started = outbox.nextBatch(10, Instant(2000)).map { it.event }
            .first { it.type == EventType.SESSION_STARTED }
        assertEquals("kv_router@5", started.payload["protocol"]!!.jsonPrimitive.content)
    }

    @Test
    fun `нет протокола - честная ошибка ДО начала съёмки`() = runTest {
        assertFailsWith<CaptureSessionFactory.ProtocolMissing> {
            CaptureSessionFactory.forTask(
                task = task(version = 3), tasks = InMemoryTaskStore(), sessionId = "s-3",
                outbox = InMemoryOutboxStore(), clock = { Instant(0) }, newEventId = { "e" },
            )
        }
    }
}
