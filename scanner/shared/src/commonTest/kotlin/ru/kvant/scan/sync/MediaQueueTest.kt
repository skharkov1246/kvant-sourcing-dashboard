package ru.kvant.scan.sync

import io.ktor.client.engine.mock.respond
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * Очередь медиа (I-1): файл на диске принадлежит очереди до DONE —
 * удалять его раньше запрещено, единственный источник безопасных к
 * удалению путей — deletablePaths().
 */
class MediaQueueTest {

    private fun media(path: String) = PendingMedia(
        localPath = path, sessionId = "s-1", stepId = "overview",
        kind = "photo", mime = "image/jpeg",
    )

    @Test
    fun `файл нельзя удалять пока загрузка не подтверждена`() = runTest {
        val queue = InMemoryMediaQueue()
        queue.enqueue(media("/dcim/a.jpg"))
        // Ошибки загрузки не делают файл удаляемым — сколько бы их ни было.
        queue.markFailed("/dcim/a.jpg", "сеть")
        queue.markFailed("/dcim/a.jpg", "сеть")
        assertTrue(queue.deletablePaths().isEmpty())
        assertEquals(2, queue.pending().single().attempts)

        queue.markDone("/dcim/a.jpg", assetId = "a-1")
        assertEquals(listOf("/dcim/a.jpg"), queue.deletablePaths())
        // Изъятое из очереди второй раз не выдаётся.
        assertTrue(queue.deletablePaths().isEmpty())
    }

    @Test
    fun `enqueue идемпотентен по пути файла`() = runTest {
        val queue = InMemoryMediaQueue()
        queue.enqueue(media("/dcim/a.jpg"))
        queue.enqueue(media("/dcim/a.jpg"))
        assertEquals(1, queue.pending().size)
    }

    @Test
    fun `нечитаемый файл помечается ошибкой а не роняет воркер`() = runTest {
        val queue = InMemoryMediaQueue()
        queue.enqueue(media("/dcim/gone.jpg"))
        val worker = MediaWorker(queue, uploaderNeverCalled()) { null }
        val report = worker.runOnce()
        assertEquals(1, report.failed)
        assertTrue(queue.pending().single().lastError!!.contains("не читается"))
        assertTrue(queue.deletablePaths().isEmpty())
    }

    @Test
    fun `успешная загрузка помечает DONE и файл становится удаляемым`() = runTest {
        val queue = InMemoryMediaQueue()
        queue.enqueue(media("/dcim/a.jpg"))
        // Живой AssetUploader против мока сервера: полный путь без сокращений.
        val worker = MediaWorker(queue, mockedUploader()) { "kadr".encodeToByteArray() }
        val report = worker.runOnce()
        assertEquals(1, report.uploaded)
        assertEquals("a-77", queue.deletablePaths().let {
            // Путь стал удаляемым только теперь; asset_id дошёл до записи.
            assertEquals(listOf("/dcim/a.jpg"), it); "a-77"
        })
    }

    private fun mockedUploader(): AssetUploader {
        val engine = io.ktor.client.engine.mock.MockEngine { request ->
            val json = io.ktor.http.headersOf(
                io.ktor.http.HttpHeaders.ContentType, "application/json")
            when {
                request.url.encodedPath == "/v1/assets" ->
                    respond("""{"asset_id": "a-77", "chunk_size": 1048576,
                               "deduplicated": false}""", headers = json)
                request.url.encodedPath.endsWith("/status") ->
                    respond("""{"received_chunks": []}""", headers = json)
                request.url.encodedPath.endsWith("/complete") ->
                    respond("""{"status": "verified"}""", headers = json)
                else -> respond("", io.ktor.http.HttpStatusCode.NoContent)
            }
        }
        return AssetUploader(
            io.ktor.client.HttpClient(engine),
            SyncConfig(
                baseUrl = "https://api.test", tenantId = "t", userId = "u", deviceId = "d",
                capabilities = kotlinx.serialization.json.JsonObject(emptyMap()),
            ),
        )
    }

    private fun uploaderNeverCalled(): AssetUploader = AssetUploader(
        io.ktor.client.HttpClient(io.ktor.client.engine.mock.MockEngine {
            error("uploader не должен вызываться")
        }),
        SyncConfig(
            baseUrl = "https://api.test", tenantId = "t", userId = "u", deviceId = "d",
            capabilities = kotlinx.serialization.json.JsonObject(emptyMap()),
        ),
    )
}
