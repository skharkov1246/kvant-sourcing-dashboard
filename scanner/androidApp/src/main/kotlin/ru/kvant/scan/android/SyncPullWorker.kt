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
import ru.kvant.scan.sync.TransportException
import java.util.concurrent.TimeUnit

/**
 * Периодический pull (§09.3: гарантию даёт pull, push — лишь сигнал):
 * каждые 15 минут задания, протоколы и справочники подтягиваются в
 * локальные хранилища, и список работает даже если приложение не открывали.
 * Push-уведомление (когда появится) будет лишь досрочно запускать этот
 * же воркер — данных в пуше нет по построению.
 */
class SyncPullWorker(
    context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result = try {
        AppGraph.container.stack.pullOnce()
        Result.success()
    } catch (_: TransportException) {
        // Сеть моргнула — прежние данные валидны, повтор по backoff.
        Result.retry()
    }

    companion object {
        private const val UNIQUE_NAME = "kvant-sync-pull"

        fun schedule(context: Context) {
            val request = PeriodicWorkRequestBuilder<SyncPullWorker>(15, TimeUnit.MINUTES)
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
