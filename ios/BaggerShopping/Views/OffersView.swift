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
    @FocusState private var searchIsFocused: Bool

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
                                .focused($searchIsFocused)
                                .onSubmit { Task { await search() } }
                            if !query.isEmpty {
                                Button {
                                    query = ""
                                    offers = []
                                    hasSearched = false
                                    errorMessage = nil
                                    searchIsFocused = true
                                } label: {
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
            if await model.addItem(
                itemName,
                retailer: offer.retailer,
                offerPrice: offer.price,
                offerValidUntil: offer.validUntil
            ) {
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
            HStack(alignment: .top, spacing: 12) {
                if offer.imageURL != nil {
                    OfferCropView(offer: offer)
                        .frame(width: 92, height: 92)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .allowsHitTesting(false)
                }

                VStack(alignment: .leading, spacing: 6) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(offer.productName)
                            .font(.headline)
                            .lineLimit(3)
                        Spacer(minLength: 8)
                        if let price = offer.price {
                            Text(price, format: .currency(code: "DKK").precision(.fractionLength(price.rounded() == price ? 0 : 2)))
                                .font(.headline.bold())
                                .foregroundStyle(.red)
                        }
                    }

                    HStack(spacing: 6) {
                        Text(offer.retailer).fontWeight(.semibold)
                        if let page = offer.pageNumber { Text("· Side \(page)") }
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)

                    if let from = offer.validFrom, let until = offer.validUntil {
                        Text("Gyldig \(from)–\(until)")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
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

struct OfferCropView: View {
    let offer: GroceryOffer

    private var crop: CGRect {
        let centerX = offer.hotspotX.map { $0 + (offer.hotspotWidth ?? 0) / 2 } ?? 0.5
        let centerY = offer.hotspotY.map { $0 + (offer.hotspotHeight ?? 0) / 2 } ?? 0.5
        let width = 0.48
        let height = 0.30
        return CGRect(
            x: min(max(0, centerX - width / 2), 1 - width),
            y: min(max(0, centerY - height / 2), 1 - height),
            width: width,
            height: height
        )
    }

    var body: some View {
        GeometryReader { proxy in
            if let url = offer.imageURL {
                AsyncImage(url: url) { phase in
                    if let image = phase.image {
                        let pageRatio = 694.0 / 1007.0
                        let scale = max(
                            proxy.size.width / (crop.width * pageRatio),
                            proxy.size.height / crop.height
                        )
                        let pageWidth = scale * pageRatio
                        let pageHeight = scale
                        let visibleWidth = crop.width * pageWidth
                        let visibleHeight = crop.height * pageHeight
                        image
                            .resizable()
                            .frame(width: pageWidth, height: pageHeight)
                            .offset(
                                x: -crop.minX * pageWidth + (proxy.size.width - visibleWidth) / 2,
                                y: -crop.minY * pageHeight + (proxy.size.height - visibleHeight) / 2
                            )
                    } else if phase.error != nil {
                        Color(uiColor: .tertiarySystemFill)
                            .overlay { Image(systemName: "photo").foregroundStyle(.secondary) }
                    } else {
                        ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                    }
                }
            }
        }
        .clipped()
        .allowsHitTesting(false)
        .accessibilityLabel("Udsnit fra tilbudsavisen for \(offer.productName)")
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
            List {
                ForEach(variants) { variant in
                    Button { select(variant) } label: {
                        VStack(alignment: .leading, spacing: 5) {
                            Text(variant.name).font(.headline).foregroundStyle(.primary)
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
            }
            .navigationTitle("Vælg vare")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Annuller") { dismiss() } } }
        }
    }
}
