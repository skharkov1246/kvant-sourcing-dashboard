package ru.kvant.scan.android

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import kotlinx.coroutines.launch
import ru.kvant.scan.android.ui.CaptureScreen
import ru.kvant.scan.android.ui.CaptureViewModel
import ru.kvant.scan.android.ui.TaskDetailScreen
import ru.kvant.scan.android.ui.TaskListScreen
import ru.kvant.scan.android.ui.TaskListViewModel
import ru.kvant.scan.android.ui.ItemCardScreen
import ru.kvant.scan.android.ui.TraceLinkDialog
import ru.kvant.scan.capture.CaptureSessionFactory
import ru.kvant.scan.sync.LocalTask

/**
 * Точка входа: список → детали → съёмка. Навигация — простое состояние
 * (три экрана не оправдывают навигационный фреймворк); «назад» из съёмки
 * намеренно не закрывает сессию — события уже в outbox, брошенную сессию
 * закрывает abandon с причиной, а не жест.
 */
class MainActivity : ComponentActivity() {

    private val container get() = AppGraph.container

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        MediaUploadWorker.schedule(applicationContext)
        SyncPullWorker.schedule(applicationContext)
        setContent {
            MaterialTheme {
                Surface { KvantApp(container) }
            }
        }
    }
}

private sealed interface Screen {
    data object TaskList : Screen
    data class TaskDetail(val task: LocalTask) : Screen
    data class Capture(val viewModel: CaptureViewModel) : Screen
    data class ItemCard(
        val card: ru.kvant.scan.sync.ItemCard,
        val from: TaskDetail,
    ) : Screen
}

@Composable
private fun KvantApp(container: AppContainer) {
    var screen by remember { mutableStateOf<Screen>(Screen.TaskList) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val taskListModel = remember {
        TaskListViewModel(container.stack.tasks, container.stack.engine, container.stack)
    }

    when (val current = screen) {
        is Screen.TaskList -> TaskListScreen(
            model = taskListModel,
            onTaskClick = { screen = Screen.TaskDetail(it) },
        )
        is Screen.TaskDetail -> {
            // Комментарий контролёра — из локального TaskStore (приехал pull'ом).
            val verdict by androidx.compose.runtime.produceState<ru.kvant.scan.sync.SessionVerdict?>(
                initialValue = null, key1 = current.task.id,
            ) { value = container.stack.tasks.verdictForTask(current.task.id) }
            var traceItemId by remember { mutableStateOf<String?>(null) }
            TaskDetailScreen(
                task = current.task,
                verdict = verdict,
                onStartCapture = { task ->
                    scope.launch {
                        try {
                            val controller = container.captureController(task)
                            screen = Screen.Capture(CaptureViewModel(controller))
                        } catch (e: CaptureSessionFactory.ProtocolMissing) {
                            // Честная ошибка ДО съёмки: протокол не синхронизирован.
                            error = e.message
                        }
                    }
                },
                // Карточка принятой съёмки детерминирована: itm-<session>.
                onAddTrace = { verdict?.let { traceItemId = "itm-${it.sessionId}" } },
                onOpenItemCard = {
                    verdict?.let { v ->
                        scope.launch {
                            try {
                                val card = container.stack.fetchItemCard("itm-${v.sessionId}")
                                if (card != null) {
                                    screen = Screen.ItemCard(card, current)
                                } else {
                                    // Честное «карточки нет» (сессия без кодов) — не ошибка.
                                    error = "Карточка ещё не создана: в сессии не было кодов"
                                }
                            } catch (e: Exception) {
                                error = "Карточка недоступна: ${e.message}"
                            }
                        }
                    }
                },
            )
            traceItemId?.let { itemId ->
                val form = remember(itemId) { container.stack.traceLinkForm() }
                TraceLinkDialog(
                    form = form,
                    itemId = itemId,
                    onDismiss = { traceItemId = null },
                    onSent = { ok ->
                        traceItemId = null
                        // false — не ошибка: заявка в очереди и уедет со следующим
                        // проходом синка (офлайн-надёжность, I-1).
                        if (!ok) error = "Сети нет — заявка в очереди, уйдёт автоматически"
                    },
                )
            }
        }
        is Screen.Capture -> CaptureScreen(viewModel = current.viewModel)
        is Screen.ItemCard -> {
            androidx.activity.compose.BackHandler { screen = current.from }
            ItemCardScreen(card = current.card)
        }
    }
    // error показывается тостом/снэкбаром хост-темы; для дев-сборки достаточно
    // лога — до APK этот экран не рендерится.
    error?.let { android.util.Log.w("KvantScan", it) }
}
