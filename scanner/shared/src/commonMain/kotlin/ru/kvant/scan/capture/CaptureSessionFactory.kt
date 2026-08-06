package ru.kvant.scan.capture

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import ru.kvant.scan.domain.AccuracyClass
import ru.kvant.scan.domain.Instant
import ru.kvant.scan.protocol.CaptureProtocol
import ru.kvant.scan.sync.LocalTask
import ru.kvant.scan.sync.OutboxStore
import ru.kvant.scan.sync.TaskStore

/**
 * Сборка сессии съёмки по заданию: кнопка «Начать съёмку» на экране деталей
 * приводит СЮДА, а не в платформенный код.
 *
 * Протокол берётся из локального TaskStore (офлайн-первичность: он уже
 * синхронизирован pull-каналом); session.started получает protocol-ref и
 * task_id — по ним сервер найдёт протокол для приёмки и подвинет задание
 * после вердикта контролёра. Отсутствие протокола — честная ошибка ДО
 * начала съёмки, а не сюрприз на середине сессии.
 */
object CaptureSessionFactory {

    private val json = Json { ignoreUnknownKeys = true }

    class ProtocolMissing(message: String) : IllegalStateException(message)

    suspend fun forTask(
        task: LocalTask,
        tasks: TaskStore,
        sessionId: String,
        outbox: OutboxStore,
        clock: () -> Instant,
        newEventId: () -> String,
        deviceMaxAccuracy: AccuracyClass = AccuracyClass.D,
    ): CaptureSessionController {
        // Задание с пином версии (I-4) берёт ровно её; без пина — свежайшую
        // синхронизированную (сервер уже отсёк несовместимые при выдаче).
        val document = task.protocolVersion
            ?.let { tasks.protocol(task.protocolCode, it) }
            ?: tasks.latestProtocol(task.protocolCode)
            ?: throw ProtocolMissing(
                "Протокол ${task.protocolCode} не синхронизирован — обновите список заданий")
        val protocol = json.decodeFromJsonElement(CaptureProtocol.serializer(), document)
        val version = document["version"]?.jsonPrimitive?.intOrNull ?: protocol.version
        return CaptureSessionController(
            protocol = protocol,
            sessionId = sessionId,
            outbox = outbox,
            clock = clock,
            newEventId = newEventId,
            deviceMaxAccuracy = deviceMaxAccuracy,
            startContext = buildJsonObject {
                put("protocol", "${task.protocolCode}@$version")
                put("task_id", task.id)
            },
        )
    }

    /**
     * Инициативная сессия — без задания. Кладовщик оказался у стеллажа и
     * решил наполнить базу сам: съёмка идёт по протоколу-маршрутизатору
     * (он сам определяет категорию детали), в session.started нет task_id,
     * зато есть origin=initiative — сервер и контролёр видят, что это
     * добровольный вклад, а не работа по заданию.
     */
    suspend fun initiative(
        tasks: TaskStore,
        sessionId: String,
        outbox: OutboxStore,
        clock: () -> Instant,
        newEventId: () -> String,
        deviceMaxAccuracy: AccuracyClass = AccuracyClass.D,
        protocolCode: String = "kv_router",
    ): CaptureSessionController {
        val document = tasks.latestProtocol(protocolCode)
            ?: throw ProtocolMissing(
                "Протокол $protocolCode не синхронизирован — обновите список заданий")
        val protocol = json.decodeFromJsonElement(CaptureProtocol.serializer(), document)
        val version = document["version"]?.jsonPrimitive?.intOrNull ?: protocol.version
        return CaptureSessionController(
            protocol = protocol,
            sessionId = sessionId,
            outbox = outbox,
            clock = clock,
            newEventId = newEventId,
            deviceMaxAccuracy = deviceMaxAccuracy,
            startContext = buildJsonObject {
                put("protocol", "$protocolCode@$version")
                put("origin", "initiative")
            },
        )
    }
}
