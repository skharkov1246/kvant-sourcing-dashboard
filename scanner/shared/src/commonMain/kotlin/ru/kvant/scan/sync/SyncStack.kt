package ru.kvant.scan.sync

import io.ktor.client.HttpClient
import kotlinx.datetime.Clock
import ru.kvant.scan.directory.DirectoryStore
import ru.kvant.scan.directory.SupplierDirectoryStore
import ru.kvant.scan.domain.Instant
import ru.kvant.scan.trace.InMemoryTraceLinkQueue
import ru.kvant.scan.trace.TraceLinkForm
import ru.kvant.scan.trace.TraceLinkQueue
import ru.kvant.scan.trace.TraceLinkWorker
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

    private val ktorTransport = KtorSyncTransport(httpClient, config)
    val transport: SyncTransport = ktorTransport

    val tasks: TaskStore = InMemoryTaskStore()
    val outbox: OutboxStore = InMemoryOutboxStore()
    val mediaQueue: MediaQueue = InMemoryMediaQueue()
    val directories: DirectoryStore = DirectoryStore()
    val suppliers: SupplierDirectoryStore = SupplierDirectoryStore()
    val traceLinks: TraceLinkQueue = InMemoryTraceLinkQueue()

    /** Доставка заявок трассировки; сетевые отказы оставляют заявку в очереди. */
    val traceLinkWorker: TraceLinkWorker = TraceLinkWorker(traceLinks) { draft ->
        ktorTransport.addTraceLink(
            itemId = draft.itemId,
            codeType = draft.codeType,
            codeValue = draft.codeValue,
            supplierName = draft.supplierName,
            supplierInn = draft.supplierInn,
            supplierRef = draft.supplierRef,
        )
    }

    val engine: SyncEngine = SyncEngine(
        outbox = outbox,
        transport = transport,
        clock = { Instant(Clock.System.now().toEpochMilliseconds()) },
        random = { Random.nextDouble() },
    )

    val uploader: AssetUploader = AssetUploader(httpClient, config)

    val directorySync: DirectorySync = DirectorySync(transport, directories)

    /** Курсор pull; в памяти графа до SQLDelight (directory_cache-стиль). */
    var pullCursor: String? = null

    /** Один проход pull-канала целиком: задания+протоколы, затем справочники. */
    suspend fun pullOnce() {
        pullCursor = engine.pullOnce(pullCursor, PullApplier(tasks))
        directorySync.syncOnce()
        syncSuppliers()
        // Появилась сеть — уезжают и отложенные заявки трассировки.
        traceLinkWorker.runOnce()
    }

    /**
     * Справочник suppliers публикуется динамически (снапшот из CRM):
     * до первой публикации сервер честно отвечает 404, при обрыве сети
     * подсказки просто остаются вчерашними — и то и другое не роняет
     * основной pull.
     */
    private suspend fun syncSuppliers() {
        try {
            val response = ktorTransport.fetchDirectory("suppliers", suppliers.etag)
            if (!response.notModified) {
                response.body?.let { suppliers.apply(response.etag, it) }
            }
        } catch (_: TransportException) {
        }
    }

    /** Карточка товара с сервера; null — карточки (ещё) нет, это не ошибка. */
    suspend fun fetchItemCard(itemId: String): ItemCard? = ktorTransport.fetchItem(itemId)

    /**
     * Форма заявленной трассировки поверх снапшота поставщиков (docs/09 §9.5).
     *
     * submit идёт ЧЕРЕЗ очередь, не напрямую: без сети заявка не теряется,
     * а ждёт следующего прохода синка. true — доставлено прямо сейчас;
     * false — заявка в очереди (для UI это «уйдёт при появлении сети»,
     * не ошибка).
     */
    fun traceLinkForm(): TraceLinkForm = TraceLinkForm(
        suppliers = { suppliers.current },
        send = { draft ->
            traceLinks.enqueue(draft)
            traceLinkWorker.runOnce()
            traceLinks.isDelivered(draft)
        },
    )

    /**
     * Сводка «что ещё не уехало» по всем трём офлайн-каналам — для
     * индикатора синхронизации в UI. Нулевая сводка = устройство чисто,
     * его можно выключать без потери данных (I-1).
     */
    suspend fun syncStatus(): SyncStatus = SyncStatus(
        outboxEvents = outbox.depth(),
        pendingMedia = mediaQueue.pending().size,
        pendingTraceLinks = traceLinks.pending().size,
    )

    fun now(): Instant = Instant(Clock.System.now().toEpochMilliseconds())
}

data class SyncStatus(
    val outboxEvents: Int,
    val pendingMedia: Int,
    val pendingTraceLinks: Int,
) {
    val total: Int get() = outboxEvents + pendingMedia + pendingTraceLinks
    val clean: Boolean get() = total == 0
}
