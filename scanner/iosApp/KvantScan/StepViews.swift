import SwiftUI
import shared

/// Восемь примитивов протокола — двойник Android-версии
/// (`androidApp/.../StepComponents.kt`), та же граница ответственности:
/// формы, подтверждение, вес и заметка — чистый SwiftUI и завершаются здесь;
/// фото, видео, обмер и код требуют камеры/ARKit — их запускает хост-экран
/// через `CaptureActions`, а сюда возвращаются готовые asset_id и payload.
protocol CaptureActions {
    func launchPhoto(step: CaptureStep)
    func launchVideo(step: CaptureStep)
    func launchMeasure(step: CaptureStep)
    func launchCodeScan(step: CaptureStep)
}

private struct CaptureActionsKey: EnvironmentKey {
    static let defaultValue: CaptureActions? = nil
}

extension EnvironmentValues {
    var captureActions: CaptureActions? {
        get { self[CaptureActionsKey.self] }
        set { self[CaptureActionsKey.self] = newValue }
    }
}

// MARK: - Камерные шаги: делегируют запуск хосту

struct PhotoStepView: View {
    let step: CaptureStep
    @ObservedObject var model: CaptureViewModel
    @Environment(\.captureActions) private var actions

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let repeatSpec = step.repeat_, repeatSpec.min > 1 {
                Text("Нужно кадров: от \(repeatSpec.min) до \(repeatSpec.max)")
                    .font(.caption)
            }
            Button("Открыть камеру") { actions?.launchPhoto(step: step) }
                .buttonStyle(.borderedProminent)
                .frame(maxWidth: .infinity)
        }
    }
}

struct VideoStepView: View {
    let step: CaptureStep
    @ObservedObject var model: CaptureViewModel
    @Environment(\.captureActions) private var actions

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let video = step.video {
                Text("Видео ~\(video.durationS) с" +
                     (video.requireMotion ? ", обойдите объект" : ""))
                    .font(.caption)
            }
            Button("Начать съёмку") { actions?.launchVideo(step: step) }
                .buttonStyle(.borderedProminent)
                .frame(maxWidth: .infinity)
        }
    }
}

struct MeasureStepView: View {
    let step: CaptureStep
    @ObservedObject var model: CaptureViewModel
    @Environment(\.captureActions) private var actions

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let spec = step.measure {
                Text("Цель: \(spec.target), класс не хуже \(spec.minAccuracyClass)")
                    .font(.caption)
            }
            Button("Начать обмер") { actions?.launchMeasure(step: step) }
                .buttonStyle(.borderedProminent)
                .frame(maxWidth: .infinity)
        }
    }
}

struct CodeStepView: View {
    let step: CaptureStep
    @ObservedObject var model: CaptureViewModel
    @Environment(\.captureActions) private var actions
    @State private var manual = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button("Сканировать код") { actions?.launchCodeScan(step: step) }
                .buttonStyle(.borderedProminent)
                .frame(maxWidth: .infinity)
            if step.code?.allowManual == true {
                TextField("Ввести вручную", text: $manual)
                    .textFieldStyle(.roundedBorder)
                Button("Подтвердить ввод") {
                    model.completeCurrentStep(fields: [
                        "raw_value": manual, "source": "manual",
                    ])
                }
                .disabled(manual.isEmpty)
                .frame(maxWidth: .infinity)
            }
        }
    }
}

// MARK: - Некамерные шаги: завершаются прямо здесь

struct FormStepView: View {
    let step: CaptureStep
    @ObservedObject var model: CaptureViewModel
    @State private var values: [String: String] = [:]

    private var fields: [FormField] { step.form?.fields ?? [] }
    private var requiredFilled: Bool {
        fields.filter(\.required).allSatisfy { !(values[$0.key] ?? "").isEmpty }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(fields, id: \.key) { field in
                if field.type == "bool" {
                    Toggle(field.label ?? field.key, isOn: boolBinding(field.key))
                } else {
                    TextField(
                        (field.label ?? field.key) + (field.required ? " *" : ""),
                        text: binding(field.key)
                    )
                    .textFieldStyle(.roundedBorder)
                }
            }
            Button("Готово") { model.completeCurrentStep(fields: values) }
                .buttonStyle(.borderedProminent)
                .disabled(!requiredFilled)
                .frame(maxWidth: .infinity)
        }
    }

    private func binding(_ key: String) -> Binding<String> {
        Binding(get: { values[key] ?? "" }, set: { values[key] = $0 })
    }

    private func boolBinding(_ key: String) -> Binding<Bool> {
        Binding(get: { values[key] == "true" }, set: { values[key] = $0 ? "true" : "false" })
    }
}

struct WeightStepView: View {
    let step: CaptureStep
    @ObservedObject var model: CaptureViewModel
    @State private var kg = ""

    private var parsed: Double? { Double(kg.replacingOccurrences(of: ",", with: ".")) }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField("Вес, \(step.weight?.unit ?? "kg")", text: $kg)
                .keyboardType(.decimalPad)
                .textFieldStyle(.roundedBorder)
            Button("Записать вес") {
                if let value = parsed {
                    // Kotlin Map<String, Double> экспортируется как [String: KotlinDouble]
                    model.completeCurrentStep(
                        payload: PayloadBuilder.shared.fromMixed(
                            strings: ["source": "manual"],
                            numbers: ["value": KotlinDouble(value: value)],
                            booleans: [:]
                        )
                    )
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled((parsed ?? 0) <= 0)
            .frame(maxWidth: .infinity)
        }
    }
}

struct NoteStepView: View {
    let step: CaptureStep
    @ObservedObject var model: CaptureViewModel
    @State private var text = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField("Заметка (можно голосом)", text: $text, axis: .vertical)
                .lineLimit(3...6)
                .textFieldStyle(.roundedBorder)
            Button("Сохранить") { model.completeCurrentStep(fields: ["text": text]) }
                .buttonStyle(.borderedProminent)
                .frame(maxWidth: .infinity)
        }
    }
}

struct ConfirmStepView: View {
    let step: CaptureStep
    @ObservedObject var model: CaptureViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(step.confirm?.text ?? "")
                .padding()
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
            Button("Подтверждаю") {
                model.completeCurrentStep(fields: ["confirmed": "true"])
            }
            .buttonStyle(.borderedProminent)
            .frame(maxWidth: .infinity)
        }
    }
}

// MARK: - Служебные панели

struct QualityWarningsView: View {
    let warnings: [String]
    let onRetake: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(warnings, id: \.self) { Text("⚠ \($0)") }
            // Мягкое предупреждение (§03.4): переснять — предложение, не стена.
            Button("Переснять", action: onRetake)
                .buttonStyle(.bordered)
                .frame(maxWidth: .infinity)
        }
        .padding(12)
        .background(.yellow.opacity(0.15), in: RoundedRectangle(cornerRadius: 8))
    }
}

struct SessionCompleteView: View {
    @ObservedObject var model: CaptureViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Все шаги пройдены: \(model.doneSteps) из \(model.totalSteps)")
            if model.autoAccept {
                Text("Сессия проходит критерии автоприёма — проверка не потребуется.")
                    .font(.caption)
            }
            Button("Завершить и отправить") { model.completeSession() }
                .buttonStyle(.borderedProminent)
                .frame(maxWidth: .infinity)
            // Отправка не ждёт сеть: события уже в outbox, медиа догрузится фоном (§04.1).
            Text("Данные отправятся автоматически при появлении связи.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}
