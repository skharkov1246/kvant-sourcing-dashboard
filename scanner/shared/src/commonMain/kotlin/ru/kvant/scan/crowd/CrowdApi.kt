package ru.kvant.scan.crowd

import io.ktor.client.HttpClient
import io.ktor.client.request.get
import io.ktor.client.request.header
import io.ktor.client.statement.HttpResponse
import io.ktor.client.statement.bodyAsText
import io.ktor.http.HttpStatusCode
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import ru.kvant.scan.sync.SyncConfig
import ru.kvant.scan.sync.TransportException

/**
 * Карточка кампании для участника (docs/15 §3): по остатку бюджета
 * приложение гасит съёмку ДО того, как человек потратил время, — сервер
 * скан сверх бюджета всё равно не оплатит.
 */
data class CampaignCard(
    val id: String,
    val title: String,
    val state: String,
    val budgetCents: Int,
    val spentCents: Int,
    val remainingCents: Int,
) {
    /**
     * Пускать ли участника в съёмку по этой кампании. Цена скана — параметр:
     * тариф это данные (reward_policy), ядро его не хардкодит.
     */
    fun allowsCapture(scanCostCents: Int): Boolean =
        state == "ACTIVE" && remainingCents >= scanCostCents
}

data class LedgerLine(
    val kind: String,  // ACCRUAL | BONUS | CLAWBACK | PAYOUT
    val amountCents: Int,
    val note: String?,
    val submissionId: String?,
)

data class ContributorLedger(
    val contributorId: String,
    val balanceCents: Int,
    val lines: List<LedgerLine>,
)

/**
 * Клиент крауд-контура (/v1/crowd) — читающая сторона участника: баланс,
 * реестр, состояние кампании. Денежные операции (вердикты, клобэк, выплаты)
 * намеренно отсутствуют: они принадлежат аудитору на стороне сервера.
 */
class CrowdApi(
    private val client: HttpClient,
    private val config: SyncConfig,
) {
    private val json = Json { ignoreUnknownKeys = true }

    /** null — кампании нет (404): гасим съёмку, это не ошибка сети. */
    suspend fun fetchCampaign(campaignId: String): CampaignCard? {
        val response = execute("${config.baseUrl}/v1/crowd/campaigns/$campaignId")
            ?: return null
        val doc = json.parseToJsonElement(response.bodyAsText()).jsonObject
        return CampaignCard(
            id = doc["id"]?.jsonPrimitive?.content ?: campaignId,
            title = doc["title"]?.jsonPrimitive?.content ?: "",
            state = doc["state"]?.jsonPrimitive?.content ?: "CLOSED",
            budgetCents = doc["budget_cents_eur"]?.jsonPrimitive?.intOrNull ?: 0,
            spentCents = doc["spent_cents_eur"]?.jsonPrimitive?.intOrNull ?: 0,
            remainingCents = doc["remaining_cents_eur"]?.jsonPrimitive?.intOrNull ?: 0,
        )
    }

    /** null — участника нет (404). */
    suspend fun fetchLedger(contributorId: String): ContributorLedger? {
        val response = execute(
            "${config.baseUrl}/v1/crowd/contributors/$contributorId/ledger")
            ?: return null
        val doc = json.parseToJsonElement(response.bodyAsText()).jsonObject
        return ContributorLedger(
            contributorId = doc["contributor_id"]?.jsonPrimitive?.content ?: contributorId,
            balanceCents = doc["balance_cents_eur"]?.jsonPrimitive?.intOrNull ?: 0,
            lines = (doc["entries"]?.jsonArray ?: emptyList()).map {
                val entry = it.jsonObject
                LedgerLine(
                    kind = entry["kind"]?.jsonPrimitive?.content ?: "",
                    amountCents = entry["amount_cents_eur"]?.jsonPrimitive?.intOrNull ?: 0,
                    note = entry["note"]?.jsonPrimitive?.contentOrNull,
                    submissionId = entry["submission_id"]?.jsonPrimitive?.contentOrNull,
                )
            },
        )
    }

    private suspend fun execute(url: String): HttpResponse? {
        val response = try {
            client.get(url) {
                config.bearerToken?.let { header("Authorization", "Bearer $it") }
                header("X-Tenant-Id", config.tenantId)
                header("X-User-Id", config.userId)
                header("X-Roles", config.roles)
            }
        } catch (e: Exception) {
            throw TransportException(e.message ?: "сеть недоступна", e)
        }
        if (response.status == HttpStatusCode.NotFound) return null
        if (response.status.value !in 200..299) {
            throw TransportException("HTTP ${response.status.value} от сервера синхронизации")
        }
        return response
    }
}
