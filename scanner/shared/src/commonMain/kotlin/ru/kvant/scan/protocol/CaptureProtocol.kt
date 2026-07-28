package ru.kvant.scan.protocol

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

/**
 * Модель протокола съёмки — версионируемого документа, который приложение
 * ИСПОЛНЯЕТ (ADR-0003). Форма документа описана в
 * `docs/schemas/capture-protocol.schema.json`; пример — `pallet_general_v7.json`.
 *
 * Правило совместимости (§03.7): неизвестные поля внутри известного шага
 * игнорируются и складываются в [CaptureStep.rawExtras]; неизвестный `kind`
 * помечает шаг как неподдерживаемый, но не роняет разбор документа.
 */

/** Версия формата, которую понимает это ядро. Выше — отказ исполнять. */
const val SUPPORTED_SCHEMA_VERSION: Int = 1

@Serializable
enum class ProtocolStatus {
    @SerialName("draft") DRAFT,
    @SerialName("active") ACTIVE,
    @SerialName("deprecated") DEPRECATED,
}

@Serializable
enum class StepKind {
    @SerialName("photo") PHOTO,
    @SerialName("video") VIDEO,
    @SerialName("measure") MEASURE,
    @SerialName("code") CODE,
    @SerialName("form") FORM,
    @SerialName("weight") WEIGHT,
    @SerialName("note") NOTE,
    @SerialName("confirm") CONFIRM,
}

@Serializable
enum class OnViolation {
    /** Не пускать дальше. Применяется точечно — см. §03.4. */
    @SerialName("block") BLOCK,
    /** Предупредить и пустить. Поведение по умолчанию. */
    @SerialName("warn") WARN,
    /** Только записать в метаданные для серверного анализа. */
    @SerialName("ignore") IGNORE,
}

@Serializable
data class ProtocolSettings(
    @SerialName("require_geo") val requireGeo: Boolean = false,
    @SerialName("min_battery_pct") val minBatteryPct: Int = 5,
    @SerialName("estimated_duration_s") val estimatedDurationS: Int? = null,
    @SerialName("allow_pause") val allowPause: Boolean = true,
    @SerialName("allow_handoff") val allowHandoff: Boolean = true,
    @SerialName("retention_days") val retentionDays: Int? = null,
)

@Serializable
data class QualitySpec(
    @SerialName("min_resolution_mp") val minResolutionMp: Double? = null,
    @SerialName("max_blur") val maxBlur: Double? = null,
    @SerialName("min_lux") val minLux: Double? = null,
    @SerialName("glare_ratio") val glareRatio: Double? = null,
    @SerialName("require_subject_fill_pct") val requireSubjectFillPct: List<Double>? = null,
    @SerialName("reject_duplicates") val rejectDuplicates: Boolean = true,
    @SerialName("on_violation") val onViolation: OnViolation = OnViolation.WARN,
)

@Serializable
data class MarkerSpec(
    val type: String,
    @SerialName("size_mm") val sizeMm: Double,
)

@Serializable
data class MeasureSpec(
    val target: String,
    @SerialName("min_accuracy_class") val minAccuracyClass: String = "C",
    val marker: MarkerSpec? = null,
    @SerialName("capture_depth") val captureDepth: Boolean = true,
    @SerialName("on_violation") val onViolation: OnViolation = OnViolation.WARN,
)

@Serializable
data class CodeSpec(
    val symbologies: List<String>,
    val parse: String = "none",
    @SerialName("allow_manual") val allowManual: Boolean = true,
    @SerialName("expect_prefix") val expectPrefix: String? = null,
    val quality: QualitySpec? = null,
)

@Serializable
data class FormField(
    val key: String,
    val type: String,
    val label: String? = null,
    val required: Boolean = false,
    val pattern: String? = null,
    val min: Double? = null,
    val max: Double? = null,
    val unit: String? = null,
    val options: List<String>? = null,
    /** Справочник, синхронизируемый на устройство: работает офлайн. */
    val source: String? = null,
    val suggest: String? = null,
    val default: JsonElement? = null,
)

