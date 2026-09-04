package ru.kvant.scan.sync

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import ru.kvant.scan.domain.Instant

/**
 * Исходящая очередь устройства.
 *
 * Правило нулевого ожидания (§04.1): ни одно действие пользователя не ждёт сеть.
 * UI пишет событие сюда синхронно и возвращает управление; доставкой занимается
 * фоновый воркер (WorkManager на Android, BGTaskScheduler на iOS).
 */

@Serializable
enum class EventType {
    @SerialName("session.started") SESSION_STARTED,
    @SerialName("step.completed") STEP_COMPLETED,
    @SerialName("step.skipped") STEP_SKIPPED,
    @SerialName("measurement.recorded") MEASUREMENT_RECORDED,
    @SerialName("code.read") CODE_READ,
    @SerialName("asset.attached") ASSET_ATTACHED,
    @SerialName("session.paused") SESSION_PAUSED,
    @SerialName("session.completed") SESSION_COMPLETED,
    @SerialName("session.abandoned") SESSION_ABANDONED,
}

@Serializable
data class ClientEvent(
    /** UUIDv7 — сортируемый по времени, не требует координации. Единственный ключ идемпотентности. */
    @SerialName("client_event_id") val clientEventId: String,
    @SerialName("session_id") val sessionId: String,
    /** Монотонный номер в рамках сессии. Порядок определяется им, а не часами устройства. */
    val seq: Long,
    val type: EventType,
    @SerialName("device_ts") val deviceTs: Instant,
    val payload: JsonObject = JsonObject(emptyMap()),
    @SerialName("trace_id") val traceId: String? = null,
)

@Serializable
enum class EventAck {
    @SerialName("accepted") ACCEPTED,
    /** Повтор после потери ответа — это успех, а не ошибка (§04.4). */
    @SerialName("duplicate") DUPLICATE,
    @SerialName("rejected") REJECTED,
}

@Serializable
data class EventResult(
    @SerialName("client_event_id") val clientEventId: String,
    val status: EventAck,
    val error: SyncError? = null,
)

@Serializable
data class SyncError(
    val code: String,
    val message: String,
    /** Классификация ошибки определяет судьбу события: повтор или карантин. */
    val retryable: Boolean = false,
)

/** Состояние записи в очереди. */
enum class OutboxState {
    PENDING,
    IN_FLIGHT,
    /** Отвергнуто сервером навсегда. Не удаляется — доступно для выгрузки в поддержку. */
    DEAD_LETTER,
}

data class OutboxEntry(
    val event: ClientEvent,
    val state: OutboxState = OutboxState.PENDING,
    val attempts: Int = 0,
    val nextAttemptAt: Instant = Instant(0),
    val lastError: SyncError? = null,
)

/**
 * Локальное хранилище очереди. Реализуется поверх SQLDelight в `data`-слое;
 * интерфейс вынесен сюда, чтобы движок синхронизации тестировался без БД.
 */
interface OutboxStore {
    suspend fun enqueue(event: ClientEvent)

    /**
     * Партия к отправке. Внутри сессии — строгий порядок по `seq`; между
     * сессиями порядок свободный, чтобы одна залипшая сессия не блокировала
     * остальные (§04.3).
     */
    suspend fun nextBatch(limit: Int, now: Instant): List<OutboxEntry>

    suspend fun markInFlight(ids: List<String>)
    suspend fun remove(ids: List<String>)
    suspend fun reschedule(id: String, attempts: Int, nextAttemptAt: Instant, error: SyncError?)
    suspend fun moveToDeadLetter(id: String, error: SyncError)
    suspend fun depth(): Int
}

/**
 * Экспоненциальный backoff с джиттером.
 *
 * Джиттер обязателен: без него после восстановления сети сто телефонов смены
 * ударят в сервер одновременно (§04.3, thundering herd).
 */
object Backoff {
    const val BASE_MS: Long = 2_000
    const val CEILING_MS: Long = 15 * 60 * 1_000

    /**
     * @param attempt номер уже сделанной попытки (0 — первая неудача)
     * @param random значение в [0,1) от источника случайности вызывающей стороны
     */
    fun delayMs(attempt: Int, random: Double): Long {
        val exponential = BASE_MS shl attempt.coerceAtMost(20)
        val capped = exponential.coerceAtMost(CEILING_MS)
        // Полный джиттер: равномерно в [capped/2, capped].
        val half = capped / 2
        return half + (half * random).toLong()
    }
}
