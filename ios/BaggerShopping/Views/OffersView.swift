import SwiftUI

struct OffersView: View {
    @EnvironmentObject private var model: AppModel
    @State private var query = ""
    @State private var selectedRetailers: Set<String> = []
    @State private var retailers = ["MENY"]
    @State private var offers: [GroceryOffer] = []
    @State private var hasSearched = false
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var addedOfferID: String?
    @State private var pendingOffer: GroceryOffer?
    @FocusState private var searchIsFocused: Bool

    private let api = APIClient()

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

                        FlowLayout(spacing: 8) {
                                ForEach(retailers, id: \.self) { retailer in
                                    Button {
                                        if selectedRetailers.contains(retailer) { selectedRetailers.remove(retailer) }
                                        else { selectedRetailers.insert(retailer) }
                                    } label: {
                                        Text(retailer)
                                            .font(.subheadline.weight(selectedRetailers.contains(retailer) ? .semibold : .regular))
                                            .foregroundStyle(selectedRetailers.contains(retailer) ? Color.white : Color.primary)
                                            .padding(.horizontal, 13)
                                            .padding(.vertical, 8)
                                            .background(
                                                selectedRetailers.contains(retailer) ? Color.accentColor : Color(uiColor: .secondarySystemGroupedBackground),
                                                in: Capsule()
                                            )
                                    }
                                    .buttonStyle(.plain)
                                }
                        }
                        Text(selectedRetailers.isEmpty ? "Søger i alle butikker" : "Søger i \(selectedRetailers.count) valgte butikker")
                            .font(.caption).foregroundStyle(.secondary)
                        .onChange(of: selectedRetailers) {
                            offers = []
                            hasSearched = false
                            errorMessage = nil
                        }
                    }

                    if isLoading {
                        HStack { Spacer(); ProgressView("Søger …"); Spacer() }
                            .padding(.top, 32)
                    } else if let errorMessage {
                        ContentUnavailableView("Kunne ikke hente tilbud", systemImage: "wifi.exclamationmark", description: Text(errorMessage))
                    } else if hasSearched && offers.isEmpty {
                        ContentUnavailableView("Ingen tilbud fundet", systemImage: "magnifyingglass", description: Text("\"\(query)\" findes ikke i de valgte aktuelle aviser."))
                    } else {
                        ForEach(offers) { offer in
                            OfferCard(offer: offer, wasAdded: addedOfferID == offer.id) {
                                switch offer.choiceState {
                                case .direct(let variant):
                                    add(offer.shoppingItemName(variant: variant), from: offer)
                                case .variants, .unspecified:
                                    pendingOffer = offer
                                }
                            }
                        }
                    }
                }
                .padding()
            }
            .navigationTitle("Tilbud")
            .task { await loadRetailers() }
            .sheet(item: $pendingOffer) { offer in
                OfferVariantSheet(offer: offer) { name in
                    add(name, from: offer)
                    pendingOffer = nil
                }
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
            }
        }
    }

    @MainActor
    private func loadRetailers() async {
        guard let response = try? await api.fetchOfferPublications() else { return }
        let available = response.publications
            .filter { $0.status == "current" && $0.searchable }
            .map(\.retailer)
        retailers = available.reduce(into: []) { result, retailer in
            if !result.contains(retailer) { result.append(retailer) }
        }
        selectedRetailers.formIntersection(retailers)
    }

    private func add(_ itemName: String, from offer: GroceryOffer) {
        Task {
            if await model.addItem(
                itemName,
                retailer: offer.retailer,
                offerPrice: offer.price,
                offerValidFrom: offer.validFrom,
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
            offers = try await api.searchOffers(query: term, retailers: Array(selectedRetailers)).offers
                .filter { $0.variants.contains(where: \.matchesQuery) }
        } catch {
            offers = []
            errorMessage = error.localizedDescription
        }
    }
}

private struct FlowLayout: Layout {
    let spacing: CGFloat
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        layout(proposal: proposal, subviews: subviews).size
    }
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let result = layout(proposal: ProposedViewSize(width: bounds.width, height: proposal.height), subviews: subviews)
        for (index, point) in result.points.enumerated() {
            subviews[index].place(at: CGPoint(x: bounds.minX + point.x, y: bounds.minY + point.y), proposal: .unspecified)
        }
    }
    private func layout(proposal: ProposedViewSize, subviews: Subviews) -> (size: CGSize, points: [CGPoint]) {
        let width = proposal.width ?? 0
        var x: CGFloat = 0, y: CGFloat = 0, rowHeight: CGFloat = 0
        var points: [CGPoint] = []
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > 0 && x + size.width > width { x = 0; y += rowHeight + spacing; rowHeight = 0 }
            points.append(CGPoint(x: x, y: y)); x += size.width + spacing; rowHeight = max(rowHeight, size.height)
        }
        return (CGSize(width: width, height: y + rowHeight), points)
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
                    Label(wasAdded ? "Tilføjet" : buttonLabel, systemImage: wasAdded ? "checkmark.circle.fill" : "plus.circle.fill")
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

    private var buttonLabel: String {
        if case .direct = offer.choiceState { return "Tilføj til liste" }
        return "Vælg vare"
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
                        if url.absoluteString.contains("x1r=") || url.absoluteString.contains("business_images") {
                            image.resizable().scaledToFit()
                                .frame(maxWidth: .infinity, maxHeight: .infinity)
                        } else {
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
                        }
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
    let select: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var customName = ""

    private var names: [String] {
        guard case .variants(let available) = offer.choiceState else { return [] }
        let matching = offer.variants.filter(\.matchesQuery).map(\.name)
        return (matching.isEmpty ? available : matching)
            .sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }
    }

    var body: some View {
        NavigationStack {
            List {
                if let imageURL = offer.imageURL {
                    AsyncImage(url: imageURL) { image in image.resizable().scaledToFit() }
                        placeholder: { ProgressView() }
                        .frame(maxWidth: .infinity, minHeight: 100, maxHeight: 180)
                        .listRowInsets(EdgeInsets())
                }
                Section {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(offer.productName).font(.headline)
                        HStack(spacing: 6) {
                            Text(offer.retailer)
                            if let price = offer.price {
                                Text("·")
                                Text(price, format: .currency(code: "DKK").precision(.fractionLength(price.rounded() == price ? 0 : 2)))
                            }
                        }
                        .font(.subheadline).foregroundStyle(.secondary)
                    }
                }
                ForEach(names, id: \.self) { name in
                    Button { select(offer.shoppingItemName(variant: name)) } label: {
                        VStack(alignment: .leading, spacing: 5) {
                            Text(name).font(.headline).foregroundStyle(.primary)
                            HStack(spacing: 8) {
                                if let quantity = offer.quantity, let unit = offer.unit {
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
                Section(names.isEmpty ? "Varianten kan ikke identificeres sikkert" : "Et andet valg") {
                    Button("Tilføj uden bestemt variant") {
                        select(offer.shoppingItemName(variant: nil))
                    }
                    if !names.isEmpty {
                        TextField("Skriv den konkrete vare", text: $customName)
                        Button("Tilføj skrevet variant") {
                            select(offer.shoppingItemName(variant: customName))
                        }
                            .disabled(customName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                }
            }
            .navigationTitle("Vælg vare")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Annuller") { dismiss() } } }
        }
    }
}