@Serializable
data class FormSpec(val fields: List<FormField>)

@Serializable
data class VideoSpec(
    @SerialName("duration_s") val durationS: Int = 10,
    @SerialName("require_motion") val requireMotion: Boolean = true,
    @SerialName("max_bitrate_kbps") val maxBitrateKbps: Int? = null,
)

@Serializable
data class WeightSpec(
    val unit: String = "kg",
    val source: String = "any",
    val min: Double? = null,
    val max: Double? = null,
)

@Serializable
data class ConfirmSpec(
    val text: String,
    @SerialName("require_signature") val requireSignature: Boolean = false,
)

@Serializable
data class RepeatSpec(val min: Int = 1, val max: Int = 1)

@Serializable
data class CaptureStep(
    val id: String,
    /** null — `kind` неизвестен этой версии приложения (§03.7, правило 2). */
    val kind: StepKind? = null,
    val title: String,
    val instruction: String? = null,
    val required: Boolean = true,
    @SerialName("hint_overlay") val hintOverlay: String? = null,
    val repeat: RepeatSpec? = null,
    @SerialName("visible_if") val visibleIf: Condition? = null,
    val quality: QualitySpec? = null,
    val measure: MeasureSpec? = null,
    val code: CodeSpec? = null,
    val form: FormSpec? = null,
    val video: VideoSpec? = null,
    val weight: WeightSpec? = null,
    val confirm: ConfirmSpec? = null,
    /** Неизвестные поля документа — сохраняются, не ломают исполнение. */
    @SerialName("raw_extras") val rawExtras: JsonObject = JsonObject(emptyMap()),
) {
    val isSupported: Boolean get() = kind != null
}

@Serializable
data class Acceptance(
    @SerialName("auto_accept_if") val autoAcceptIf: Condition? = null,
    @SerialName("reject_if") val rejectIf: Condition? = null,
)

@Serializable
data class ProtocolSignature(
    val alg: String,
    @SerialName("key_id") val keyId: String,
    val value: String,
)

@Serializable
data class CaptureProtocol(
    @SerialName("schema_version") val schemaVersion: Int,
    val code: String,
    val version: Int,
    val status: ProtocolStatus,
    val scope: String,
    val title: String,
    val description: String? = null,
    val settings: ProtocolSettings = ProtocolSettings(),
    val steps: List<CaptureStep>,
    val acceptance: Acceptance? = null,
    val signature: ProtocolSignature? = null,
) {
    /** Идентификатор версии в человекочитаемом виде: `pallet_general@7`. */
    val ref: String get() = "$code@$version"

    /**
     * Проверка совместимости перед стартом сессии.
     * Второй эшелон защиты: основной отсев несовместимых заданий делает сервер
     * при раздаче (§03.7, правило 4).
     */
    fun compatibility(supportedSchemaVersion: Int = SUPPORTED_SCHEMA_VERSION): Compatibility {
        if (schemaVersion > supportedSchemaVersion) {
            return Compatibility.RequiresAppUpdate(
                "Протокол $ref рассчитан на более новую версию приложения " +
                    "(формат $schemaVersion, поддерживается $supportedSchemaVersion)."
            )
        }
        val blockingSteps = steps.filter { !it.isSupported && it.required }
        if (blockingSteps.isNotEmpty()) {
            return Compatibility.RequiresAppUpdate(
                "Протокол $ref содержит обязательные шаги, не поддерживаемые этой версией " +
                    "приложения: " + blockingSteps.joinToString(", ") { it.id }
            )
        }
        val skippable = steps.filter { !it.isSupported }.map { it.id }
        return if (skippable.isEmpty()) Compatibility.Ok else Compatibility.PartiallySupported(skippable)
    }
}

sealed interface Compatibility {
    data object Ok : Compatibility

    /** Необязательные шаги, которые будут пропущены как `UNSUPPORTED`. */
    data class PartiallySupported(val unsupportedStepIds: List<String>) : Compatibility

    /** Молча пропускать нельзя — пользователю показывается понятное объяснение. */
    data class RequiresAppUpdate(val reason: String) : Compatibility
}
