import SwiftUI
import shared

/// Детали задания — двойник Android `TaskDetailScreen.kt`.
/// Subject показывается целиком из raw-объекта сервера: деталям нужно всё,
/// что прислала внешняя система, включая незнакомые поля (§09.7).
struct TaskDetailView: View {
    let task: LocalTask
    var verdict: SessionVerdict?
    var onAddTrace: (() -> Void)?
    var onOpenItemCard: (() -> Void)?
    let onStartCapture: (LocalTask) -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                Text(task.title ?? "Позиция без названия")
                    .font(.title2)
                Text(task.protocolCode +
                     (task.protocolVersion.map { "@\($0)" } ?? ""))
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                if let due = task.dueAt {
                    Text("Срок: \(String(due.prefix(10)))")
                        .font(.subheadline)
                }
                switch task.state {
                case "REWORK":
                    Text("Возвращено контролёром на пересъёмку")
                        .font(.subheadline).foregroundStyle(.red)
                case "REJECTED":
                    Text("Забраковано контролёром")
                        .font(.subheadline).foregroundStyle(.red)
                case "ACCEPTED", "EXPORTED":
                    Text("Принято контролёром").font(.subheadline)
                default:
                    EmptyView()
                }
                if let comment = verdict?.comment, !comment.isEmpty {
                    Text("Комментарий контролёра: \(comment)")
                        .font(.subheadline)
                }

                if let subject = task.raw["subject"] as? KotlinxSerializationJsonJsonObject {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(Array(subject.keys), id: \.self) { key in
                            HStack {
                                Text(key).font(.caption)
                                Spacer()
                                Text(primitiveText(subject[key]))
                                    .font(.caption)
                            }
                        }
                    }
                    .padding(12)
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
                }

                Button(task.state == "REWORK" ? "Переснять" : "Начать съёмку") {
                    onStartCapture(task)
                }
                .buttonStyle(.borderedProminent)
                .frame(maxWidth: .infinity)

                // Дополнить трассировку можно только у принятой съёмки:
                // карточка товара материализуется вердиктом ACCEPTED.
                if let onAddTrace, verdict?.verdict == "ACCEPTED" {
                    Button("Дополнить трассировку", action: onAddTrace)
                        .buttonStyle(.bordered)
                        .frame(maxWidth: .infinity)
                }
                if let onOpenItemCard, verdict?.verdict == "ACCEPTED" {
                    Button("Карточка товара", action: onOpenItemCard)
                        .buttonStyle(.bordered)
                        .frame(maxWidth: .infinity)
                }
            }
            .padding(16)
        }
    }

    private func primitiveText(_ element: Any?) -> String {
        if let primitive = element as? KotlinxSerializationJsonJsonPrimitive {
            return primitive.content
        }
        return element.map { "\($0)" } ?? ""
    }
}
