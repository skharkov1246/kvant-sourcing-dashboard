package ru.kvant.scan.capture

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import ru.kvant.scan.domain.AccuracyClass
import ru.kvant.scan.domain.Instant
import ru.kvant.scan.domain.StepResult
import ru.kvant.scan.domain.StepStatus
import ru.kvant.scan.protocol.CaptureProtocol
import ru.kvant.scan.protocol.CaptureStep
import ru.kvant.scan.protocol.ProtocolInterpreter
import ru.kvant.scan.sync.ClientEvent
import ru.kvant.scan.sync.EventType
import ru.kvant.scan.sync.OutboxStore

/**
 * Машина состояний прохождения сессии — ядро того, что показывает
 * `CaptureViewModel` на Android и его SwiftUI-двойник на iOS.
 *
 * Живёт в общем ядре намеренно: платформенные вьюмодели — тонкие обёртки
 * (StateFlow / @Published) вокруг этого класса, вся логика тестируется здесь,
 * без эмулятора. Инвариант класса: каждое изменение состояния синхронно
 * кладёт событие в outbox (правило нулевого ожидания, §04.1) и локальное
 * состояние ОБЯЗАНО совпадать со свёрткой этих событий (`SessionProjection`) —
 * это проверяет общий тест.
 */
class CaptureSessionController(
    private val protocol: CaptureProtocol,
    private val sessionId: String,
    private val outbox: OutboxStore,
    private val clock: () -> Instant,
    /** UUIDv7 от платформы: сортируемый, без координации (§04.4). */
    private val newEventId: () -> String,
    deviceMaxAccuracy: AccuracyClass = AccuracyClass.D,
) {
    private val interpreter = ProtocolInterpreter(protocol, deviceMaxAccuracy)
    private val results = mutableMapOf<String, StepResult>()
    private val accuracy = mutableMapOf<String, AccuracyClass>()
    private var seq: Long = -1
    private var started = false
    private var finished = false

    data class UiState(
        val currentStep: CaptureStep?,
        val doneSteps: Int,
        val totalSteps: Int,
        val warnings: List<String>,
        val isComplete: Boolean,
        val autoAccept: Boolean,
    )

    class IllegalTransition(message: String) : IllegalStateException(message)

    fun state(): UiState {
        val ctx = context()
        val (done, total) = interpreter.progress(ctx)
        val current = interpreter.nextStep(ctx)
        return UiState(
            currentStep = current,
            doneSteps = done,
            totalSteps = total,
            warnings = current?.let { results[it.id]?.violations } ?: emptyList(),
            isComplete = interpreter.isComplete(ctx),
            autoAccept = interpreter.autoAccept(ctx),
        )
    }

    suspend fun start() {
        if (started) return
        started = true
        emit(EventType.SESSION_STARTED, JsonObject(emptyMap()))
    }

    /**
     * Завершение шага. Результат применяется локально и тем же движением
     * уходит событием — UI никогда не ждёт сеть.
     */
    suspend fun completeStep(
        stepId: String,
        payload: JsonObject = JsonObject(emptyMap()),
        assetIds: List<String> = emptyList(),
        qualityScore: Double? = null,
        violations: List<String> = emptyList(),
        measuredAccuracy: AccuracyClass? = null,
    ) {
        requireActive()
        val step = visibleStep(stepId)
        val previous = results[stepId]
        results[stepId] = StepResult(
            stepId = stepId,
            status = StepStatus.COMPLETED,
            payload = payload,
            assetIds = assetIds,
            qualityScore = qualityScore,
            violations = violations,
            attempts = (previous?.attempts ?: 0) + 1,
            completedAt = clock(),
        )
        measuredAccuracy?.let { accuracy[stepId] = it }

        emit(EventType.STEP_COMPLETED, JsonObject(buildMap {
            put("step_id", JsonPrimitive(stepId))
            put("data", payload)
            put("asset_ids", JsonArray(assetIds.map(::JsonPrimitive)))
            qualityScore?.let { put("quality_score", JsonPrimitive(it)) }
            put("violations", JsonArray(violations.map(::JsonPrimitive)))
        }))
        measuredAccuracy?.let {
            emit(EventType.MEASUREMENT_RECORDED, JsonObject(mapOf(
                "step_id" to JsonPrimitive(stepId),
                "accuracy_class" to JsonPrimitive(it.name),
            )))
        }
        if (step.kind != null && assetIds.isNotEmpty()) {
            // ASSET_ATTACHED идёт отдельными событиями — медиа-канал ссылается
            // на них при догрузке файлов (§04.2).
            for (assetId in assetIds) {
                emit(EventType.ASSET_ATTACHED, JsonObject(mapOf("asset_id" to JsonPrimitive(assetId))))
            }
        }
    }

    /** Пропуск разрешён только необязательным видимым шагам (§03.4). */
    suspend fun skipStep(stepId: String) {
        requireActive()
        val step = visibleStep(stepId)
        if (step.required) {
            throw IllegalTransition("Шаг «${step.title}» обязателен — пропуск запрещён протоколом")
        }
        results[stepId] = StepResult(stepId = stepId, status = StepStatus.SKIPPED, completedAt = clock())
        emit(EventType.STEP_SKIPPED, JsonObject(mapOf("step_id" to JsonPrimitive(stepId))))
    }

    /**
     * Пересъёмка: локальный результат сбрасывается, но УЖЕ отправленные события
     * не отзываются — сессия append-only, побеждает последний STEP_COMPLETED
     * с большим seq при серверной свёртке.
     */
    fun retakeStep(stepId: String) {
        val previous = results.remove(stepId) ?: return
        results[stepId] = previous.copy(status = StepStatus.PENDING, violations = emptyList())
    }

    suspend fun completeSession() {
        requireActive()
        if (!interpreter.isComplete(context())) {
            throw IllegalTransition("Сессия не полна: остались обязательные шаги")
        }
        finished = true
        emit(EventType.SESSION_COMPLETED, JsonObject(emptyMap()))
    }

    suspend fun abandonSession(reason: String) {
        requireActive()
        finished = true
        emit(EventType.SESSION_ABANDONED, JsonObject(mapOf("reason" to JsonPrimitive(reason))))
    }

    // ── внутреннее ──

    private fun context() = ProtocolInterpreter.Context(
        results = results.toMap(),
        measurementAccuracy = accuracy.toMap(),
    )

    private fun visibleStep(stepId: String): CaptureStep =
        interpreter.visibleSteps(context()).firstOrNull { it.id == stepId }
            ?: throw IllegalTransition("Шаг $stepId не существует или сейчас не виден")

    private fun requireActive() {
        if (!started) throw IllegalTransition("Сессия не начата — сначала start()")
        if (finished) throw IllegalTransition("Сессия завершена — изменения запрещены (I-3)")
    }

    private suspend fun emit(type: EventType, payload: JsonObject) {
        seq += 1
        outbox.enqueue(
            ClientEvent(
                clientEventId = newEventId(),
                sessionId = sessionId,
                seq = seq,
                type = type,
                deviceTs = clock(),
                payload = payload,
            )
        )
    }
}
