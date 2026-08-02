import Foundation
import shared

/// Список заданий поверх локального TaskStore — двойник Android-версии
/// (`androidApp/.../TaskListViewModel.kt`).
///
/// Офлайн-первичность (§04.1): список всегда читается из TaskStore; pull
/// лишь обновляет его. Ошибка сети — не пустой экран, а прежний список
/// с плашкой «не обновилось». Сортировка «срочные сверху» живёт в ядре.
@MainActor
final class TaskListViewModel: ObservableObject {

    @Published private(set) var tasks: [LocalTask] = []
    @Published private(set) var isRefreshing = false
    @Published private(set) var syncError: String?

    private let store: TaskStore
    private let sync: SyncEngine
    // Курсор пока в памяти процесса; персистентность придёт с SQLDelight.
    private var cursor: String?

    init(store: TaskStore, sync: SyncEngine) {
        self.store = store
        self.sync = sync
        Task {
            await reload()
            await refresh()
        }
    }

    func refresh() async {
        isRefreshing = true
        do {
            cursor = try await sync.pullOnce(
                cursor: cursor, applier: PullApplier(tasks: store))
            syncError = nil
        } catch {
            // Сеть упала — список остаётся прежним, это норма склада.
            syncError = (error as NSError).localizedDescription
        }
        await reload()
        isRefreshing = false
    }

    private func reload() async {
        if let current = try? await store.tasks() {
            tasks = current
        }
    }
}
