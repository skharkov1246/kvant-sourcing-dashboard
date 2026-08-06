package ru.kvant.scan.android.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import ru.kvant.scan.sync.LocalTask

/**
 * Экран списка заданий (двойник — iOS TaskListView.swift).
 *
 * Тонкий по построению: сортировку и содержимое даёт ядро (TaskStore),
 * фильтрацию по возможностям устройства уже сделал сервер при выдаче.
 * Тап по карточке ведёт в съёмку — протокол задания открывает CaptureView.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TaskListScreen(
    model: TaskListViewModel,
    onTaskClick: (LocalTask) -> Unit,
    onInitiativeScan: (() -> Unit)? = null,
) {
    val state by model.state.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Задания") },
                actions = {
                    if (state.isRefreshing) {
                        CircularProgressIndicator(Modifier.padding(end = 16.dp))
                    } else {
                        IconButton(onClick = model::refresh) {
                            Icon(Icons.Filled.Refresh, contentDescription = "Обновить")
                        }
                    }
                },
            )
        },
        floatingActionButton = {
            // Инициативный скан: база наполняется и без заданий сверху —
            // оказался у стеллажа, отснял, контролёр примет как обычно.
            onInitiativeScan?.let {
                ExtendedFloatingActionButton(
                    onClick = it,
                    icon = { Icon(Icons.Filled.Add, contentDescription = null) },
                    text = { Text("Сканировать") },
                )
            }
        },
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            state.syncError?.let {
                // Сеть упала — список прежний, это норма склада (§04.1).
                Text(
                    "Не обновилось: работаем по сохранённому списку",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                )
            }
            if (state.pendingSync > 0) {
                // Офлайн-прозрачность: видно, что на устройстве есть незажитое —
                // выключать его сейчас нельзя (I-1). Чистое устройство молчит.
                Text(
                    "Не синхронизировано: ${state.pendingSync}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.tertiary,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                )
            }
            if (state.tasks.isEmpty() && !state.isRefreshing) {
                Text(
                    "Заданий нет. Потяните обновление или отсканируйте деталь без задания.",
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.padding(24.dp),
                )
            }
            LazyColumn(
                verticalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.fillMaxSize().padding(horizontal = 12.dp),
            ) {
                items(state.tasks, key = { it.id }) { task ->
                    TaskCard(task, onClick = { onTaskClick(task) })
                }
            }
        }
    }
}

@Composable
private fun TaskCard(task: LocalTask, onClick: () -> Unit) {
    Card(Modifier.fillMaxWidth().clickable(onClick = onClick)) {
        Column(Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    task.title ?: "Позиция без названия",
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.weight(1f),
                )
                // Судьба задания приезжает pull-каналом как state — бейджу
                // не нужен отдельный запрос вердикта.
                when (task.state) {
                    "REWORK" -> AssistChip(onClick = onClick, label = { Text("Пересъёмка") })
                    "REJECTED" -> AssistChip(onClick = onClick, label = { Text("Брак") })
                    "ACCEPTED", "EXPORTED" ->
                        AssistChip(onClick = onClick, label = { Text("Принято") })
                }
            }
            Spacer(Modifier.padding(2.dp))
            Row {
                Text(
                    task.protocolCode +
                        (task.protocolVersion?.let { "@$it" } ?: ""),
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.weight(1f),
                )
                task.dueAt?.let {
                    Text("до ${it.take(10)}", style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }
}
