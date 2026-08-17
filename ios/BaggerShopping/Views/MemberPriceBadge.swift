import SwiftUI

struct MemberPriceBadge: View {
    let offer: GroceryOffer
    var compact = false

    var body: some View {
        if let memberPrice = offer.memberPrice {
            VStack(alignment: .leading, spacing: compact ? 2 : 3) {
                HStack(spacing: compact ? 4 : 5) {
                    Image(systemName: "person.crop.circle.badge.checkmark")
                    Text(offer.memberPriceDisplayLabel)
                        .lineLimit(1)
                    Text(memberPrice, format: .currency(code: "DKK").precision(.fractionLength(memberPrice.rounded() == memberPrice ? 0 : 2)))
                        .monospacedDigit()
                        .lineLimit(1)
                }
                .font(compact ? .caption2.weight(.semibold) : .caption.weight(.semibold))
                .foregroundStyle(.red)
                .padding(.horizontal, compact ? 7 : 8)
                .padding(.vertical, compact ? 4 : 5)
                .background(Color.red.opacity(0.10), in: Capsule())

                if let activationHint = offer.memberPriceActivationHint {
                    Label(activationHint, systemImage: "bolt.badge.checkmark")
                        .font(.caption2.weight(.medium))
                        .foregroundStyle(.red.opacity(0.82))
                        .lineLimit(1)
                        .padding(.leading, compact ? 4 : 6)
                }
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel(accessibilityLabel(memberPrice: memberPrice))
        }
    }

    private func accessibilityLabel(memberPrice: Double) -> String {
        let price = memberPrice.formatted(.currency(code: "DKK"))
        if let activationHint = offer.memberPriceActivationHint {
            return "\(offer.memberPriceDisplayLabel) \(price). \(activationHint)."
        }
        return "\(offer.memberPriceDisplayLabel) \(price)"
    }
}
