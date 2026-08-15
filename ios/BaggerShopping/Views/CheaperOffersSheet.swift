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
                    Text("\(pending.itemName) findes billigere samlet eller pr. enhed. Vælg det tilbud, der passer bedst til din indkøbstur.")
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
                                if let until = offer.validUntil {
                                    Text("Gyldig til \(until)")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                if let unitPrice = offer.productIdentity?.unitPrice,
                                   let unit = offer.productIdentity?.unitPriceUnit {
                                    Text("\(unitPrice.formatted(.currency(code: "DKK").precision(.fractionLength(2)))) pr. \(unit)")
                                        .font(.caption).foregroundStyle(.secondary)
                                } else if let minimum = offer.productIdentity?.unitPriceMin,
                                          let maximum = offer.productIdentity?.unitPriceMax,
                                          let unit = offer.productIdentity?.unitPriceUnit {
                                    Text("\(minimum.formatted(.currency(code: "DKK").precision(.fractionLength(2))))–\(maximum.formatted(.currency(code: "DKK").precision(.fractionLength(2)))) pr. \(unit)")
                                        .font(.caption).foregroundStyle(.secondary)
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
