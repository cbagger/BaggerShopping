import SwiftUI

struct OfferPreviewSheet: View {
    let offer: GroceryOffer
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 16) {
                OfferCropView(offer: offer)
                    .frame(maxWidth: .infinity)
                    .aspectRatio(1.15, contentMode: .fit)
                    .background(Color(uiColor: .secondarySystemBackground))
                    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))

                VStack(alignment: .leading, spacing: 8) {
                    Text(offer.productName).font(.title3.bold())
                    HStack {
                        Text(offer.retailer)
                        if let page = offer.pageNumber { Text("· Side \(page)") }
                        Spacer()
                        if let price = offer.price {
                            Text(price, format: .currency(code: "DKK").precision(.fractionLength(price.rounded() == price ? 0 : 2)))
                                .fontWeight(.bold)
                        }
                    }
                    .foregroundStyle(.secondary)

                    if offer.memberPrice != nil {
                        MemberPriceBadge(offer: offer)
                    }
                }

                Text("Tryk udenfor eller på Luk for at gå tilbage til tilbuddene.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer(minLength: 0)
            }
            .padding()
            .navigationTitle("Se tilbud")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Luk") { dismiss() }
                }
            }
        }
    }
}
