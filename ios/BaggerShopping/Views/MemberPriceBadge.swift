import SwiftUI

struct MemberPriceBadge: View {
    let offer: GroceryOffer
    var compact = false

    var body: some View {
        if let memberPrice = offer.memberPrice {
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
            .accessibilityElement(children: .combine)
            .accessibilityLabel("\(offer.memberPriceDisplayLabel) \(memberPrice.formatted(.currency(code: "DKK")))")
        }
    }
}
