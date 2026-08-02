package ru.kvant.scan.sync

import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpMethod
import io.ktor.http.headersOf
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import ru.kvant.scan.util.Sha256
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/** Медиа-канал: дедуп, чанки, докачка с места обрыва, честный отказ по sha. */
class AssetUploaderTest {

    private val config = SyncConfig(
        baseUrl = "https://api.test", tenantId = "t-internal",
        userId = "u-1", deviceId = "d-1",
        capabilities = Json.parseToJsonElement("""{"schema_version":1}""").jsonObject,
    )

    private val meta = AssetUploader.Meta("s-1", "overview", "photo", "image/jpeg")

    private class Script {
        val requests = mutableListOf<Pair<HttpMethod, String>>()
        var received: List<Int> = emptyList()
        var deduplicated = false
        var completeStatus = "verified"
        var chunkSize = 4
    }

    private fun uploader(script: Script): AssetUploader {
        val engine = MockEngine { request ->
            script.requests += request.method to request.url.encodedPath
            val json = headersOf(HttpHeaders.ContentType, "application/json")
            when {
                request.url.encodedPath == "/v1/assets" ->
                    respond("""{"asset_id": "a-1", "chunk_size": ${script.chunkSize},
                               "deduplicated": ${script.deduplicated}}""", headers = json)
                request.url.encodedPath.endsWith("/status") ->
                    respond("""{"asset_id": "a-1", "upload_state": "uploading",
                               "received_chunks": ${script.received}}""", headers = json)
                request.url.encodedPath.endsWith("/complete") ->
                    respond("""{"status": "${script.completeStatus}"}""", headers = json)
                else -> respond("", io.ktor.http.HttpStatusCode.NoContent)
            }
        }
        return AssetUploader(HttpClient(engine), config)
    }

    @Test
    fun `sha256 совпадает с эталонными векторами NIST`() {
        assertEquals(
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            Sha256.hex(ByteArray(0)),
        )
        assertEquals(
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            Sha256.hex("abc".encodeToByteArray()),
        )
        // Многоблочный вход (>64 байт) — проверка дополнения
        assertEquals(
            "cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1",
            Sha256.hex("abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmnoijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu".encodeToByteArray()),
        )
    }

    @Test
    fun `полная загрузка - init, все чанки, complete`() = runTest {
        val script = Script()
        val id = uploader(script).upload(meta, "0123456789".encodeToByteArray())
        assertEquals("a-1", id)
        val puts = script.requests.filter { it.first == HttpMethod.Put }
        // 10 байт при чанке 4 → чанки 0,1,2
        assertEquals(
            listOf("/v1/assets/a-1/chunks/0", "/v1/assets/a-1/chunks/1", "/v1/assets/a-1/chunks/2"),
            puts.map { it.second },
        )
        assertTrue(script.requests.any { it.second.endsWith("/complete") })
    }

    @Test
    fun `дедуп по sha256 - байты не гоняются повторно`() = runTest {
        val script = Script().apply { deduplicated = true }
        uploader(script).upload(meta, "0123456789".encodeToByteArray())
        assertTrue(script.requests.none { it.first == HttpMethod.Put })
        assertTrue(script.requests.none { it.second.endsWith("/complete") })
    }

    @Test
    fun `докачка с места обрыва - уже принятые чанки пропускаются`() = runTest {
        val script = Script().apply { received = listOf(0, 1) }
        uploader(script).upload(meta, "0123456789".encodeToByteArray())
        val puts = script.requests.filter { it.first == HttpMethod.Put }
        assertEquals(listOf("/v1/assets/a-1/chunks/2"), puts.map { it.second })
    }

    @Test
    fun `checksum_mismatch - честная ошибка а не тихий приём`() = runTest {
        val script = Script().apply { completeStatus = "checksum_mismatch" }
        assertFailsWith<AssetUploader.IntegrityException> {
            uploader(script).upload(meta, "0123456789".encodeToByteArray())
        }
    }
}
