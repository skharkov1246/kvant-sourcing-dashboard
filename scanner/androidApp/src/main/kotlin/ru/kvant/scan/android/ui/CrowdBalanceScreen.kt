package ru.kvant.scan.android.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.weight
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import ru.kvant.scan.crowd.CampaignCard
import ru.kvant.scan.crowd.ContributorLedger

/**
 * Баланс и кампания участника краудсорсинга (двойник — iOS
 * CrowdBalanceView.swift). Экран крауд-приложения; в операторское
 * приложение не монтируется — хост появится вместе с онбордингом
 * (kv_crowd_onboarding).
 *
 * Правило гашения: кнопку съёмки по кампании показывает ХОСТ, но решение
 * принимает ядро (CampaignCard.allowsCapture) — здесь только честное
 * предупреждение, что кампания на исходе, ДО потраченного времени.
 */
@Composable
fun CrowdBalanceScreen(
    ledger: ContributorLedger,
    campaign: CampaignCard?,
    scanCostCents: Int,
) {
    Column(
        Modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
    ) {
        Text("Баланс", style = MaterialTheme.typography.titleSmall)
        Text(
            eur(ledger.balanceCents),
            style = MaterialTheme.typography.headlineMedium,
        )

        campaign?.let { card ->
            Card(Modifier.fillMaxWidth().padding(vertical = 12.dp)) {
                Column(Modifier.padding(12.dp)) {
                    Text(card.title, style = MaterialTheme.typography.titleSmall)
                    Text(
                        "Остаток кампании: ${eur(card.remainingCents)}",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    when {
                        !card.allowsCapture(scanCostCents) -> Text(
                            "Кампания завершена или бюджет исчерпан — сканы по ней не оплачиваются",
                            color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodySmall,
                        )
                        card.remainingCents < scanCostCents * 3 -> Text(
                            "Кампания на исходе: остатка хватит меньше чем на 3 скана",
                            color = MaterialTheme.colorScheme.tertiary,
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                }
            }
        }

        Text(
            "История",
            style = MaterialTheme.typography.titleSmall,
            modifier = Modifier.padding(top = 8.dp),
        )
        ledger.lines.forEach { line ->
            Row(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                Text(
                    kindTitle(line.kind) + (line.note?.let { " · $it" } ?: ""),
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    (if (line.amountCents > 0) "+" else "") + eur(line.amountCents),
                    style = MaterialTheme.typography.bodySmall,
                    color = if (line.amountCents >= 0) MaterialTheme.colorScheme.primary
                    else MaterialTheme.colorScheme.error,
                )
            }
        }
    }
}

private fun eur(cents: Int): String {
    val sign = if (cents < 0) "-" else ""
    val abs = kotlin.math.abs(cents)
    return "$sign${abs / 100},${(abs % 100).toString().padStart(2, '0')} €"
}

private fun kindTitle(kind: String): String = when (kind) {
    "ACCRUAL" -> "Принятый скан"
    "BONUS" -> "Бонус за субпоставщика"
    "CLAWBACK" -> "Сторно (аудит)"
    "PAYOUT" -> "Выплата"
    else -> kind
}
