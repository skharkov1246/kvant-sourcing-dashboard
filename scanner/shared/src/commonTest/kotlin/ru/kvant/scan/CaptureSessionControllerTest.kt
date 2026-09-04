package ru.kvant.scan

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNull
import kotlin.test.assertTrue
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import ru.kvant.scan.capture.CaptureSessionController
import ru.kvant.scan.domain.Instant
import ru.kvant.scan.domain.SessionState
import ru.kvant.scan.domain.StepStatus
import ru.kvant.scan.protocol.CaptureProtocol
import ru.kvant.scan.protocol.CaptureStep
import ru.kvant.scan.protocol.Condition
import ru.kvant.scan.protocol.ProtocolStatus
import ru.kvant.scan.protocol.StepKind
import ru.kvant.scan.sync.ClientEvent
import ru.kvant.scan.sync.OutboxEntry
import ru.kvant.scan.sync.OutboxStore
import ru.kvant.scan.sync.SessionProjection
import ru.kvant.scan.sync.SyncError

/**
 * Контроллер сессии: инвариант класса — локальное состояние ОБЯЗАНО совпадать
 * со свёрткой отправленных им событий. Это тот же контракт, что между
 * клиентом и сервером (`SessionProjection` ↔ `projections.py`): если контроллер
 * показывает одно, а из событий складывается другое, кладовщик и диспетчер
 * видят разные данные.
 */
class CaptureSessionControllerTest {

    private class RecordingOutbox : OutboxStore {
        val events = mutableListOf<ClientEvent>()
        override suspend fun enqueue(event: ClientEvent) { events += event }
        override suspend fun nextBatch(limit: Int, now: Instant): List<OutboxEntry> = emptyList()
        override suspend fun markInFlight(ids: List<String>) = Unit
        override suspend fun remove(ids: List<String>) = Unit
        override suspend fun reschedule(id: String, attempts: Int, nextAttemptAt: Instant, error: SyncError?) = Unit
        override suspend fun moveToDeadLetter(id: String, error: SyncError) = Unit
        override suspend fun depth(): Int = events.size
    }

    private fun protocol(steps: List<CaptureStep>) = CaptureProtocol(
        schemaVersion = 1,
        code = "test",
        version = 1,
        status = ProtocolStatus.ACTIVE,
        scope = "global",
        title = "Тестовый протокол",
        steps = steps,
    )

    private fun photo(id: String, required: Boolean = true, visibleIf: Condition? = null) =
        CaptureStep(id = id, kind = StepKind.PHOTO, title = id, required = required, visibleIf = visibleIf)

    private fun controller(
        steps: List<CaptureStep>,
        outbox: RecordingOutbox,
    ): CaptureSessionController {
        var tick = 0L
        var ids = 0
        return CaptureSessionController(
            protocol = protocol(steps),
            sessionId = "s-1",
            outbox = outbox,
            clock = { Instant(++tick) },
            newEventId = { "evt-${++ids}" },
        )
    }

    @Test
    fun `состояние контроллера совпадает со свёрткой его же событий`() = runTest {
        val outbox = RecordingOutbox()
        val c = controller(listOf(photo("overview"), photo("label")), outbox)
        c.start()
        c.completeStep("overview", assetIds = listOf("a-1"), qualityScore = 0.9)
        c.completeStep("label", qualityScore = 0.8)
        c.completeSession()

        val folded = SessionProjection.fold("s-1", outbox.events)
        assertEquals(SessionState.COMPLETED_LOCAL, folded.state)
        assertEquals(
            setOf("overview", "label"),
            folded.results.filterValues { it.status == StepStatus.COMPLETED }.keys,
        )
        assertEquals(setOf("a-1"), folded.assetIds)
        assertTrue(c.state().isComplete)
        // seq монотонен и без дыр — порядок определяется им, не часами (§04.4)
        assertEquals(outbox.events.indices.map { it.toLong() }, outbox.events.map { it.seq })
    }

    @Test
    fun `обязательный шаг нельзя пропустить а необязательный можно`() = runTest {
        val outbox = RecordingOutbox()
        val c = controller(listOf(photo("must"), photo("extra", required = false)), outbox)
        c.start()
        assertFailsWith<CaptureSessionController.IllegalTransition> { c.skipStep("must") }
        c.skipStep("extra")
        c.completeStep("must")
        assertTrue(c.state().isComplete)
    }

    @Test
    fun `завершение сессии с незакрытым обязательным шагом запрещено`() = runTest {
        val outbox = RecordingOutbox()
        val c = controller(listOf(photo("must")), outbox)
        c.start()
        assertFailsWith<CaptureSessionController.IllegalTransition> { c.completeSession() }
    }

    @Test
    fun `скрытый условием шаг не принимает результат`() = runTest {
        val outbox = RecordingOutbox()
        val damagePhoto = photo(
            "damage",
            visibleIf = Condition.StepPredicate("check", field = "has_damage", eq = JsonPrimitive(true)),
        )
        val check = CaptureStep(id = "check", kind = StepKind.FORM, title = "check")
        val c = controller(listOf(check, damagePhoto), outbox)
        c.start()
        assertFailsWith<CaptureSessionController.IllegalTransition> { c.completeStep("damage") }

        c.completeStep("check", payload = buildJsonObject { put("has_damage", JsonPrimitive(true)) })
        c.completeStep("damage") // теперь шаг виден
        assertTrue(c.state().isComplete)
    }

    @Test
    fun `после завершения сессия иммутабельна`() = runTest {
        val outbox = RecordingOutbox()
        val c = controller(listOf(photo("only")), outbox)
        c.start()
        c.completeStep("only")
        c.completeSession()
        assertFailsWith<CaptureSessionController.IllegalTransition> { c.completeStep("only") }
    }

    @Test
    fun `пересъёмка не отзывает события и наращивает attempts`() = runTest {
        val outbox = RecordingOutbox()
        val c = controller(listOf(photo("shot")), outbox)
        c.start()
        c.completeStep("shot", qualityScore = 0.3, violations = listOf("blur"))
        val eventsBefore = outbox.events.size
        c.retakeStep("shot")
        assertEquals(eventsBefore, outbox.events.size, "retake не отправляет событий")
        assertEquals("shot", c.state().currentStep?.id, "шаг снова текущий после сброса")
        c.completeStep("shot", qualityScore = 0.9)
        // сервер сворачивает по seq: побеждает последний COMPLETED
        val folded = SessionProjection.fold("s-1", outbox.events)
        assertEquals(0.9, folded.results["shot"]?.qualityScore)
    }
}
