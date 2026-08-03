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
import kotlinx.serialization.json.JsonPrimitive
import ru.kvant.scan.sync.ItemCard

/**
 * Карточка единицы товара (двойник — iOS ItemCardView.swift).
 *
 * Identity и трассировка — с сервера как есть: значок «✓ проверено» ставится
 * только по confidence=verified (машинное чтение, подтверждённое контролёром),
 * заявленное внешним сотрудником остаётся «заявлено» (I-7).
 */
@Composable
fun ItemCardScreen(card: ItemCard) {
    Column(
        Modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
    ) {
        Text("Карточка товара", style = MaterialTheme.typography.headlineSmall)
        Text(card.id, style = MaterialTheme.typography.bodySmall)
        card.qualityScore?.let {
            Text("Качество съёмки: $it", style = MaterialTheme.typography.bodyMedium,
                 modifier = Modifier.padding(top = 4.dp))
        }

        if (card.identity.isNotEmpty()) {
            Card(Modifier.fillMaxWidth().padding(vertical = 12.dp)) {
                Column(Modifier.padding(12.dp)) {
                    Text("Идентичность", style = MaterialTheme.typography.titleSmall)
                    card.identity.forEach { (field, value) ->
                        Row(Modifier.padding(vertical = 2.dp)) {
                            Text(field, style = MaterialTheme.typography.bodySmall,
                                 modifier = Modifier.weight(1f))
                            Text(value, style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
        }

        Card(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
            Column(Modifier.padding(12.dp)) {
                Text("Трассировка", style = MaterialTheme.typography.titleSmall)
                card.trace.forEach { link ->
                    Row(Modifier.padding(vertical = 2.dp)) {
                        Text(
                            "${link.codeType}: ${link.codeValue}" +
                                (link.supplierName?.let { " · $it" } ?: ""),
                            style = MaterialTheme.typography.bodySmall,
                            modifier = Modifier.weight(1f),
                        )
                        Text(
                            if (link.confidence == "verified") "✓ проверено" else "заявлено",
                            style = MaterialTheme.typography.bodySmall,
                            color = if (link.confidence == "verified")
                                MaterialTheme.colorScheme.primary
                            else MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }

        if (card.measurements.isNotEmpty()) {
            Card(Modifier.fillMaxWidth().padding(vertical = 12.dp)) {
                Column(Modifier.padding(12.dp)) {
                    Text("Замеры принятой сессии", style = MaterialTheme.typography.titleSmall)
                    card.measurements.forEach { measurement ->
                        measurement.forEach { (key, value) ->
                            Row(Modifier.padding(vertical = 2.dp)) {
                                Text(key, style = MaterialTheme.typography.bodySmall,
                                     modifier = Modifier.weight(1f))
                                Text(
                                    (value as? JsonPrimitive)?.content ?: value.toString(),
                                    style = MaterialTheme.typography.bodySmall,
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}
