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
import ru.kvant.scan.capture.CaptureSessionFactory
import ru.kvant.scan.sync.LocalTask

/**
 * Точка входа: список → детали → съёмка. Навигация — простое состояние
 * (три экрана не оправдывают навигационный фреймворк); «назад» из съёмки
 * намеренно не закрывает сессию — события уже в outbox, брошенную сессию
 * закрывает abandon с причиной, а не жест.
 */
class MainActivity : ComponentActivity() {

    private val container by lazy { AppContainer() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
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
}

@Composable
private fun KvantApp(container: AppContainer) {
    var screen by remember { mutableStateOf<Screen>(Screen.TaskList) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val taskListModel = remember {
        TaskListViewModel(container.stack.tasks, container.stack.engine)
    }

    when (val current = screen) {
        is Screen.TaskList -> TaskListScreen(
            model = taskListModel,
            onTaskClick = { screen = Screen.TaskDetail(it) },
        )
        is Screen.TaskDetail -> TaskDetailScreen(
            task = current.task,
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
        )
        is Screen.Capture -> CaptureScreen(viewModel = current.viewModel)
    }
    // error показывается тостом/снэкбаром хост-темы; для дев-сборки достаточно
    // лога — до APK этот экран не рендерится.
    error?.let { android.util.Log.w("KvantScan", it) }
}
