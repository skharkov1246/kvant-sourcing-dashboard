import SwiftUI
import shared

/// Точка входа iOS — двойник MainActivity: список → детали → съёмка.
/// Граф зависимостей — общий SyncStack из ядра; здесь только платформенное:
/// идентификатор устройства и адрес дев-стенда (localhost симулятора).
@main
struct KvantScanApp: App {
    private let container = AppContainer()

    var body: some Scene {
        WindowGroup {
            RootView(container: container)
        }
    }
}

final class AppContainer {
    let stack: SyncStack

    init(baseUrl: String = "http://127.0.0.1:8077",
         tenantId: String = "t-internal",
         userId: String = "u-operator") {
        let capabilities = JsonHelpers.shared.parseJsonObject(
            text: #"{"schema_version": 1, "app_version": "0.1.0", "platform": "ios", "max_accuracy_class": "B"}"#)
        stack = SyncStack(config: SyncConfig(
            baseUrl: baseUrl,
            tenantId: tenantId,
            userId: userId,
            deviceId: "ios-dev",
            roles: "operator",
            bearerToken: nil,
            capabilities: capabilities
        ))
    }

    /// Сессия по заданию: протокол из локального TaskStore (фабрика ядра).
    func captureController(task: LocalTask) async throws -> CaptureSessionController {
        try await CaptureSessionFactory.shared.forTask(
            task: task,
            tasks: stack.tasks,
            sessionId: "s-" + UUID().uuidString.lowercased(),
            outbox: stack.outbox,
            clock: { self.stack.now() },
            newEventId: { UUID().uuidString.lowercased() },
            deviceMaxAccuracy: .b
        )
    }
}

private struct RootView: View {
    let container: AppContainer
    @StateObject private var taskList: TaskListViewModel
    @State private var captureModel: CaptureViewModel?
    @State private var protocolError: String?

    init(container: AppContainer) {
        self.container = container
        _taskList = StateObject(wrappedValue: TaskListViewModel(
            store: container.stack.tasks, sync: container.stack.engine))
    }

    var body: some View {
        NavigationStack {
            TaskListView(model: taskList) { task in
                // Навигация значением: NavigationLink не нужен, съёмка
                // открывается после асинхронной сборки контроллера.
            }
            .navigationDestination(item: $captureModel) { model in
                CaptureView(model: model)
            }
        }
        .environment(\.openTaskDetail) { task in
            Task { await startCapture(task) }
        }
        .alert("Протокол не синхронизирован", isPresented: .constant(protocolError != nil)) {
            Button("Ок") { protocolError = nil }
        } message: { Text(protocolError ?? "") }
    }

    @MainActor
    private func startCapture(_ task: LocalTask) async {
        do {
            let controller = try await container.captureController(task: task)
            captureModel = CaptureViewModel(controller: controller)
        } catch {
            // Честная ошибка ДО съёмки (ProtocolMissing из фабрики ядра).
            protocolError = (error as NSError).localizedDescription
        }
    }
}

/// Хук деталей задания: замыкание в Environment вместо жёсткой навигации.
private struct OpenTaskDetailKey: EnvironmentKey {
    static let defaultValue: (LocalTask) -> Void = { _ in }
}

extension EnvironmentValues {
    var openTaskDetail: (LocalTask) -> Void {
        get { self[OpenTaskDetailKey.self] }
        set { self[OpenTaskDetailKey.self] = newValue }
    }
}

extension CaptureViewModel: Identifiable {}
