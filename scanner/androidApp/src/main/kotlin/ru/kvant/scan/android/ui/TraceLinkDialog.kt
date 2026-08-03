package ru.kvant.scan.android.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import ru.kvant.scan.trace.TraceLinkForm

/**
 * Диалог заявленной трассировки (двойник — iOS TraceLinkView.swift).
 *
 * Логика целиком в общей [TraceLinkForm]: здесь только зеркалирование её
 * полей в Compose-состояние. Поставщик выбирается из подсказок CRM-справочника
 * тапом, правка текста снимает выбор (правило формы, не UI).
 */
@Composable
fun TraceLinkDialog(
    form: TraceLinkForm,
    itemId: String,
    onDismiss: () -> Unit,
    onSent: (Boolean) -> Unit,
) {
    var query by remember { mutableStateOf(form.supplierQuery) }
    var codeType by remember { mutableStateOf(form.codeType) }
    var codeValue by remember { mutableStateOf(form.codeValue) }
    // Счётчик правок — дешёвый способ пересчитать подсказки после select/edit.
    var revision by remember { mutableIntStateOf(0) }
    val scope = rememberCoroutineScope()

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Трассировка") },
        text = {
            Column(Modifier.fillMaxWidth()) {
                OutlinedTextField(
                    value = query,
                    onValueChange = {
                        query = it
                        form.editSupplierQuery(it)
                        revision++
                    },
                    label = { Text("Поставщик (необязательно)") },
                    modifier = Modifier.fillMaxWidth(),
                )
                key(revision) {
                    if (form.selectedSupplier == null) {
                        form.suggestions(limit = 5).forEach { supplier ->
                            Text(
                                supplier.name + (supplier.inn?.let { " · ИНН $it" } ?: ""),
                                style = MaterialTheme.typography.bodyMedium,
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clickable {
                                        form.selectSupplier(supplier)
                                        query = form.supplierQuery
                                        revision++
                                    }
                                    .padding(vertical = 6.dp),
                            )
                        }
                    }
                }
                Row(Modifier.padding(vertical = 8.dp)) {
                    form.codeTypes.forEach { type ->
                        FilterChip(
                            selected = codeType == type,
                            onClick = {
                                codeType = type
                                form.setCodeType(type)
                            },
                            label = { Text(type) },
                            modifier = Modifier.padding(end = 8.dp),
                        )
                    }
                }
                OutlinedTextField(
                    value = codeValue,
                    onValueChange = {
                        codeValue = it
                        form.setCodeValue(it)
                    },
                    label = { Text("Значение кода") },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            TextButton(
                enabled = form.canSubmit(),
                onClick = {
                    scope.launch {
                        val ok = try {
                            form.submit(itemId)
                        } catch (_: Exception) {
                            false  // сеть/сервер: заявка не ушла, диалог сообщит
                        }
                        onSent(ok)
                    }
                },
            ) { Text("Отправить") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Отмена") }
        },
    )
}
