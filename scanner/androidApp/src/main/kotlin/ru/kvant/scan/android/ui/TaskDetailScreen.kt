package ru.kvant.scan.android.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.weight
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import ru.kvant.scan.sync.LocalTask

/**
 * Детали задания (двойник — iOS TaskDetailView.swift).
 *
 * Subject показывается целиком из raw-объекта сервера: список знает только
 * нужное карточке, а деталям нужно всё, что прислала внешняя система, —
 * включая поля, о которых это приложение ещё не знает (аддитивность §09.7).
 */
@Composable
fun TaskDetailScreen(task: LocalTask, onStartCapture: (LocalTask) -> Unit) {
    Column(
        Modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
    ) {
        Text(task.title ?: "Позиция без названия", style = MaterialTheme.typography.headlineSmall)
        Text(
            task.protocolCode + (task.protocolVersion?.let { "@$it" } ?: ""),
            style = MaterialTheme.typography.bodyMedium,
        )
        task.dueAt?.let {
            Text("Срок: ${it.take(10)}", style = MaterialTheme.typography.bodyMedium)
        }
        if (task.state == "REWORK") {
            Text(
                "Возвращено контролёром на пересъёмку",
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(vertical = 4.dp),
            )
        }

        (task.raw["subject"] as? JsonObject)?.let { subject ->
            Card(Modifier.fillMaxWidth().padding(vertical = 12.dp)) {
                Column(Modifier.padding(12.dp)) {
                    subject.forEach { (key, value) ->
                        Row(Modifier.padding(vertical = 2.dp)) {
                            Text(
                                key,
                                style = MaterialTheme.typography.bodySmall,
                                modifier = Modifier.weight(1f),
                            )
                            Text(
                                (value as? JsonPrimitive)?.content ?: value.toString(),
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                    }
                }
            }
        }

        Button(onClick = { onStartCapture(task) }, modifier = Modifier.fillMaxWidth()) {
            Text(if (task.state == "REWORK") "Переснять" else "Начать съёмку")
        }
    }
}
