import Foundation
import shared

/// Тонкая обёртка над `CaptureSessionController` из общего ядра — двойник
/// Android-версии (`androidApp/.../CaptureViewModel.kt`).
///
/// Вся логика прохождения сессии живёт в KMP-контроллере и тестируется без
/// симулятора; здесь — только перекладка его состояния в @Published и запуск
/// suspend-вызовов через Swift Concurrency (KMP экспортирует suspend-функции
/// как async). Ошибка перехода — баг UI, показавшего недоступное действие:
/// не глотается, а отображается предупреждением текущего шага.
@MainActor
final class CaptureViewModel: ObservableObject {

    @Published private(set) var currentStep: CaptureStep?
    @Published private(set) var doneSteps: Int = 0
    @Published private(set) var totalSteps: Int = 0
    @Published private(set) var warnings: [String] = []
    @Published private(set) var isComplete: Bool = false
    @Published private(set) var autoAccept: Bool = false
    @Published private(set) var transitionError: String?

    var progressFraction: Double {
        totalSteps == 0 ? 0 : Double(doneSteps) / Double(totalSteps)
    }

    private let controller: CaptureSessionController

    init(controller: CaptureSessionController) {
        self.controller = controller
        refresh()
        Task { await transition { try await self.controller.start() } }
    }

    // MARK: - Действия шагов

    func completeCurrentStep(
        payload: KotlinxSerializationJsonJsonObject = PayloadBuilder.shared.fromStrings(values: [:]),
        assetIds: [String] = [],
        qualityScore: Double? = nil,
        violations: [String] = [],
        measuredAccuracy: AccuracyClass? = nil
    ) {
        guard let stepId = currentStep?.id else { return }
        Task {
            await transition {
                try await self.controller.completeStep(
                    stepId: stepId,
                    payload: payload,
                    assetIds: assetIds,
                    qualityScore: qualityScore.map { KotlinDouble(value: $0) },
                    violations: violations,
                    measuredAccuracy: measuredAccuracy
                )
            }
        }
    }

    /// Удобный путь для форм: словарь строк → JsonObject через мост ядра.
    func completeCurrentStep(fields: [String: String]) {
        completeCurrentStep(payload: PayloadBuilder.shared.fromStrings(values: fields))
    }

    func skipCurrentStep() {
        guard let stepId = currentStep?.id else { return }
        Task { await transition { try await self.controller.skipStep(stepId: stepId) } }
    }

    func retakeCurrentStep() {
        guard let stepId = currentStep?.id else { return }
        controller.retakeStep(stepId: stepId)
        refresh()
    }

    func completeSession() {
        Task { await transition { try await self.controller.completeSession() } }
    }

    func abandonSession(reason: String) {
        Task { await transition { try await self.controller.abandonSession(reason: reason) } }
    }

    // MARK: - Внутреннее

    private func transition(_ block: @escaping () async throws -> Void) async {
        do {
            try await block()
            transitionError = nil
        } catch {
            // IllegalTransition из ядра приходит как KotlinException —
            // текст причины уже человекочитаемый (на русском, из контроллера).
            transitionError = (error as NSError).localizedDescription
        }
        refresh()
    }

    private func refresh() {
        let state = controller.state()
        currentStep = state.currentStep
        doneSteps = Int(state.doneSteps)
        totalSteps = Int(state.totalSteps)
        warnings = state.warnings
        isComplete = state.isComplete
        autoAccept = state.autoAccept
    }
}
