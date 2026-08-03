import SwiftUI
import shared

/// Гид по шагам на iOS.
///
/// Экран — интерпретатор протокола, а не сценарий: он рисует то, что вернуло
/// общее ядро. Тот же набор восьми примитивов, что на Android; расхождение
/// поведения между платформами здесь было бы расхождением данных.
struct CaptureView: View {
    @StateObject var model: CaptureViewModel
    @State private var showAbandon = false
    @State private var abandonReason = ""

    var body: some View {
        content
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Прервать…") { showAbandon = true }
                }
            }
            // Прерывание — явное действие с ПРИЧИНОЙ, а не жест «назад»:
            // события уже в outbox, брошенная молча сессия повисла бы ACTIVE (§04.6).
            .alert("Прервать сессию?", isPresented: $showAbandon) {
                TextField("Причина (обязательно)", text: $abandonReason)
                Button("Прервать", role: .destructive) {
                    let reason = abandonReason.trimmingCharacters(in: .whitespaces)
                    if !reason.isEmpty { model.abandonSession(reason: reason) }
                }
                Button("Продолжить съёмку", role: .cancel) {}
            }
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 12) {
            ProgressView(value: model.progressFraction)
            Text("Шаг \(model.doneSteps + 1) из \(model.totalSteps)")
                .font(.caption)

            if let step = model.currentStep {
                Text(step.title).font(.title2)
                if let instruction = step.instruction {
                    Text(instruction).font(.body).foregroundStyle(.secondary)
                }

                switch step.kind {
                case .photo:   PhotoStepView(step: step, model: model)
                case .video:   VideoStepView(step: step, model: model)
                case .measure: MeasureStepView(step: step, model: model)
                case .code:    CodeStepView(step: step, model: model)
                case .form:    FormStepView(step: step, model: model)
                case .weight:  WeightStepView(step: step, model: model)
                case .note:    NoteStepView(step: step, model: model)
                case .confirm: ConfirmStepView(step: step, model: model)
                case .none:
                    // Молча пропускать нельзя: кладовщик не поймёт, почему
                    // задание не закрывается (§03.7, правило 1).
                    Text("Шаг «\(step.title)» требует более новой версии приложения.")
                }

                if !model.warnings.isEmpty {
                    QualityWarningsView(warnings: model.warnings, onRetake: model.retakeCurrentStep)
                }
            } else {
                SessionCompleteView(model: model)
            }
            Spacer()
        }
        .padding()
    }
}
