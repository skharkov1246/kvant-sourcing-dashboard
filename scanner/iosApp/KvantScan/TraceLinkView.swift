import SwiftUI
import shared

/// Форма заявленной трассировки — двойник Android `TraceLinkDialog.kt`.
/// Логика целиком в общей `TraceLinkForm` (KMP): здесь только зеркалирование
/// её полей в SwiftUI-состояние. Поставщик выбирается тапом из подсказок
/// CRM-справочника, правка текста снимает выбор (правило формы, не UI).
struct TraceLinkView: View {
    let form: TraceLinkForm
    let itemId: String
    let onDone: (Bool) -> Void

    @State private var query = ""
    @State private var codeType = "gtin"
    @State private var codeValue = ""
    /// Счётчик правок — дешёвая инвалидация подсказок после select/edit.
    @State private var revision = 0
    @State private var sending = false

    var body: some View {
        let _ = revision
        NavigationStack {
            Form {
                Section("Поставщик (необязательно)") {
                    TextField("Название или ИНН", text: $query)
                        .onChange(of: query) { value in
                            form.editSupplierQuery(query: value)
                            revision += 1
                        }
                    if form.selectedSupplier == nil {
                        ForEach(form.suggestions(limit: 5), id: \.name) { supplier in
                            Button {
                                form.selectSupplier(supplier: supplier)
                                query = form.supplierQuery
                                revision += 1
                            } label: {
                                Text(supplier.name +
                                     (supplier.inn.map { " · ИНН \($0)" } ?? ""))
                                    .font(.subheadline)
                            }
                        }
                    }
                }
                Section("Код") {
                    Picker("Тип", selection: $codeType) {
                        ForEach(form.codeTypes, id: \.self) { Text($0) }
                    }
                    .onChange(of: codeType) { form.setCodeType(type: $0) }
                    TextField("Значение", text: $codeValue)
                        .onChange(of: codeValue) { form.setCodeValue(value: $0) }
                }
            }
            .navigationTitle("Трассировка")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Отмена") { onDone(false) }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Отправить") {
                        sending = true
                        Task {
                            // Сеть/сервер: заявка не ушла — форма сообщит хосту.
                            let ok = (try? await form.submit(itemId: itemId))?
                                .boolValue ?? false
                            sending = false
                            onDone(ok)
                        }
                    }
                    .disabled(sending || !form.canSubmit())
                }
            }
        }
    }
}
