package ru.kvant.scan.sync

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import ru.kvant.scan.domain.AccuracyClass
import ru.kvant.scan.domain.Instant
import ru.kvant.scan.domain.SessionState
import ru.kvant.scan.domain.StepResult
import ru.kvant.scan.domain.StepStatus

/**
 * Свёртка лога событий в состояние сессии.
 *
 * Единственный способ получить состояние — доиграть события (ADR-0002).
 * Эта же логика повторена на сервере в `backend/app/projections.py`; общий
 * набор тестов проверяет, что из одного лога получается одинаковый результат.
 * Расхождение здесь означает, что кладовщик и диспетчер видят разные данные.
 */
object SessionProjection {

    data class State(
        val sessionId: String,
        val state: SessionState = SessionState.DRAFT,
        val results: Map<String, StepResult> = emptyMap(),
        val measurementAccuracy: Map<String, AccuracyClass> = emptyMap(),
        val assetIds: Set<String> = emptySet(),
        val lastSeq: Long = -1,
        val startedAt: Instant? = null,
        val finishedAt: Instant? = null,
    )

    /**
     * Применяет события по возрастанию `seq`.
     *
     * Дубликаты по `seq` игнорируются (побеждает первое применённое — §04.5),
     * пропуски в нумерации не мешают: сервер видит валидный префикс сессии,
     * даже если хвост ещё не доехал.
     */
    fun fold(sessionId: String, events: List<ClientEvent>): State {
        var state = State(sessionId = sessionId)
        for (event in events.sortedBy { it.seq }) {
            if (event.seq <= state.lastSeq) continue
            state = apply(state, event)
        }
        return state
    }

    fun apply(state: State, event: ClientEvent): State {
        val next = state.copy(lastSeq = event.seq)
        return when (event.type) {
            EventType.SESSION_STARTED -> next.copy(
                state = SessionState.ACTIVE,
                startedAt = event.deviceTs,
            )

            EventType.STEP_COMPLETED -> {
                val stepId = event.payload.stringOrNull("step_id") ?: return next
                next.copy(
                    results = next.results + (stepId to StepResult(
                        stepId = stepId,
                        status = StepStatus.COMPLETED,
                        payload = event.payload.objectOrEmpty("data"),
                        assetIds = event.payload.stringList("asset_ids"),
                        qualityScore = event.payload["quality_score"]?.jsonPrimitive?.doubleOrNull,
                        violations = event.payload.stringList("violations"),
                        completedAt = event.deviceTs,
                    )),
                    assetIds = next.assetIds + event.payload.stringList("asset_ids"),
                )
            }

            EventType.STEP_SKIPPED -> {
                val stepId = event.payload.stringOrNull("step_id") ?: return next
                next.copy(
                    results = next.results + (stepId to StepResult(
                        stepId = stepId,
                        status = StepStatus.SKIPPED,
                        completedAt = event.deviceTs,
                    ))
                )
            }

            EventType.MEASUREMENT_RECORDED -> {
                val stepId = event.payload.stringOrNull("step_id") ?: return next
                val accuracy = event.payload.stringOrNull("accuracy_class")
                    ?.let { runCatching { AccuracyClass.valueOf(it) }.getOrNull() }
                    ?: return next
                next.copy(measurementAccuracy = next.measurementAccuracy + (stepId to accuracy))
            }

            EventType.CODE_READ -> next

            EventType.ASSET_ATTACHED -> next.copy(
                assetIds = next.assetIds + listOfNotNull(event.payload.stringOrNull("asset_id"))
            )

            EventType.SESSION_PAUSED -> next.copy(state = SessionState.PAUSED)

            EventType.SESSION_COMPLETED -> next.copy(
                state = SessionState.COMPLETED_LOCAL,
                finishedAt = event.deviceTs,
            )

            EventType.SESSION_ABANDONED -> next.copy(
                state = SessionState.ABANDONED,
                finishedAt = event.deviceTs,
            )
        }
    }

    // ── Мелкие безопасные извлекатели: неизвестная форма payload не роняет свёртку ──

    private fun JsonObject.stringOrNull(key: String): String? =
        runCatching { this[key]?.jsonPrimitive?.content }.getOrNull()

    private fun JsonObject.objectOrEmpty(key: String): JsonObject =
        runCatching { this[key]?.jsonObject }.getOrNull() ?: JsonObject(emptyMap())

    private fun JsonObject.stringList(key: String): List<String> =
        runCatching { this[key]?.jsonArray?.map { it.jsonPrimitive.content } }.getOrNull() ?: emptyList()

    @Suppress("unused")
    private fun JsonObject.boolOrNull(key: String): Boolean? =
        runCatching { this[key]?.jsonPrimitive?.booleanOrNull }.getOrNull()
}
