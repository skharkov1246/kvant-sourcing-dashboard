package ru.kvant.scan.android.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import ru.kvant.scan.capture.CaptureSessionController
import ru.kvant.scan.protocol.CaptureStep
import ru.kvant.scan.protocol.FormField

/**
 * Восемь примитивов протокола — по компоненту на kind (§03.3).
 *
 * Форма, подтверждение, заметка и вес — чистый Compose и завершаются здесь же.
 * Фото, видео, обмер и код требуют камеры/AR: их запускает Activity через
 * [CaptureActions] (CameraX, ArCoreMeasurementEngine, ML Kit сканер), а сюда
 * возвращаются готовые asset_id и payload — компонент шага никогда не трогает
 * платформенный API напрямую.
 */
interface CaptureActions {
    fun launchPhoto(step: CaptureStep)
    fun launchVideo(step: CaptureStep)
    fun launchMeasure(step: CaptureStep)
    fun launchCodeScan(step: CaptureStep)
}

val LocalCaptureActions = compositionLocalOf<CaptureActions> {
    error("CaptureActions не предоставлен: оберните экран в CompositionLocalProvider")
}

// ── Камерные шаги: делегируют запуск Activity ────────────────────────────────

@Composable
fun PhotoStep(step: CaptureStep, state: CaptureSessionController.UiState, viewModel: CaptureViewModel) {
    val actions = LocalCaptureActions.current
    val repeat = step.repeat
    if (repeat != null && repeat.min > 1) {
        Text("Нужно кадров: от ${repeat.min} до ${repeat.max}")
    }
    Button(onClick = { actions.launchPhoto(step) }, modifier = Modifier.fillMaxWidth()) {
        Text("Открыть камеру")
    }
}

@Composable
fun VideoStep(step: CaptureStep, viewModel: CaptureViewModel) {
    val actions = LocalCaptureActions.current
    step.video?.let { Text("Видео ~${it.durationS} с" + if (it.requireMotion) ", обойдите объект" else "") }
    Button(onClick = { actions.launchVideo(step) }, modifier = Modifier.fillMaxWidth()) {
        Text("Начать съёмку")
    }
}

@Composable
fun MeasureStep(step: CaptureStep, state: CaptureSessionController.UiState, viewModel: CaptureViewModel) {
    val actions = LocalCaptureActions.current
    step.measure?.let { spec ->
        Text("Цель: ${spec.target}, класс не хуже ${spec.minAccuracyClass}")
    }
    Button(onClick = { actions.launchMeasure(step) }, modifier = Modifier.fillMaxWidth()) {
        Text("Начать обмер")
    }
}

