import SwiftUI

struct CheaperOffersSheet: View {
    let pending: PendingOfferAddition
    let select: (GroceryOffer) -> Void
    let ignore: () -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var previewOffer: GroceryOffer?

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text("Kurv har fundet en lavere pris på samme vare. Medlemspriser er markeret særskilt og kan kræve butikkens medlemsprogram eller app.")
                        .foregroundStyle(.secondary)
                }

                Section("Billigere tilbud") {
                    ForEach(pending.cheaperOffers) { offer in
                        HStack(spacing: 12) {
                            Button { previewOffer = offer } label: {
                                OfferCropView(offer: offer)
                                    .frame(width: 72, height: 72)
                                    .background(Color(uiColor: .tertiarySystemFill))
                                    .clipShape(RoundedRectangle(cornerRadius: 10))
                                    .overlay(alignment: .bottomTrailing) {
                                        Image(systemName: "arrow.up.left.and.arrow.down.right")
                                            .font(.caption2.bold())
                                            .padding(4)
                                            .background(.ultraThinMaterial, in: Circle())
                                    }
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("Se tilbuddet fra \(offer.retailer)")

                            VStack(alignment: .leading, spacing: 5) {
                                HStack {
                                    Text(offer.retailer).font(.headline)
                                    Spacer()
                                    if let price = offer.price {
                                        Text(price, format: .currency(code: "DKK").precision(.fractionLength(price.rounded() == price ? 0 : 2)))
                                            .font(.headline.bold())
                                            .foregroundStyle(.red)
                                    }
                                }
                                if offer.memberPrice != nil {
                                    MemberPriceBadge(offer: offer, compact: true)
                                }
                                if let until = offer.validUntil {
                                    Text("Gyldig til \(until)")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                Button("Tilføj dette tilbud") {
                                    OfferAddActivity.shared.beginAdding()
                                    select(offer)
                                    dismiss()
                                }
                                .buttonStyle(.borderedProminent)
                            }
                        }
                        .padding(.vertical, 5)
                    }
                }

                Section {
                    Button("Ignorer og tilføj alligevel") {
                        OfferAddActivity.shared.beginAdding()
                        ignore()
                        dismiss()
                    }
                } footer: {
                    Text("Tilføjer det oprindelige tilbud fra \(pending.selectedOffer.retailer).")
                }
            }
            .navigationTitle("Billigere tilbud fundet")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Annuller") { dismiss() }
                }
            }
            .sheet(item: $previewOffer) { offer in
                OfferPreviewSheet(offer: offer)
                    .presentationDetents([.medium, .large])
                    .presentationDragIndicator(.visible)
            }
        }
    }
}
