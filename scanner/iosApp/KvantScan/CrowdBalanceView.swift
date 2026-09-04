import SwiftUI
import shared

/// Баланс и кампания участника краудсорсинга — двойник Android
/// `CrowdBalanceScreen.kt`. Экран крауд-приложения; в операторское
/// приложение не монтируется — хост появится вместе с онбордингом.
///
/// Правило гашения: решение принимает ядро (CampaignCard.allowsCapture) —
/// здесь только честное предупреждение, что кампания на исходе, ДО
/// потраченного участником времени.
struct CrowdBalanceView: View {
    let ledger: ContributorLedger
    let campaign: CampaignCard?
    let scanCostCents: Int32

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                Text("Баланс").font(.subheadline).bold()
                Text(eur(ledger.balanceCents)).font(.largeTitle)

                if let card = campaign {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(card.title).font(.subheadline).bold()
                        Text("Остаток кампании: \(eur(card.remainingCents))")
                            .font(.subheadline)
                        if !card.allowsCapture(scanCostCents: scanCostCents) {
                            Text("Кампания завершена или бюджет исчерпан — сканы по ней не оплачиваются")
                                .font(.caption)
                                .foregroundStyle(.red)
                        } else if card.remainingCents < scanCostCents * 3 {
                            Text("Кампания на исходе: остатка хватит меньше чем на 3 скана")
                                .font(.caption)
                                .foregroundStyle(.orange)
                        }
                    }
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
                }

                Text("История").font(.subheadline).bold().padding(.top, 8)
                ForEach(Array(ledger.lines.enumerated()), id: \.offset) { _, line in
                    HStack {
                        Text(kindTitle(line.kind) +
                             (line.note.map { " · \($0)" } ?? ""))
                            .font(.caption)
                        Spacer()
                        Text((line.amountCents > 0 ? "+" : "") + eur(line.amountCents))
                            .font(.caption)
                            .foregroundStyle(line.amountCents >= 0 ? .blue : .red)
                    }
                    .padding(.vertical, 2)
                }
            }
            .padding(16)
        }
        .navigationTitle("Вознаграждение")
    }

    private func eur(_ cents: Int32) -> String {
        let sign = cents < 0 ? "-" : ""
        let abs = Swift.abs(Int(cents))
        return "\(sign)\(abs / 100),\(String(format: "%02d", abs % 100)) €"
    }

    private func kindTitle(_ kind: String) -> String {
        switch kind {
        case "ACCRUAL": return "Принятый скан"
        case "BONUS": return "Бонус за субпоставщика"
        case "CLAWBACK": return "Сторно (аудит)"
        case "PAYOUT": return "Выплата"
        default: return kind
        }
    }
}
