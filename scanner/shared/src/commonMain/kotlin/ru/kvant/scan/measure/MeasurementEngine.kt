package ru.kvant.scan.measure

import ru.kvant.scan.domain.AccuracyClass
import ru.kvant.scan.domain.MeasureMethod

/**
 * Граница между общим ядром и платформенной работой с сенсорами (ADR-0001).
 *
 * Ядро знает, КАКОЙ класс точности требуется и КАК его выбрать; сама работа с
 * ARCore / ARKit / камерой реализуется нативно и подключается через этот интерфейс.
 * Здесь нет ни одной ссылки на платформенный API — иначе iOS и Android разъедутся
 * именно в том месте, где расхождение дороже всего.
 */
interface MeasurementEngine {

    /** Что реально умеет это устройство. Определяется ДО начала шага (§05.2). */
    val capabilities: Capabilities

    /**
     * Запускает сеанс обмера. Возвращает результат либо причину, по которой
     * выбранный метод неприменим — тогда вызывающая сторона спускается на
     * ступень ниже по лестнице деградации.
     */
    suspend fun measure(request: Request): Result

    data class Capabilities(
        val supportedMethods: Set<MeasureMethod>,
        /** Лучший достижимый класс на этом устройстве. */
        val maxAccuracy: AccuracyClass,
        val hasDepthSensor: Boolean,
        val canCaptureDepthFrames: Boolean,
    )

    data class Request(
        val target: String,
        val method: MeasureMethod,
        val markerType: String? = null,
        val markerSizeMm: Double? = null,
        /** Сохранять ли сырьё для серверного пересчёта (raw-first, ADR-0008). */
        val captureDepth: Boolean = true,
        /** Сколько кадров накапливать для медианы (§05.3, шаг 5). */
        val frameBudget: Int = 12,
    )

    sealed interface Result {
        data class Success(
            val method: MeasureMethod,
            val accuracyClass: AccuracyClass,
            val lengthMm: Double,
            val widthMm: Double,
            val heightMm: Double,
            /** Оценка из разброса между кадрами, а не выдуманное число. */
            val confidence: Double,
            /** Идентификаторы сохранённых кадров, карт глубины и облаков точек. */
            val assetIds: List<String>,
            val framesUsed: Int,
        ) : Result

        /** Метод неприменим здесь и сейчас — спускаемся на ступень ниже. */
        data class Unavailable(val reason: Reason, val message: String) : Result

        /** Пользователь прервал обмер. */
        data object Cancelled : Result
    }

    enum class Reason {
        NO_DEPTH_SUPPORT,
        PLANE_NOT_FOUND,
        SUBJECT_NOT_SEGMENTED,
        MARKER_NOT_DETECTED,
        TOO_DARK,
        TOO_FEW_FRAMES,
        IMPLAUSIBLE_RESULT,
    }
}

/**
 * Лестница деградации обмера (§05.2, ADR-0005).
 *
 * Возвращает методы от лучшего к самому примитивному. Последняя ступень —
 * ручной ввод — доступна всегда, поэтому тупика не бывает.
 */
object MeasurementLadder {

    fun ladderFor(capabilities: MeasurementEngine.Capabilities): List<MeasureMethod> =
        listOf(
            MeasureMethod.LIDAR,
            MeasureMethod.AR_DEPTH,
            MeasureMethod.MARKER,
            MeasureMethod.MANUAL,
        ).filter { it == MeasureMethod.MANUAL || it in capabilities.supportedMethods }

    /**
     * Пробует методы по порядку, пока один не сработает.
     *
     * @param minAccuracy минимально приемлемый класс из протокола. Если ни один
     *   доступный метод его не даёт, всё равно доходим до ручного ввода — но
     *   результат честно помечается фактическим классом, а не требуемым.
     */
    suspend fun measureWithFallback(
        engine: MeasurementEngine,
        request: MeasurementEngine.Request,
        minAccuracy: AccuracyClass,
        /** Вызывается, когда фактический класс оказался хуже требуемого протоколом. */
        onDowngrade: (actual: AccuracyClass, required: AccuracyClass) -> Unit = { _, _ -> },
        onFallback: (from: MeasureMethod, to: MeasureMethod, reason: MeasurementEngine.Reason) -> Unit = { _, _, _ -> },
    ): MeasurementEngine.Result {
        val ladder = ladderFor(engine.capabilities)
        var lastFailure: MeasurementEngine.Result.Unavailable? = null

        for ((index, method) in ladder.withIndex()) {
            when (val result = engine.measure(request.copy(method = method))) {
                is MeasurementEngine.Result.Success -> {
                    if (!result.accuracyClass.atLeast(minAccuracy)) {
                        onDowngrade(result.accuracyClass, minAccuracy)
                    }
                    return result
                }
                is MeasurementEngine.Result.Cancelled -> return result
                is MeasurementEngine.Result.Unavailable -> {
                    lastFailure = result
                    ladder.getOrNull(index + 1)?.let { next ->
                        onFallback(method, next, result.reason)
                    }
                }
            }
        }

        return lastFailure ?: MeasurementEngine.Result.Unavailable(
            MeasurementEngine.Reason.NO_DEPTH_SUPPORT,
            "Ни один метод обмера недоступен, включая ручной ввод — это баг конфигурации.",
        )
    }
}
