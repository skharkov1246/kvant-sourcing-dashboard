import SwiftUI
import shared

/// Экран списка заданий — двойник Android `TaskListScreen.kt`.
/// Тонкий по построению: содержимое и порядок даёт ядро, сервер уже
/// отфильтровал задания по возможностям устройства. Тап — в съёмку.
struct TaskListView: View {
    @ObservedObject var model: TaskListViewModel
    let onTaskTap: (LocalTask) -> Void

    var body: some View {
        NavigationStack {
            List {
                if model.syncError != nil {
                    // Сеть упала — список прежний, это норма склада (§04.1).
                    Text("Не обновилось: работаем по сохранённому списку")
                        .font(.caption)
                        .foregroundStyle(.red)
                }
                if model.tasks.isEmpty && !model.isRefreshing {
                    Text("Заданий нет. Потяните вниз или отсканируйте деталь без задания.")
                        .font(.body)
                }
                ForEach(model.tasks, id: \.id) { task in
                    TaskRow(task: task)
                        .contentShape(Rectangle())
                        .onTapGesture { onTaskTap(task) }
                }
            }
            .navigationTitle("Задания")
            .refreshable { await model.refresh() }
        }
    }
}

private struct TaskRow: View {
    let task: LocalTask

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(task.title ?? "Позиция без названия")
                    .font(.headline)
                Spacer()
                if task.state == "REWORK" {
                    Text("Пересъёмка")
                        .font(.caption)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(.orange.opacity(0.2), in: Capsule())
                }
            }
            HStack {
                Text(task.protocolCode +
                     (task.protocolVersion.map { "@\($0)" } ?? ""))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                if let due = task.dueAt {
                    Text("до \(String(due.prefix(10)))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(.vertical, 2)
    }
}
