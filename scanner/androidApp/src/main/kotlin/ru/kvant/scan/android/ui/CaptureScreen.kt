package ru.kvant.scan.android.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import ru.kvant.scan.protocol.CaptureStep
import ru.kvant.scan.protocol.StepKind

/**
 * Гид по шагам — то место, где «приложение ведёт кладовщика».
 *
 * Экран не знает, что такое паллета: он рисует то, что вернул интерпретатор
 * протокола из общего ядра (ADR-0003). Новая категория товара — это новая
 * версия протокола, а не правка этого файла.
 */
@Composable
fun CaptureScreen(viewModel: CaptureViewModel) {
    val state by viewModel.state.collectAsState()

    Column(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        // Прогресс считается по видимым обязательным шагам: условные шаги
        // (например, фото повреждений) не должны портить индикатор.
        LinearProgressIndicator(
            progress = { if (state.totalSteps == 0) 0f else state.doneSteps.toFloat() / state.totalSteps },
            modifier = Modifier.fillMaxWidth(),
        )
        Text(
            text = "Шаг ${state.doneSteps + 1} из ${state.totalSteps}",
            style = MaterialTheme.typography.labelMedium,
        )

        val step = state.currentStep
        if (step == null) {
            SessionCompletePanel(state, viewModel)
            return@Column
        }

        Text(step.title, style = MaterialTheme.typography.headlineSmall)
        step.instruction?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }

        Spacer(Modifier.height(8.dp))

        // Каждый примитив — свой компонент. Их ровно восемь; девятый добавляется
        // только когда появляется новая АППАРАТНАЯ способность (§03.3).
        when (step.kind) {
            StepKind.PHOTO -> PhotoStep(step, state, viewModel)
            StepKind.VIDEO -> VideoStep(step, viewModel)
            StepKind.MEASURE -> MeasureStep(step, state, viewModel)
            StepKind.CODE -> CodeStep(step, viewModel)
            StepKind.FORM -> FormStep(step, viewModel)
            StepKind.WEIGHT -> WeightStep(step, viewModel)
            StepKind.NOTE -> NoteStep(step, viewModel)
            StepKind.CONFIRM -> ConfirmStep(step, viewModel)
            null -> UnsupportedStep(step)
        }

        // Мягкое предупреждение вместо жёсткой блокировки — сознательное решение
        // (§03.4): кладовщик под давлением плана всегда обойдёт блокировку,
        // сняв стену. Лучше пустить и поймать на серверном контроле качества.
        if (state.warnings.isNotEmpty()) {
            QualityWarnings(state.warnings, onRetake = viewModel::retakeCurrentStep)
        }

        if (!step.required) {
            Button(onClick = viewModel::skipCurrentStep, modifier = Modifier.fillMaxWidth()) {
                Text("Пропустить шаг")
            }
        }
    }
}

@Composable
private fun UnsupportedStep(step: CaptureStep) {
    // Молча пропускать нельзя (§03.7, правило 1) — иначе кладовщик не поймёт,
    // почему задание не закрывается.
    Text(
        text = "Шаг «${step.title}» требует более новой версии приложения. " +
            "Обновите приложение и вернитесь к заданию.",
        style = MaterialTheme.typography.bodyMedium,
    )
}
