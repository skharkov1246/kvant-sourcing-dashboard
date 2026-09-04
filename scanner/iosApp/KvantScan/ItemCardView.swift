import SwiftUI
import shared

/// Карточка единицы товара — двойник Android `ItemCardScreen.kt`.
/// Значок «✓ проверено» ставится только по confidence=verified (машинное
/// чтение, подтверждённое контролёром); заявленное внешним сотрудником
/// остаётся «заявлено» (I-7).
struct ItemCardView: View {
    let card: ItemCard

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                Text(card.id).font(.caption).foregroundStyle(.secondary)
                if let score = card.qualityScore {
                    Text("Качество съёмки: \(score)").font(.subheadline)
                }

                if !card.identity.isEmpty {
                    section("Идентичность") {
                        ForEach(Array(card.identity.keys).sorted(), id: \.self) { field in
                            HStack {
                                Text(field).font(.caption)
                                Spacer()
                                Text(card.identity[field] ?? "").font(.caption)
                            }
                        }
                    }
                }

                section("Трассировка") {
                    ForEach(Array(card.trace.enumerated()), id: \.offset) { _, link in
                        HStack {
                            Text("\(link.codeType): \(link.codeValue)" +
                                 (link.supplierName.map { " · \($0)" } ?? ""))
                                .font(.caption)
                            Spacer()
                            Text(link.confidence == "verified" ? "✓ проверено" : "заявлено")
                                .font(.caption)
                                .foregroundStyle(
                                    link.confidence == "verified" ? .blue : .secondary)
                        }
                    }
                }

                if !card.measurements.isEmpty {
                    section("Замеры принятой сессии") {
                        ForEach(Array(card.measurements.enumerated()), id: \.offset) { _, m in
                            ForEach(Array(m.keys), id: \.self) { key in
                                HStack {
                                    Text(key).font(.caption)
                                    Spacer()
                                    Text(primitiveText(m[key])).font(.caption)
                                }
                            }
                        }
                    }
                }
            }
            .padding(16)
        }
        .navigationTitle("Карточка товара")
    }

    @ViewBuilder
    private func section(_ title: String, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.subheadline).bold()
            content()
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
    }

    private func primitiveText(_ element: Any?) -> String {
        if let primitive = element as? KotlinxSerializationJsonJsonPrimitive {
            return primitive.content
        }
        return element.map { "\($0)" } ?? ""
    }
}
