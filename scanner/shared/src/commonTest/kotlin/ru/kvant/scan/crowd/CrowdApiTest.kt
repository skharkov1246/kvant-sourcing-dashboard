package ru.kvant.scan.crowd

import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.MockRequestHandleScope
import io.ktor.client.engine.mock.respond
import io.ktor.client.request.HttpRequestData
import io.ktor.client.request.HttpResponseData
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import ru.kvant.scan.sync.SyncConfig
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * Читающий клиент крауд-контура: карточка кампании гасит съёмку ДО съёмки,
 * реестр показывает участнику его деньги, 404 — честный null, не ошибка.
 */
class CrowdApiTest {

    private val config = SyncConfig(
        baseUrl = "https://api.test",
        tenantId = "t-internal", userId = "u-crowd", deviceId = "d-1",
        capabilities = Json.parseToJsonElement("""{"schema_version":1}""").jsonObject,
    )

    private fun api(
        handler: suspend MockRequestHandleScope.(HttpRequestData) -> HttpResponseData,
    ) = CrowdApi(HttpClient(MockEngine { request -> handler(request) }), config)

    @Test
    fun `карточка кампании разбирается вместе с остатком`() = runTest {
        var captured: HttpRequestData? = null
        val api = api { request ->
            captured = request
            respond(
                """{"id": "camp-1", "title": "Уплотнения август", "state": "ACTIVE",
                    "budget_cents_eur": 10000, "spent_cents_eur": 9600,
                    "remaining_cents_eur": 400}""",
                headers = headersOf(HttpHeaders.ContentType, "application/json"),
            )
        }
        val card = api.fetchCampaign("camp-1")!!

        assertEquals("https://api.test/v1/crowd/campaigns/camp-1",
            captured!!.url.toString())
        assertEquals("t-internal", captured!!.headers["X-Tenant-Id"])
        assertEquals(400, card.remainingCents)
        // Остатка не хватает на скан по базовому тарифу — съёмка гаснет ДО
        // того, как участник потратил время; цена — параметр, не хардкод ядра.
        assertFalse(card.allowsCapture(scanCostCents = 500))
        assertTrue(card.allowsCapture(scanCostCents = 400))
    }

    @Test
    fun `неактивная кампания не пускает в съёмку даже с бюджетом`() = runTest {
        val api = api {
            respond(
                """{"id": "camp-2", "title": "Пауза", "state": "PAUSED",
                    "budget_cents_eur": 10000, "spent_cents_eur": 0,
                    "remaining_cents_eur": 10000}""",
                headers = headersOf(HttpHeaders.ContentType, "application/json"),
            )
        }
        assertFalse(api.fetchCampaign("camp-2")!!.allowsCapture(500))
    }

    @Test
    fun `404 по кампании - null а не исключение`() = runTest {
        val api = api { respond("нет", HttpStatusCode.NotFound) }
        assertNull(api.fetchCampaign("ghost"))
    }

    @Test
    fun `реестр участника - баланс и строки с видами операций`() = runTest {
        val api = api {
            respond(
                """{"contributor_id": "c-1", "balance_cents_eur": 300,
                    "entries": [
                      {"id": "l-1", "kind": "ACCRUAL", "amount_cents_eur": 500,
                       "submission_id": "s-1", "note": null},
                      {"id": "l-2", "kind": "CLAWBACK", "amount_cents_eur": -200,
                       "submission_id": "s-1", "note": "фото с экрана"}
                    ]}""",
                headers = headersOf(HttpHeaders.ContentType, "application/json"),
            )
        }
        val ledger = api.fetchLedger("c-1")!!

        assertEquals(300, ledger.balanceCents)
        assertEquals(listOf("ACCRUAL", "CLAWBACK"), ledger.lines.map { it.kind })
        assertNull(ledger.lines[0].note)  // JsonNull не превращается в "null"
        assertEquals("фото с экрана", ledger.lines[1].note)
        // Баланс — сумма строк: история не переписывается (C-2).
        assertEquals(ledger.balanceCents, ledger.lines.sumOf { it.amountCents })
    }
}
