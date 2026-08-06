package ru.kvant.scan.android

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
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

    // Разрешение камеры спрашивается В МОМЕНТ входа в съёмку, а не на старте
    // приложения: человек видит, зачем оно нужно. Отказ — не тупик: список
    // и детали заданий работают без камеры, но молчать об отказе нельзя —
    // иначе кнопка «Начать съёмку» просто ничего не делает.
    private var afterGrant: (() -> Unit)? = null
    private var afterDenial: (() -> Unit)? = null
    private val requestCamera = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) afterGrant?.invoke() else afterDenial?.invoke()
        afterGrant = null
        afterDenial = null
    }

    private fun withCameraPermission(onDenied: () -> Unit, action: () -> Unit) {
        val granted = ContextCompat.checkSelfPermission(
            this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        if (granted) action() else {
            afterGrant = action
            afterDenial = onDenied
            requestCamera.launch(Manifest.permission.CAMERA)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        MediaUploadWorker.schedule(applicationContext)
        SyncPullWorker.schedule(applicationContext)
        setContent {
            MaterialTheme {
                Surface { KvantApp(container, ::withCameraPermission) }
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
private fun KvantApp(
    container: AppContainer,
    withCameraPermission: (onDenied: () -> Unit, action: () -> Unit) -> Unit,
) {
    var screen by remember { mutableStateOf<Screen>(Screen.TaskList) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    val taskListModel = remember {
        TaskListViewModel(container.stack.tasks, container.stack.engine, container.stack)
    }

    // Сообщение об ошибке видит ЧЕЛОВЕК, а не только logcat: тексты писались
    // для кладовщика в проходе между стеллажами, им место на экране.
    LaunchedEffect(error) {
        error?.let {
            snackbar.showSnackbar(it, withDismissAction = true)
            error = null
        }
    }

    Box(Modifier.fillMaxSize()) {
    when (val current = screen) {
        is Screen.TaskList -> TaskListScreen(
            model = taskListModel,
            onTaskClick = { screen = Screen.TaskDetail(it) },
            onInitiativeScan = {
                withCameraPermission({
                    error = "Без доступа к камере съёмка невозможна. " +
                        "Разрешение можно выдать в настройках телефона."
                }) {
                    scope.launch {
                        try {
                            val controller = container.initiativeCaptureController()
                            screen = Screen.Capture(CaptureViewModel(controller))
                        } catch (e: CaptureSessionFactory.ProtocolMissing) {
                            error = e.message
                        }
                    }
                }
            },
        )
        is Screen.TaskDetail -> {
            // Кнопка «назад» телефона возвращает к списку: без этого экран
            // деталей — тупик, из которого выходят только убийством приложения.
            BackHandler { screen = Screen.TaskList }
            // Комментарий контролёра — из локального TaskStore (приехал pull'ом).
            val verdict by androidx.compose.runtime.produceState<ru.kvant.scan.sync.SessionVerdict?>(
                initialValue = null, key1 = current.task.id,
            ) { value = container.stack.tasks.verdictForTask(current.task.id) }
            var traceItemId by remember { mutableStateOf<String?>(null) }
            TaskDetailScreen(
                task = current.task,
                verdict = verdict,
                onStartCapture = { task ->
                    // Первая лямбда — отказ в разрешении, вторая — сама съёмка.
                    withCameraPermission({
                        error = "Без доступа к камере съёмка невозможна. " +
                            "Разрешение можно выдать в настройках телефона."
                    }) {
                        scope.launch {
                            try {
                                val controller = container.captureController(task)
                                screen = Screen.Capture(CaptureViewModel(controller))
                            } catch (e: CaptureSessionFactory.ProtocolMissing) {
                                // Честная ошибка ДО съёмки: протокол не синхронизирован.
                                error = e.message
                            }
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
        is Screen.Capture -> {
            // «Назад» уводит к списку, но НЕ закрывает сессию: события уже в
            // outbox, закрыть её может только abandon с причиной (§04.6).
            // Возврат в задание вернёт человека на тот же шаг съёмки.
            BackHandler { screen = Screen.TaskList }
            CaptureScreen(viewModel = current.viewModel)
        }
        is Screen.ItemCard -> {
            BackHandler { screen = current.from }
            ItemCardScreen(card = current.card)
        }
    }
        SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter))
    }
}
