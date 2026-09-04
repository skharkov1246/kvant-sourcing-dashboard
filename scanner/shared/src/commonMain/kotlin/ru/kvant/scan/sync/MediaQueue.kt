package ru.kvant.scan.sync

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Очередь загрузки медиа (§04.2, инвариант I-1: сырьё не теряется).
 *
 * AssetWriter платформы кладёт файл на диск и ставит запись СЮДА — прямой
 * загрузки из съёмки нет: съёмка не ждёт сеть. Очередь двигает фоновый
 * воркер ([MediaWorker]); файл на диске принадлежит очереди до состояния
 * DONE — удалять его раньше запрещено контрактом [deletablePaths]:
 * это единственный источник путей, безопасных к удалению.
 */
data class PendingMedia(
    val localPath: String,
    val sessionId: String,
    val stepId: String,
    val kind: String,
    val mime: String,
    val state: State = State.PENDING,
    val attempts: Int = 0,
    val lastError: String? = null,
    val assetId: String? = null,
) {
    enum class State { PENDING, DONE }
}

interface MediaQueue {
    /** Идемпотентно по localPath: один файл — одна запись. */
    suspend fun enqueue(media: PendingMedia)

    suspend fun pending(): List<PendingMedia>

    suspend fun markDone(localPath: String, assetId: String)

    suspend fun markFailed(localPath: String, error: String)

    /**
     * Пути, файлы которых можно удалить с диска: ТОЛЬКО DONE.
     * Возвращённые записи считаются изъятыми из очереди.
     */
    suspend fun deletablePaths(): List<String>
}

class InMemoryMediaQueue : MediaQueue {
    private val mutex = Mutex()
    private val byPath = linkedMapOf<String, PendingMedia>()

    override suspend fun enqueue(media: PendingMedia): Unit = mutex.withLock {
        if (media.localPath !in byPath) byPath[media.localPath] = media
    }

    override suspend fun pending(): List<PendingMedia> = mutex.withLock {
        byPath.values.filter { it.state == PendingMedia.State.PENDING }
    }

    override suspend fun markDone(localPath: String, assetId: String): Unit = mutex.withLock {
        byPath[localPath]?.let {
            byPath[localPath] = it.copy(state = PendingMedia.State.DONE, assetId = assetId)
        }
    }

    override suspend fun markFailed(localPath: String, error: String): Unit = mutex.withLock {
        byPath[localPath]?.let {
            byPath[localPath] = it.copy(attempts = it.attempts + 1, lastError = error)
        }
    }

    override suspend fun deletablePaths(): List<String> = mutex.withLock {
        val done = byPath.values.filter { it.state == PendingMedia.State.DONE }
        done.forEach { byPath.remove(it.localPath) }
        done.map { it.localPath }
    }
}

/**
 * Фоновый воркер медиа-канала: читает файл, грузит через [AssetUploader],
 * отмечает DONE. Ошибка загрузки оставляет запись PENDING с причиной —
 * файл живёт на диске до подтверждения сервера, сколько бы попыток
 * ни потребовалось.
 */
class MediaWorker(
    private val queue: MediaQueue,
    private val uploader: AssetUploader,
    /** Чтение файла — платформенная забота (java.io / NSData). */
    private val readFile: suspend (String) -> ByteArray?,
) {
    data class Report(val uploaded: Int = 0, val failed: Int = 0)

    suspend fun runOnce(): Report {
        var report = Report()
        for (entry in queue.pending()) {
            val bytes = readFile(entry.localPath)
            if (bytes == null) {
                queue.markFailed(entry.localPath, "файл не читается: ${entry.localPath}")
                report = report.copy(failed = report.failed + 1)
                continue
            }
            try {
                val assetId = uploader.upload(
                    AssetUploader.Meta(entry.sessionId, entry.stepId, entry.kind, entry.mime),
                    bytes,
                )
                queue.markDone(entry.localPath, assetId)
                report = report.copy(uploaded = report.uploaded + 1)
            } catch (e: TransportException) {
                queue.markFailed(entry.localPath, e.message ?: "сеть")
                report = report.copy(failed = report.failed + 1)
            } catch (e: AssetUploader.IntegrityException) {
                // Сервер сбросил приём — следующий проход начнёт заново.
                queue.markFailed(entry.localPath, e.message ?: "checksum")
                report = report.copy(failed = report.failed + 1)
            }
        }
        return report
    }
}
