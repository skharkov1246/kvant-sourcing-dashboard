package ru.kvant.scan.sync

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import ru.kvant.scan.directory.DirectoryStore

/**
 * Локальное состояние pull-канала: задания и протоколы, полученные с сервера.
 *
 * Экран списка заданий читает ОТСЮДА, а не из сети — офлайн-первичность
 * (§04.1): список работает в подвале склада, сеть лишь обновляет его.
 * Задания сервер уже отфильтровал по возможностям устройства (§03.7),
 * клиент им верит и не фильтрует повторно.
 */

/**
 * Задание в том виде, в котором его показывает список. Разбирается из JSON
 * сервера терпимо: неизвестные поля игнорируются (аддитивность API, §09.7),
 * исходный объект сохраняется в [raw] — деталям экрана может понадобиться
 * больше, чем нужно списку.
 */
data class LocalTask(
    val id: String,
    val protocolCode: String,
    val protocolVersion: Int?,
    val state: String,
    val priority: Int,
    val dueAt: String?,
    val title: String?,
    val raw: JsonObject,
) {
    companion object {
        fun fromJson(data: JsonObject): LocalTask? {
            val id = data["id"]?.jsonPrimitive?.content ?: return null
            val subject = data["subject"] as? JsonObject
            return LocalTask(
                id = id,
                protocolCode = data["protocol_code"]?.jsonPrimitive?.content ?: return null,
                protocolVersion = data["protocol_version"]?.jsonPrimitive?.intOrNull,
                state = data["state"]?.jsonPrimitive?.content ?: "NEW",
                priority = data["priority"]?.jsonPrimitive?.intOrNull ?: 100,
                dueAt = data["due_at"]?.jsonPrimitive?.content,
                title = subject?.get("title")?.jsonPrimitive?.content
                    ?: subject?.get("name")?.jsonPrimitive?.content,
                raw = data,
            )
        }
    }
}

interface TaskStore {
    suspend fun upsertTask(task: LocalTask)

    /** Для списка: срочные сверху — приоритет, затем дедлайн (ISO сортируется строкой). */
    suspend fun tasks(): List<LocalTask>

    suspend fun upsertProtocol(code: String, version: Int, document: JsonObject)
    suspend fun protocol(code: String, version: Int): JsonObject?

    /** Свежайшая синхронизированная версия кода — для заданий без пина версии. */
    suspend fun latestProtocol(code: String): JsonObject?

    /** Судьба сессии от контролёра (scope verdicts): «принято/брак/пересъёмка». */
    suspend fun upsertVerdict(verdict: SessionVerdict)
    suspend fun verdict(sessionId: String): SessionVerdict?

    /** Свежайший вердикт по заданию — для бейджа и комментария в деталях. */
    suspend fun verdictForTask(taskId: String): SessionVerdict?
}

data class SessionVerdict(
    val sessionId: String,
    val taskId: String?,
    val verdict: String,
    val comment: String?,
    val resolvedAt: String?,
)

class InMemoryTaskStore : TaskStore {
    private val mutex = Mutex()
    private val byId = linkedMapOf<String, LocalTask>()
    private val protocols = mutableMapOf<Pair<String, Int>, JsonObject>()

    override suspend fun upsertTask(task: LocalTask): Unit = mutex.withLock {
        byId[task.id] = task
    }

    override suspend fun tasks(): List<LocalTask> = mutex.withLock {
        byId.values.sortedWith(
            compareBy({ it.priority }, { it.dueAt ?: "9999" }, { it.id })
        )
    }

    override suspend fun upsertProtocol(code: String, version: Int, document: JsonObject) {
        mutex.withLock { protocols[code to version] = document }
    }

    override suspend fun protocol(code: String, version: Int): JsonObject? =
        mutex.withLock { protocols[code to version] }

    override suspend fun latestProtocol(code: String): JsonObject? = mutex.withLock {
        protocols.filterKeys { it.first == code }.maxByOrNull { it.key.second }?.value
    }

    private val verdicts = mutableMapOf<String, SessionVerdict>()

    override suspend fun upsertVerdict(verdict: SessionVerdict): Unit = mutex.withLock {
        verdicts[verdict.sessionId] = verdict
    }

    override suspend fun verdict(sessionId: String): SessionVerdict? =
        mutex.withLock { verdicts[sessionId] }

    override suspend fun verdictForTask(taskId: String): SessionVerdict? = mutex.withLock {
        verdicts.values.filter { it.taskId == taskId }
            .maxByOrNull { it.resolvedAt ?: "" }
    }
}

/**
 * Применение ответа pull к локальному состоянию.
 *
 * Неизвестный тип изменения пропускается со счётчиком, а не ошибкой:
 * клиент обязан игнорировать неизвестное (аддитивность внутри версии API),
 * иначе каждое расширение сервера ломало бы старые приложения.
 */
class PullApplier(private val tasks: TaskStore) {

    data class Summary(
        val tasksApplied: Int = 0,
        val protocolsApplied: Int = 0,
        val verdictsApplied: Int = 0,
        val skipped: Int = 0,
    )

    suspend fun apply(result: PullResult): Summary {
        var summary = Summary()
        for (change in result.changes) {
            when (change.type) {
                "task.upsert" -> {
                    val task = LocalTask.fromJson(change.data)
                    if (task != null) {
                        tasks.upsertTask(task)
                        summary = summary.copy(tasksApplied = summary.tasksApplied + 1)
                    } else {
                        summary = summary.copy(skipped = summary.skipped + 1)
                    }
                }
                "protocol.upsert" -> {
                    val code = change.data["code"]?.jsonPrimitive?.content
                    val version = change.data["version"]?.jsonPrimitive?.intOrNull
                    if (code != null && version != null) {
                        tasks.upsertProtocol(code, version, change.data.jsonObject)
                        summary = summary.copy(protocolsApplied = summary.protocolsApplied + 1)
                    } else {
                        summary = summary.copy(skipped = summary.skipped + 1)
                    }
                }
                "verdict.upsert" -> {
                    val sessionId = change.data["session_id"]?.jsonPrimitive?.content
                    val verdict = change.data["verdict"]?.jsonPrimitive?.content
                    if (sessionId != null && verdict != null) {
                        tasks.upsertVerdict(SessionVerdict(
                            sessionId = sessionId,
                            taskId = change.data["task_id"]?.jsonPrimitive?.content,
                            verdict = verdict,
                            comment = change.data["verdict_comment"]?.jsonPrimitive?.content,
                            resolvedAt = change.data["resolved_at"]?.jsonPrimitive?.content,
                        ))
                        summary = summary.copy(verdictsApplied = summary.verdictsApplied + 1)
                    } else {
                        summary = summary.copy(skipped = summary.skipped + 1)
                    }
                }
                else -> summary = summary.copy(skipped = summary.skipped + 1)
            }
        }
        return summary
    }
}

/**
 * Синхронизация одного справочника: запрос с If-None-Match из кэша,
 * применение через [DirectoryStore] (защита от отката версии — там).
 */
class DirectorySync(
    private val transport: SyncTransport,
    private val store: DirectoryStore,
    private val name: String = "standard_series",
) {
    /** true — справочник обновился; false — актуален или ответ отвергнут. */
    suspend fun syncOnce(): Boolean {
        val response = transport.fetchDirectory(name, store.etag)
        if (response.notModified) {
            store.applyNotModified()
            return false
        }
        val body = response.body ?: return false
        return store.apply(response.etag, body)
    }
}
