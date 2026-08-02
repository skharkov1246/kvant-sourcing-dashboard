package ru.kvant.scan.android.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import ru.kvant.scan.sync.LocalTask
import ru.kvant.scan.sync.PullApplier
import ru.kvant.scan.sync.SyncEngine
import ru.kvant.scan.sync.TaskStore
import ru.kvant.scan.sync.TransportException

/**
 * Список заданий поверх локального [TaskStore] (двойник — iOS
 * TaskListViewModel.swift). Сортировка «срочные сверху» живёт в ядре и
 * покрыта тестами; здесь — перекладка в StateFlow и запуск pull.
 *
 * Офлайн-первичность (§04.1): список всегда читается из TaskStore; pull
 * лишь обновляет его. Ошибка сети не пустой экран, а вчерашний список
 * с плашкой «не обновилось».
 */
class TaskListViewModel(
    private val tasks: TaskStore,
    private val sync: SyncEngine,
) : ViewModel() {

    data class UiState(
        val tasks: List<LocalTask> = emptyList(),
        val isRefreshing: Boolean = false,
        val syncError: String? = null,
    )

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    // Курсор пока живёт в памяти процесса; персистентность — в directory_cache
    // по образцу справочников, когда подключится SQLDelight-драйвер.
    private var cursor: String? = null

    init {
        viewModelScope.launch { reload() }
        refresh()
    }

    fun refresh() {
        viewModelScope.launch {
            _state.value = _state.value.copy(isRefreshing = true)
            try {
                cursor = sync.pullOnce(cursor, PullApplier(tasks))
                _state.value = _state.value.copy(syncError = null)
            } catch (e: TransportException) {
                // Сеть упала — список остаётся прежним, это норма склада.
                _state.value = _state.value.copy(syncError = e.message)
            }
            reload()
            _state.value = _state.value.copy(isRefreshing = false)
        }
    }

    private suspend fun reload() {
        _state.value = _state.value.copy(tasks = tasks.tasks())
    }
}
