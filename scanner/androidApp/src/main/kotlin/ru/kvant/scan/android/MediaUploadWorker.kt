package ru.kvant.scan.android

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import ru.kvant.scan.sync.MediaWorker
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Фоновая доставка медиа (WorkManager-обвязка над MediaWorker ядра, §04.2).
 *
 * Вся логика очереди — в ядре и покрыта тестами; здесь платформенное:
 * условие «есть сеть», системный backoff и чтение/удаление файлов.
 * Файлы удаляются ТОЛЬКО по deletablePaths() — единственный легальный
 * источник путей, безопасных к удалению (I-1).
 */
class MediaUploadWorker(
    context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val stack = AppGraph.container.stack
        val worker = MediaWorker(stack.mediaQueue, stack.uploader) { path ->
            runCatching { File(path).readBytes() }.getOrNull()
        }
        val report = worker.runOnce()
        stack.mediaQueue.deletablePaths().forEach { File(it).delete() }
        // Частичный отказ — не провал прохода: подтверждённое подтверждено,
        // остальное доедет следующим запуском с системным backoff.
        return if (report.failed > 0) Result.retry() else Result.success()
    }

    companion object {
        private const val UNIQUE_NAME = "kvant-media-upload"

        fun schedule(context: Context) {
            val request = PeriodicWorkRequestBuilder<MediaUploadWorker>(15, TimeUnit.MINUTES)
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED)
                        .build(),
                )
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                UNIQUE_NAME, ExistingPeriodicWorkPolicy.KEEP, request,
            )
        }
    }
}
