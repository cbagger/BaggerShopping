import SwiftUI

struct OffersView: View {
    @EnvironmentObject private var model: AppModel
    @State private var query = ""
    @State private var selectedRetailer = "MENY"
    @State private var offers: [GroceryOffer] = []
    @State private var hasSearched = false
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var addedOfferID: String?
    @State private var pendingOffer: GroceryOffer?

    private let api = APIClient()
    private let retailers = ["MENY"]

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Find tilbud i aktuelle aviser")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)

                        HStack(spacing: 10) {
                            Image(systemName: "magnifyingglass")
                                .foregroundStyle(.secondary)
                            TextField("Søg fx juice eller oksekød", text: $query)
                                .textInputAutocapitalization(.never)
                                .submitLabel(.search)
                                .onSubmit { Task { await search() } }
                            if !query.isEmpty {
                                Button { query = ""; offers = []; hasSearched = false } label: {
                                    Image(systemName: "xmark.circle.fill")
                                        .foregroundStyle(.tertiary)
                                }
                            }
                        }
                        .padding(12)
                        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14))

                        Picker("Butik", selection: $selectedRetailer) {
                            ForEach(retailers, id: \.self) { Text($0).tag($0) }
                        }
                        .pickerStyle(.segmented)
                    }

                    if isLoading {
                        HStack { Spacer(); ProgressView("Søger …"); Spacer() }
                            .padding(.top, 32)
                    } else if let errorMessage {
                        ContentUnavailableView("Kunne ikke hente tilbud", systemImage: "wifi.exclamationmark", description: Text(errorMessage))
                    } else if hasSearched && offers.isEmpty {
                        ContentUnavailableView("Ingen tilbud fundet", systemImage: "magnifyingglass", description: Text("\"\(query)\" findes ikke i den aktuelle \(selectedRetailer)-avis."))
                    } else {
                        ForEach(offers) { offer in
                            OfferCard(offer: offer, wasAdded: addedOfferID == offer.id) {
                                let matching = offer.variants.filter(\.matchesQuery)
                                if offer.variants.count == 1, let variant = offer.variants.first {
                                    add(variant.name, from: offer)
                                } else if matching.count == 1, let variant = matching.first {
                                    add(variant.name, from: offer)
                                } else {
                                    pendingOffer = offer
                                }
                            }
                        }
                    }
                }
                .padding()
            }
            .navigationTitle("Tilbud")
            .sheet(item: $pendingOffer) { offer in
                OfferVariantSheet(offer: offer) { variant in
                    add(variant.name, from: offer)
                    pendingOffer = nil
                }
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
            }
        }
    }

    private func add(_ itemName: String, from offer: GroceryOffer) {
        Task {
            if await model.addItem(itemName, retailer: offer.retailer) {
                withAnimation { addedOfferID = offer.id }
            }
        }
    }

    @MainActor
    private func search() async {
        let term = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !term.isEmpty else { return }
        isLoading = true
        hasSearched = true
        errorMessage = nil
        addedOfferID = nil
        defer { isLoading = false }
        do {
            offers = try await api.searchOffers(query: term, retailer: selectedRetailer).offers
                .filter { $0.variants.contains(where: \.matchesQuery) }
        } catch {
            offers = []
            errorMessage = error.localizedDescription
        }
    }
}

private struct OfferCard: View {
    let offer: GroceryOffer
    let wasAdded: Bool
    let add: () -> Void

    var body: some View {
        let matchingCount = offer.variants.filter(\.matchesQuery).count
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(offer.productName)
                    .font(.headline)
                    .lineLimit(3)
                Spacer(minLength: 12)
                if let price = offer.price {
                    Text(price, format: .currency(code: "DKK").precision(.fractionLength(price.rounded() == price ? 0 : 2)))
                        .font(.title3.bold())
                        .foregroundStyle(.red)
                }
            }

            HStack(spacing: 8) {
                Text(offer.retailer).fontWeight(.semibold)
                if let quantity = offer.quantity, let unit = offer.unit {
                    Text("· \(quantity.formatted(.number.precision(.fractionLength(0...2)))) \(unit)")
                }
                if let page = offer.pageNumber { Text("· Side \(page)") }
            }
            .font(.caption)
            .foregroundStyle(.secondary)

            if let from = offer.validFrom, let until = offer.validUntil {
                Text("Gyldig \(from)–\(until)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }

            if offer.variants.count > 1 {
                Text(matchingCount < offer.variants.count
                     ? "\(matchingCount) matchende af \(offer.variants.count) varianter"
                     : "\(offer.variants.count) varianter – vælg den rigtige")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if offer.safeToAdd {
                Button(action: add) {
                    Label(wasAdded ? "Tilføjet" : (offer.variants.count == 1 || matchingCount == 1 ? "Tilføj til liste" : "Vælg vare"), systemImage: wasAdded ? "checkmark.circle.fill" : "plus.circle.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(wasAdded ? .green : .accentColor)
                .disabled(wasAdded)
            }
        }
        .padding(14)
        .background(Color(uiColor: .secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 16))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color(uiColor: .separator).opacity(0.35)))
    }
}

private struct OfferVariantSheet: View {
    let offer: GroceryOffer
    let select: (OfferVariant) -> Void
    @Environment(\.dismiss) private var dismiss

    private var variants: [OfferVariant] {
        offer.variants.filter(\.matchesQuery).sorted { lhs, rhs in
            lhs.name.localizedCaseInsensitiveCompare(rhs.name) == .orderedAscending
        }
    }

    var body: some View {
        NavigationStack {
            List(variants) { variant in
                Button { select(variant) } label: {
                    VStack(alignment: .leading, spacing: 5) {
                        HStack {
                            Text(variant.name).font(.headline).foregroundStyle(.primary)
                            Spacer()
                            if variant.matchesQuery {
                                Text("MATCH").font(.caption2.bold()).foregroundStyle(.green)
                            }
                        }
                        HStack(spacing: 8) {
                            if let quantity = variant.quantity, let unit = variant.unit {
                                Text("\(quantity.formatted(.number.precision(.fractionLength(0...2)))) \(unit)")
                            }
                            if let price = offer.price {
                                Text(price, format: .currency(code: "DKK").precision(.fractionLength(price.rounded() == price ? 0 : 2)))
                            }
                        }
                        .font(.subheadline).foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 4)
                }
            }
            .navigationTitle("Vælg vare")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Annuller") { dismiss() } } }
        }
    }
}
