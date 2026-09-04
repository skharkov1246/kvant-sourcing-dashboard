package ru.kvant.scan.domain

import kotlin.jvm.JvmInline
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/**
 * Доменные модели общего ядра.
 *
 * Здесь живёт только то, что обязано вести себя ОДИНАКОВО на Android и iOS.
 * Всё, что касается отрисовки, камеры и сенсоров, остаётся в платформенных слоях
 * (см. ADR-0001).
 */

// ─────────────────────────────────────────────────────────────────────────────
// Задание
// ─────────────────────────────────────────────────────────────────────────────

@Serializable
enum class TaskState {
    @SerialName("NEW") NEW,
    @SerialName("ASSIGNED") ASSIGNED,
    @SerialName("IN_PROGRESS") IN_PROGRESS,
    @SerialName("SUBMITTED") SUBMITTED,
    @SerialName("IN_REVIEW") IN_REVIEW,
    @SerialName("REWORK") REWORK,
    @SerialName("ACCEPTED") ACCEPTED,
    @SerialName("REJECTED") REJECTED,
    @SerialName("EXPORTED") EXPORTED,
    @SerialName("CANCELLED") CANCELLED,
    @SerialName("EXPIRED") EXPIRED;

    /**
     * Состояния, до которых устройство вправе доводить задание самостоятельно.
     * Всё, что дальше SUBMITTED, — серверная зона ответственности (§02.4).
     */
    val isDeviceOwned: Boolean
        get() = this == ASSIGNED || this == IN_PROGRESS || this == SUBMITTED
}

@Serializable
data class TaskSubject(
    val sku: String? = null,
    val gtin: String? = null,
    val name: String? = null,
    @SerialName("expected_qty") val expectedQty: Double? = null,
    val location: String? = null,
    val batch: String? = null,
)

@Serializable
data class ScanTask(
    val id: String,
    @SerialName("site_id") val siteId: String? = null,
    @SerialName("protocol_code") val protocolCode: String,
    @SerialName("protocol_version") val protocolVersion: Int? = null,
    val subject: TaskSubject,
    val priority: Int = 100,
    @SerialName("due_at") val dueAt: Instant? = null,
    val state: TaskState,
    @SerialName("created_at") val createdAt: Instant,
)

// ─────────────────────────────────────────────────────────────────────────────
// Сессия
// ─────────────────────────────────────────────────────────────────────────────

@Serializable
enum class SessionState {
    DRAFT, ACTIVE, PAUSED, COMPLETED_LOCAL, UPLOADING, SYNCED, ABANDONED
}

@Serializable
data class ScanSession(
    val id: String,
    @SerialName("task_id") val taskId: String,
    @SerialName("device_id") val deviceId: String,
    @SerialName("user_id") val userId: String,
    /** Версия протокола пинуется на старте и не меняется до конца сессии (инвариант I-4). */
    @SerialName("protocol_code") val protocolCode: String,
    @SerialName("protocol_version") val protocolVersion: Int,
    @SerialName("corrects_session_id") val correctsSessionId: String? = null,
    val state: SessionState,
    @SerialName("started_at") val startedAt: Instant,
    @SerialName("finished_at") val finishedAt: Instant? = null,
)

// ─────────────────────────────────────────────────────────────────────────────
// Результат шага
// ─────────────────────────────────────────────────────────────────────────────

@Serializable
enum class StepStatus { PENDING, COMPLETED, SKIPPED, UNSUPPORTED, BLOCKED }

@Serializable
data class StepResult(
    @SerialName("step_id") val stepId: String,
    val status: StepStatus,
    /** Полезная нагрузка шага. Форма зависит от `kind` и описана протоколом. */
    val payload: JsonObject = JsonObject(emptyMap()),
    @SerialName("asset_ids") val assetIds: List<String> = emptyList(),
    /** Оценка качества [0,1], посчитанная на устройстве до отправки. */
    @SerialName("quality_score") val qualityScore: Double? = null,
    val violations: List<String> = emptyList(),
    val attempts: Int = 1,
    @SerialName("completed_at") val completedAt: Instant? = null,
)

// ─────────────────────────────────────────────────────────────────────────────
// Обмер
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Класс точности обмера. Порядок важен: A — лучший.
 * Никогда не «улучшается» задним числом — серверное уточнение создаёт новую
 * запись со ссылкой [Measurement.refinedFrom] (инвариант I-5).
 */
@Serializable
enum class AccuracyClass {
    @SerialName("A") A,
    @SerialName("B") B,
    @SerialName("C") C,
    @SerialName("D") D;

    /** Меньше — точнее. Используется в сравнении `accuracy_class_at_least`. */
    val rank: Int get() = ordinal

    fun atLeast(required: AccuracyClass): Boolean = rank <= required.rank
}

