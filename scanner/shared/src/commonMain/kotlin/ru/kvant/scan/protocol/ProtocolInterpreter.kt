package ru.kvant.scan.protocol

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonPrimitive
import ru.kvant.scan.domain.AccuracyClass
import ru.kvant.scan.domain.StepResult
import ru.kvant.scan.domain.StepStatus

/**
 * Интерпретатор протокола съёмки.
 *
 * Это то место, где «приложение ведёт кладовщика по шагам». Логика намеренно
 * общая для Android и iOS (ADR-0001): расхождение здесь = расхождение данных.
 * Тот же порядок вычисления повторён на сервере в `backend/app/protocol.py`,
 * и общий набор тестов проверяет, что оба дают одинаковый результат.
 */
class ProtocolInterpreter(
    private val protocol: CaptureProtocol,
    /** Лучший класс точности, доступный на этом устройстве. */
    private val deviceMaxAccuracy: AccuracyClass,
) {

    /** Состояние прохождения сессии, из которого вычисляются условия. */
    data class Context(
        val results: Map<String, StepResult>,
        val measurementAccuracy: Map<String, AccuracyClass> = emptyMap(),
    ) {
        /**
         * Средневзвешенная оценка качества по завершённым шагам.
         * Шаг без оценки считается идеальным — иначе необязательные шаги
         * несправедливо тянули бы сессию вниз.
         */
        val qualityScore: Double
            get() {
                val scored = results.values.filter { it.status == StepStatus.COMPLETED }
                if (scored.isEmpty()) return 0.0
                return scored.sumOf { it.qualityScore ?: 1.0 } / scored.size
            }
    }

    /** Шаги, которые пользователь должен увидеть при текущем состоянии сессии. */
    fun visibleSteps(context: Context): List<CaptureStep> =
        protocol.steps.filter { step ->
            step.isSupported && (step.visibleIf?.let { evaluate(it, context) } ?: true)
        }

    /** Следующий незавершённый шаг или null, если обязательная часть пройдена. */
    fun nextStep(context: Context): CaptureStep? =
        visibleSteps(context).firstOrNull { step ->
            val result = context.results[step.id]
            when {
                result == null -> true
                result.status == StepStatus.PENDING -> true
                result.status == StepStatus.BLOCKED -> true
                else -> false
            }
        }

    /** Все ли обязательные видимые шаги завершены. */
    fun isComplete(context: Context): Boolean =
        visibleSteps(context)
            .filter { it.required }
            .all { context.results[it.id]?.status == StepStatus.COMPLETED }

    /** Прогресс для индикатора — по видимым обязательным шагам. */
    fun progress(context: Context): Pair<Int, Int> {
        val required = visibleSteps(context).filter { it.required }
        val done = required.count { context.results[it.id]?.status == StepStatus.COMPLETED }
        return done to required.size
    }

    /**
     * Может ли устройство выполнить шаг обмера с требуемой точностью.
     * Если нет — предлагается ступень ниже с явной отметкой в результате (§05.2).
     */
    fun measurementFallback(step: CaptureStep): AccuracyClass? {
        val spec = step.measure ?: return null
        val required = AccuracyClass.valueOf(spec.minAccuracyClass)
        return if (deviceMaxAccuracy.atLeast(required)) required else deviceMaxAccuracy
    }

    /** Решение об автоприёме по критериям протокола. Считается и на сервере. */
    fun autoAccept(context: Context): Boolean {
        val acceptance = protocol.acceptance ?: return false
        acceptance.rejectIf?.let { if (evaluate(it, context)) return false }
        return acceptance.autoAcceptIf?.let { evaluate(it, context) } ?: false
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Вычисление условий
    // ─────────────────────────────────────────────────────────────────────────

    fun evaluate(condition: Condition, context: Context): Boolean = when (condition) {
        is Condition.All -> condition.conditions.all { evaluate(it, context) }
        is Condition.Any -> condition.conditions.any { evaluate(it, context) }
        is Condition.Not -> !evaluate(condition.condition, context)
        is Condition.QualityScoreAtLeast -> context.qualityScore >= condition.threshold
        is Condition.StepPredicate -> evaluateStep(condition, context)
    }

    private fun evaluateStep(p: Condition.StepPredicate, context: Context): Boolean {
        val result = context.results[p.step] ?: return p.exists == false

        p.accuracyClassAtLeast?.let { required ->
            val actual = context.measurementAccuracy[p.step] ?: return false
            return actual.atLeast(required)
        }

        val value = p.field?.let { resolvePath(result.payload, it) }
            ?: JsonPrimitive(result.status.name)

        p.exists?.let { expected ->
            val present = value != JsonNull
            if (present != expected) return false
        }
        p.eq?.let { if (!jsonEquals(value, it)) return false }
        p.neq?.let { if (jsonEquals(value, it)) return false }
        p.inValues?.let { list -> if (list.none { jsonEquals(value, it) }) return false }
        p.gt?.let { bound -> if ((asDouble(value) ?: return false) <= bound) return false }
        p.lt?.let { bound -> if ((asDouble(value) ?: return false) >= bound) return false }

        // Предикат без операторов означает «шаг выполнен».
        val hasOperator = p.eq != null || p.neq != null || p.inValues != null ||
            p.gt != null || p.lt != null || p.exists != null
        if (!hasOperator) return result.status == StepStatus.COMPLETED

        return true
    }

    /** Разбор точечного пути `parsed.gtin` внутри payload шага. */
    private fun resolvePath(root: JsonObject, path: String): JsonElement {
        var current: JsonElement = root
        for (segment in path.split('.')) {
            val obj = current as? JsonObject ?: return JsonNull
            current = obj[segment] ?: return JsonNull
        }
        return current
    }

    private fun jsonEquals(a: JsonElement, b: JsonElement): Boolean {
        if (a == b) return true
        val pa = a as? JsonPrimitive ?: return false
        val pb = b as? JsonPrimitive ?: return false
        // Сравнение по содержимому: "true" из формы и true из JSON — одно и то же.
        return pa.content == pb.content
    }

    private fun asDouble(value: JsonElement): Double? =
        (value as? JsonPrimitive)?.let { it.doubleOrNull ?: it.content.toDoubleOrNull() }

    private fun asBoolean(value: JsonElement): Boolean? =
        (value as? JsonPrimitive)?.let { it.booleanOrNull ?: it.content.toBooleanStrictOrNull() }
}
