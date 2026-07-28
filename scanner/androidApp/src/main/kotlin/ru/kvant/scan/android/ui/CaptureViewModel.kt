package ru.kvant.scan.android.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import ru.kvant.scan.capture.CaptureSessionController
import ru.kvant.scan.domain.AccuracyClass

/**
 * Тонкая обёртка над [CaptureSessionController] из общего ядра.
 *
 * Вся логика прохождения сессии живёт в контроллере и тестируется без
 * эмулятора; здесь — только перекладка его состояния в StateFlow и запуск
 * suspend-вызовов в viewModelScope. Ошибка перехода (Illegal Transition) —
 * это баг UI, показавшего недоступное действие, поэтому она не глотается,
 * а отображается как warning текущего шага.
 */
class CaptureViewModel(
    private val controller: CaptureSessionController,
) : ViewModel() {

    private val _state = MutableStateFlow(controller.state())
    val state: StateFlow<CaptureSessionController.UiState> = _state.asStateFlow()

    private val _transitionError = MutableStateFlow<String?>(null)
    val transitionError: StateFlow<String?> = _transitionError.asStateFlow()

    init {
        viewModelScope.launch {
            controller.start()
            refresh()
        }
    }

    fun completeCurrentStep(
        payload: JsonObject = JsonObject(emptyMap()),
        assetIds: List<String> = emptyList(),
        qualityScore: Double? = null,
        violations: List<String> = emptyList(),
        measuredAccuracy: AccuracyClass? = null,
    ) {
        val stepId = _state.value.currentStep?.id ?: return
        launchTransition {
            controller.completeStep(stepId, payload, assetIds, qualityScore, violations, measuredAccuracy)
        }
    }

    fun skipCurrentStep() {
        val stepId = _state.value.currentStep?.id ?: return
        launchTransition { controller.skipStep(stepId) }
    }

    fun retakeCurrentStep() {
        val stepId = _state.value.currentStep?.id ?: return
        controller.retakeStep(stepId)
        refresh()
    }

    fun completeSession() = launchTransition { controller.completeSession() }

    fun abandonSession(reason: String) = launchTransition { controller.abandonSession(reason) }

    private fun launchTransition(block: suspend () -> Unit) {
        viewModelScope.launch {
            try {
                block()
                _transitionError.value = null
            } catch (e: CaptureSessionController.IllegalTransition) {
                _transitionError.value = e.message
            }
            refresh()
        }
    }

    private fun refresh() {
        _state.value = controller.state()
    }
}
