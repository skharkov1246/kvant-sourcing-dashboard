import SwiftUI
import shared

/// Точка входа iOS — двойник MainActivity: список → детали → съёмка.
/// Граф зависимостей — общий SyncStack из ядра; здесь только платформенное:
/// идентификатор устройства и адрес дев-стенда (localhost симулятора).
@main
struct KvantScanApp: App {
    private let container = AppContainer()

    init() {
        MediaUploadScheduler.register(stack: container.stack)
        SyncRefreshScheduler.register(stack: container.stack)
    }

    var body: some Scene {
        WindowGroup {
            RootView(container: container)
                .onAppear {
                    MediaUploadScheduler.submit()
                    SyncRefreshScheduler.submit()
                }
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
            store: container.stack.tasks, sync: container.stack.engine,
            stack: container.stack))
    }

    @State private var selectedTask: LocalTask?
    @State private var selectedVerdict: SessionVerdict?
    @State private var traceTarget: TraceTarget?
    @State private var itemCard: ItemCard?
    @State private var itemCardError: String?

    var body: some View {
        NavigationStack {
            TaskListView(model: taskList) { task in
                selectedVerdict = nil
                selectedTask = task
                Task {  // комментарий контролёра — из локального TaskStore
                    selectedVerdict = try? await container.stack.tasks
                        .verdictForTask(taskId: task.id)
                }
            }
            .navigationDestination(item: $selectedTask) { task in
                TaskDetailView(
                    task: task,
                    verdict: selectedVerdict,
                    // Карточка принятой съёмки детерминирована: itm-<session>.
                    onAddTrace: {
                        if let verdict = selectedVerdict {
                            traceTarget = TraceTarget(itemId: "itm-\(verdict.sessionId)")
                        }
                    },
                    onOpenItemCard: {
                        if let verdict = selectedVerdict {
                            Task { await openItemCard("itm-\(verdict.sessionId)") }
                        }
                    }
                ) { chosen in
                    Task { await startCapture(chosen) }
                }
            }
            .navigationDestination(item: $captureModel) { model in
                CaptureView(model: model)
            }
            .navigationDestination(item: $itemCard) { card in
                ItemCardView(card: card)
            }
        }
        .sheet(item: $traceTarget) { target in
            TraceLinkView(
                form: container.stack.traceLinkForm(),
                itemId: target.itemId,
                onCancel: { traceTarget = nil },
                onSubmitted: { delivered in
                    traceTarget = nil
                    // false — не ошибка: заявка в очереди и уедет со следующим
                    // проходом синка (офлайн-надёжность, I-1).
                    if !delivered {
                        itemCardError = "Сети нет — заявка в очереди, уйдёт автоматически"
                    }
                }
            )
        }
        .alert("Протокол не синхронизирован", isPresented: .constant(protocolError != nil)) {
            Button("Ок") { protocolError = nil }
        } message: { Text(protocolError ?? "") }
        .alert("Карточка товара", isPresented: .constant(itemCardError != nil)) {
            Button("Ок") { itemCardError = nil }
        } message: { Text(itemCardError ?? "") }
    }

    @MainActor
    private func openItemCard(_ itemId: String) async {
        do {
            if let card = try await container.stack.fetchItemCard(itemId: itemId) {
                itemCard = card
            } else {
                // Честное «карточки нет» (сессия без кодов) — не ошибка сети.
                itemCardError = "Карточка ещё не создана: в сессии не было кодов"
            }
        } catch {
            itemCardError = "Карточка недоступна: \((error as NSError).localizedDescription)"
        }
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

extension CaptureViewModel: Identifiable {}

extension LocalTask: Identifiable {}

private struct TraceTarget: Identifiable {
    let itemId: String
    var id: String { itemId }
}

extension ItemCard: Identifiable {}
