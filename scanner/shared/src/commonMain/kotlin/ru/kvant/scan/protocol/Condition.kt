package ru.kvant.scan.protocol

import kotlinx.serialization.KSerializer
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.descriptors.buildClassSerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonEncoder
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import ru.kvant.scan.domain.AccuracyClass

/**
 * Декларативное условие протокола съёмки.
 *
 * Осознанное ограничение (§03.5): никакого Turing-complete DSL. Интерпретатор
 * обязан быть тривиально верифицируемым, одинаково работать на Android и iOS
 * и никогда не зависать. Цена — редкие сложные сценарии придётся разбивать
 * на два протокола.
 */
@Serializable(with = ConditionSerializer::class)
sealed interface Condition {
    data class All(val conditions: List<Condition>) : Condition
    data class Any(val conditions: List<Condition>) : Condition
    data class Not(val condition: Condition) : Condition

    /** Предикат по общему качеству сессии. */
    data class QualityScoreAtLeast(val threshold: Double) : Condition

    /** Предикат по результату ранее выполненного шага. */
    data class StepPredicate(
        val step: String,
        /** Путь внутри payload шага в точечной нотации: `parsed.gtin`. */
        val field: String? = null,
        val eq: JsonElement? = null,
        val neq: JsonElement? = null,
        val inValues: List<JsonElement>? = null,
        val gt: Double? = null,
        val lt: Double? = null,
        val exists: Boolean? = null,
        val accuracyClassAtLeast: AccuracyClass? = null,
    ) : Condition
}

/**
 * Ручной сериализатор: форма условия определяется набором ключей, а не
 * дискриминатором. Так документ протокола остаётся читаемым для технолога.
 */
object ConditionSerializer : KSerializer<Condition> {
    override val descriptor: SerialDescriptor = buildClassSerialDescriptor("Condition")

    override fun deserialize(decoder: Decoder): Condition {
        val input = decoder as? JsonDecoder
            ?: error("Condition читается только из JSON")
        return fromJson(input.decodeJsonElement().jsonObject)
    }

    override fun serialize(encoder: Encoder, value: Condition) {
        val output = encoder as? JsonEncoder ?: error("Condition пишется только в JSON")
        output.encodeJsonElement(toJson(value))
    }

    fun fromJson(obj: JsonObject): Condition = when {
        "all" in obj -> Condition.All(obj.getValue("all").jsonArray.map { fromJson(it.jsonObject) })
        "any" in obj -> Condition.Any(obj.getValue("any").jsonArray.map { fromJson(it.jsonObject) })
        "not" in obj -> Condition.Not(fromJson(obj.getValue("not").jsonObject))
        "quality_score_at_least" in obj ->
            Condition.QualityScoreAtLeast(obj.getValue("quality_score_at_least").jsonPrimitive.content.toDouble())
        "step" in obj -> Condition.StepPredicate(
            step = obj.getValue("step").jsonPrimitive.content,
            field = obj["field"]?.jsonPrimitive?.content,
            eq = obj["eq"],
            neq = obj["neq"],
            inValues = obj["in"]?.jsonArray?.toList(),
            gt = obj["gt"]?.jsonPrimitive?.content?.toDouble(),
            lt = obj["lt"]?.jsonPrimitive?.content?.toDouble(),
            exists = obj["exists"]?.jsonPrimitive?.content?.toBooleanStrictOrNull(),
            accuracyClassAtLeast = obj["accuracy_class_at_least"]
                ?.jsonPrimitive?.content
                ?.let { AccuracyClass.valueOf(it) },
        )
        else -> error("Неизвестная форма условия: ключи ${obj.keys}")
    }

    private fun toJson(value: Condition): JsonElement = when (value) {
        is Condition.All -> buildJsonObject {
            put("all", kotlinx.serialization.json.JsonArray(value.conditions.map { toJson(it) }))
        }
        is Condition.Any -> buildJsonObject {
            put("any", kotlinx.serialization.json.JsonArray(value.conditions.map { toJson(it) }))
        }
        is Condition.Not -> buildJsonObject { put("not", toJson(value.condition)) }
        is Condition.QualityScoreAtLeast -> buildJsonObject {
            put("quality_score_at_least", JsonPrimitive(value.threshold))
        }
        is Condition.StepPredicate -> buildJsonObject {
            put("step", JsonPrimitive(value.step))
            value.field?.let { put("field", JsonPrimitive(it)) }
            value.eq?.let { put("eq", it) }
            value.neq?.let { put("neq", it) }
            value.inValues?.let { put("in", kotlinx.serialization.json.JsonArray(it)) }
            value.gt?.let { put("gt", JsonPrimitive(it)) }
            value.lt?.let { put("lt", JsonPrimitive(it)) }
            value.exists?.let { put("exists", JsonPrimitive(it)) }
            value.accuracyClassAtLeast?.let { put("accuracy_class_at_least", JsonPrimitive(it.name)) }
        }
    }
}