@Serializable
enum class MeasureMethod {
    @SerialName("lidar") LIDAR,
    @SerialName("ar_depth") AR_DEPTH,
    @SerialName("marker") MARKER,
    @SerialName("manual") MANUAL,
    @SerialName("photogrammetry") PHOTOGRAMMETRY;

    val accuracyClass: AccuracyClass
        get() = when (this) {
            LIDAR, PHOTOGRAMMETRY -> AccuracyClass.A
            AR_DEPTH -> AccuracyClass.B
            MARKER -> AccuracyClass.C
            MANUAL -> AccuracyClass.D
        }
}

@Serializable
data class Measurement(
    val id: String,
    val method: MeasureMethod,
    @SerialName("accuracy_class") val accuracyClass: AccuracyClass,
    @SerialName("length_mm") val lengthMm: Double? = null,
    @SerialName("width_mm") val widthMm: Double? = null,
    @SerialName("height_mm") val heightMm: Double? = null,
    @SerialName("weight_g") val weightG: Double? = null,
    val confidence: Double? = null,
    /** Инвариант I-5: габаритов «из ниоткуда» не существует. */
    @SerialName("source_asset_ids") val sourceAssetIds: List<String>,
    @SerialName("refined_from") val refinedFrom: String? = null,
) {
    val volumeMm3: Double?
        get() {
            val l = lengthMm ?: return null
            val w = widthMm ?: return null
            val h = heightMm ?: return null
            return l * w * h
        }

    /** Простая проверка на здравый смысл — выполняется до отправки (§05.3, шаг 6). */
    fun isPlausible(maxEdgeMm: Double = 4000.0): Boolean {
        val v = volumeMm3 ?: return method == MeasureMethod.MANUAL
        if (v <= 0.0) return false
        return listOfNotNull(lengthMm, widthMm, heightMm).all { it in 1.0..maxEdgeMm }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Коды
// ─────────────────────────────────────────────────────────────────────────────

@Serializable
enum class Symbology {
    @SerialName("data_matrix") DATA_MATRIX,
    @SerialName("qr") QR,
    @SerialName("code_128") CODE_128,
    @SerialName("gs1_128") GS1_128,
    @SerialName("ean_13") EAN_13,
    @SerialName("ean_8") EAN_8,
    @SerialName("itf_14") ITF_14,
    @SerialName("pdf_417") PDF_417,
    @SerialName("aztec") AZTEC,
}

@Serializable
data class CodeRead(
    val id: String,
    val symbology: Symbology,
    @SerialName("raw_value") val rawValue: String,
    /** Результат разбора GS1 / «Честного знака», если протокол его запросил. */
    val parsed: Map<String, String> = emptyMap(),
    /** camera | manual — важно для критериев автоприёма. */
    val source: String = "camera",
)

// ─────────────────────────────────────────────────────────────────────────────
// Активы
// ─────────────────────────────────────────────────────────────────────────────

@Serializable
enum class AssetKind {
    @SerialName("photo") PHOTO,
    @SerialName("video") VIDEO,
    @SerialName("depth_map") DEPTH_MAP,
    @SerialName("point_cloud") POINT_CLOUD,
    @SerialName("audio") AUDIO,
}

@Serializable
enum class UploadState { PENDING, UPLOADING, COMPLETE, VERIFIED }

@Serializable
data class Asset(
    val id: String,
    @SerialName("session_id") val sessionId: String,
    @SerialName("step_id") val stepId: String,
    val kind: AssetKind,
    val mime: String,
    val bytes: Long,
    /** Считается на устройстве ДО отправки и сверяется сервером (инвариант I-1). */
    val sha256: String,
    @SerialName("local_path") val localPath: String,
    @SerialName("capture_meta") val captureMeta: JsonObject = JsonObject(emptyMap()),
    @SerialName("upload_state") val uploadState: UploadState = UploadState.PENDING,
) {
    /** Локальный файл удаляется только после подтверждённой сверки (I-1). */
    val isSafeToDeleteLocally: Boolean get() = uploadState == UploadState.VERIFIED
}

// ─────────────────────────────────────────────────────────────────────────────
// Возможности устройства
// ─────────────────────────────────────────────────────────────────────────────

@Serializable
data class DeviceCapabilities(
    /** Максимальная поддерживаемая версия формата протокола съёмки (§03.7). */
    @SerialName("schema_version") val schemaVersion: Int,
    @SerialName("app_version") val appVersion: String,
    val platform: String,
    /** lidar | ar_depth | none */
    val depth: String,
    @SerialName("max_accuracy_class") val maxAccuracyClass: AccuracyClass,
    @SerialName("camera_mp") val cameraMp: Double,
    @SerialName("free_storage_mb") val freeStorageMb: Long,
)

/** Минимальная обёртка над временем, чтобы не тянуть зависимость в общий домен. */
@Serializable
@JvmInline
value class Instant(val epochMillis: Long) : Comparable<Instant> {
    override fun compareTo(other: Instant): Int = epochMillis.compareTo(other.epochMillis)
}