@Composable
fun CodeStep(step: CaptureStep, viewModel: CaptureViewModel) {
    val actions = LocalCaptureActions.current
    Button(onClick = { actions.launchCodeScan(step) }, modifier = Modifier.fillMaxWidth()) {
        Text("Сканировать код")
    }
    if (step.code?.allowManual == true) {
        var manual by remember { mutableStateOf("") }
        OutlinedTextField(
            value = manual,
            onValueChange = { manual = it },
            label = { Text("Ввести вручную") },
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedButton(
            onClick = {
                viewModel.completeCurrentStep(
                    payload = JsonObject(mapOf(
                        "raw_value" to JsonPrimitive(manual),
                        "source" to JsonPrimitive("manual"),
                    ))
                )
            },
            enabled = manual.isNotBlank(),
            modifier = Modifier.fillMaxWidth(),
        ) { Text("Подтвердить ввод") }
    }
}

// ── Некамерные шаги: завершаются прямо здесь ─────────────────────────────────

@Composable
fun FormStep(step: CaptureStep, viewModel: CaptureViewModel) {
    val fields = step.form?.fields ?: return
    val values = remember(step.id) { mutableStateMapOf<String, String>() }

    fields.forEach { field ->
        FormFieldInput(field, values[field.key].orEmpty()) { values[field.key] = it }
    }

    val requiredFilled = fields.filter { it.required }.all { !values[it.key].isNullOrBlank() }
    Button(
        onClick = {
            viewModel.completeCurrentStep(
                payload = JsonObject(values.mapValues { (_, v) -> JsonPrimitive(v) })
            )
        },
        enabled = requiredFilled,
        modifier = Modifier.fillMaxWidth(),
    ) { Text("Готово") }
}

@Composable
private fun FormFieldInput(field: FormField, value: String, onChange: (String) -> Unit) {
    when (field.type) {
        "bool" -> Row {
            Checkbox(checked = value == "true", onCheckedChange = { onChange(it.toString()) })
            Text(field.label ?: field.key, modifier = Modifier.padding(top = 12.dp))
        }
        // enum со справочником рисуется как выпадающий список из локальной
        // копии справочника; до подключения SQLDelight-слоя — текстовый ввод.
        else -> OutlinedTextField(
            value = value,
            onValueChange = onChange,
            label = { Text((field.label ?: field.key) + if (field.required) " *" else "") },
            supportingText = field.unit?.let { { Text(it) } },
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

@Composable
fun WeightStep(step: CaptureStep, viewModel: CaptureViewModel) {
    var kg by remember { mutableStateOf("") }
    OutlinedTextField(
        value = kg,
        onValueChange = { kg = it },
        label = { Text("Вес, ${step.weight?.unit ?: "kg"}") },
        modifier = Modifier.fillMaxWidth(),
    )
    Button(
        onClick = {
            viewModel.completeCurrentStep(
                payload = JsonObject(mapOf(
                    "value" to JsonPrimitive(kg.replace(',', '.').toDouble()),
                    "source" to JsonPrimitive("manual"),
                ))
            )
        },
        enabled = kg.replace(',', '.').toDoubleOrNull()?.let { it > 0 } == true,
        modifier = Modifier.fillMaxWidth(),
    ) { Text("Записать вес") }
}

@Composable
fun NoteStep(step: CaptureStep, viewModel: CaptureViewModel) {
    var text by remember { mutableStateOf("") }
    OutlinedTextField(
        value = text,
        onValueChange = { text = it },
        label = { Text("Заметка (можно голосом)") },
        minLines = 3,
        modifier = Modifier.fillMaxWidth(),
    )
    Button(
        onClick = { viewModel.completeCurrentStep(payload = JsonObject(mapOf("text" to JsonPrimitive(text)))) },
        modifier = Modifier.fillMaxWidth(),
    ) { Text("Сохранить") }
}

@Composable
fun ConfirmStep(step: CaptureStep, viewModel: CaptureViewModel) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Text(step.confirm?.text.orEmpty(), modifier = Modifier.padding(16.dp))
    }
    Button(
        onClick = {
            viewModel.completeCurrentStep(payload = JsonObject(mapOf("confirmed" to JsonPrimitive(true))))
        },
        modifier = Modifier.fillMaxWidth(),
    ) { Text("Подтверждаю") }
}

// ── Служебные панели ─────────────────────────────────────────────────────────

@Composable
fun QualityWarnings(warnings: List<String>, onRetake: () -> Unit) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(12.dp)) {
            warnings.forEach { Text("⚠ $it") }
            // Мягкое предупреждение (§03.4): переснять — предложение, не стена.
            OutlinedButton(onClick = onRetake, modifier = Modifier.fillMaxWidth().padding(top = 8.dp)) {
                Text("Переснять")
            }
        }
    }
}

@Composable
fun SessionCompletePanel(state: CaptureSessionController.UiState, viewModel: CaptureViewModel) {
    Text("Все шаги пройдены: ${state.doneSteps} из ${state.totalSteps}")
    if (state.autoAccept) {
        Text("Сессия проходит критерии автоприёма — проверка не потребуется.")
    }
    Button(onClick = viewModel::completeSession, modifier = Modifier.fillMaxWidth()) {
        Text("Завершить и отправить")
    }
    // Отправка не ждёт сеть: события уже в outbox, медиа догрузится фоном (§04.1).
    Text("Данные отправятся автоматически при появлении связи.")
}
