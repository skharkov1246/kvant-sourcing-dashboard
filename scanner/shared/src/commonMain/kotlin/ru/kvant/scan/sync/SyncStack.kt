package ru.kvant.scan.sync

import io.ktor.client.HttpClient
import kotlinx.datetime.Clock
import ru.kvant.scan.directory.DirectoryStore
import ru.kvant.scan.domain.Instant
import kotlin.random.Random

/**
 * Сборка синхронизационного графа приложения — одна на обе платформы.
 *
 * Хосты (AppContainer на Android, KvantScanApp на iOS) не собирают
 * зависимости сами: расхождение конфигурации платформ — это расхождение
 * поведения. HttpClient() без аргументов выбирает платформенный движок
 * из classpath (OkHttp / Darwin) — общий код движка не называет.
 *
 * Хранилища пока in-memory: SQLDelight-реализации встанут на эти же
 * интерфейсы при сборке APK, граф не изменится.
 */
class SyncStack(val config: SyncConfig) {

    val httpClient: HttpClient = HttpClient()

    val transport: SyncTransport = KtorSyncTransport(httpClient, config)

    val tasks: TaskStore = InMemoryTaskStore()
    val outbox: OutboxStore = InMemoryOutboxStore()
    val mediaQueue: MediaQueue = InMemoryMediaQueue()
    val directories: DirectoryStore = DirectoryStore()

    val engine: SyncEngine = SyncEngine(
        outbox = outbox,
        transport = transport,
        clock = { Instant(Clock.System.now().toEpochMilliseconds()) },
        random = { Random.nextDouble() },
    )

    val uploader: AssetUploader = AssetUploader(httpClient, config)

    val directorySync: DirectorySync = DirectorySync(transport, directories)

    fun now(): Instant = Instant(Clock.System.now().toEpochMilliseconds())
}
