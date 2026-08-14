import SwiftUI

struct FlyersView: View {
    @EnvironmentObject private var navigation: AppNavigation
    @State private var publications: [OfferPublication] = []
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var selectedPublication: OfferPublication?
    private let api = APIClient()

    var body: some View {
        NavigationStack {
            Group {
                if isLoading && publications.isEmpty {
                    ProgressView("Henter aktuelle aviser …")
                } else if let errorMessage, publications.isEmpty {
                    ContentUnavailableView("Kunne ikke hente aviser", systemImage: "wifi.exclamationmark", description: Text(errorMessage))
                } else {
                    ScrollView {
                        LazyVGrid(
                            columns: [GridItem(.flexible(), spacing: 14), GridItem(.flexible(), spacing: 14)],
                            alignment: .leading,
                            spacing: 24
                        ) {
                            ForEach(Array(publications.enumerated()), id: \.element.id) { _, publication in
                                Button { selectedPublication = publication } label: {
                                    FlyerCoverCard(publication: publication)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .padding(.horizontal, 18)
                        .padding(.vertical, 12)
                        // Keep the final row fully above the persistent tab bar.
                        .padding(.bottom, 88)
                    }
                    .refreshable { await load() }
                }
            }
            .navigationTitle("Aviser")
            .task { await load() }
            .onChange(of: navigation.flyerRoute?.id) { _, _ in
                openRequestedFlyerIfAvailable()
            }
            .fullScreenCover(item: $selectedPublication) { NativeFlyerReader(publication: $0) }
        }
    }

    @MainActor private func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let fetched = try await api.fetchOfferPublications().publications
            publications = fetched
            openRequestedFlyerIfAvailable()
        }
        catch { errorMessage = error.localizedDescription }
    }

    @MainActor private func openRequestedFlyerIfAvailable() {
        guard let route = navigation.flyerRoute else { return }
        if let exact = publications.first(where: { $0.id == route.publicationID }) {
            selectedPublication = exact
            return
        }
        if let retailer = route.retailer,
           let latest = publications.first(where: {
               $0.retailer.caseInsensitiveCompare(retailer) == .orderedSame && $0.status != "expired"
           }) {
            selectedPublication = latest
        }
    }
}

private struct FlyerCoverCard: View {
    let publication: OfferPublication

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            ZStack {
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(Color(uiColor: .secondarySystemBackground))

                if let coverURL = publication.pageImageURLs.first {
                    AsyncImage(url: coverURL) { phase in
                        if let image = phase.image {
                            image
                                .resizable()
                                .scaledToFill()
                        } else if phase.error != nil {
                            Image(systemName: "newspaper")
                                .font(.largeTitle)
                                .foregroundStyle(.secondary)
                        } else {
                            ProgressView()
                        }
                    }
                } else {
                    Image(systemName: "newspaper")
                        .font(.largeTitle)
                        .foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity)
            .frame(height: 240)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .clipped()
            .overlay {
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(.black.opacity(0.08), lineWidth: 1)
            }
            .shadow(color: .black.opacity(0.12), radius: 8, y: 3)

            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(publication.retailer)
                    .font(.headline)
                    .foregroundStyle(.primary)
                    .lineLimit(1)

                Spacer(minLength: 4)

                Text(weekLabel)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }

            Label(expiryLabel, systemImage: "clock")
                .font(.subheadline)
                .foregroundStyle(expiryColor)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityHint("Åbner tilbudsavisen")
    }

    private var weekLabel: String {
        let lowercased = publication.title.lowercased()
        guard let range = lowercased.range(of: "uge") else { return publication.title }
        let suffix = lowercased[range.upperBound...]
            .drop(while: { !$0.isNumber })
        let digits = suffix.prefix(while: { $0.isNumber })
        guard digits.count >= 2 else { return publication.title }
        return "Uge \(digits.prefix(2))"
    }

    private var expiryLabel: String {
        if publication.status == "upcoming" { return "Kommer snart" }
        guard let expiryDate else { return "Aktuel avis" }

        let days = Calendar.current.dateComponents(
            [.day],
            from: Calendar.current.startOfDay(for: Date()),
            to: Calendar.current.startOfDay(for: expiryDate)
        ).day ?? 0

        switch days {
        case ..<0: return "Udløbet"
        case 0: return "Slutter i dag"
        case 1: return "Slutter i morgen"
        default: return "\(days) dage tilbage"
        }
    }

    private var expiryColor: Color {
        guard let expiryDate else { return .secondary }
        let days = Calendar.current.dateComponents(
            [.day],
            from: Calendar.current.startOfDay(for: Date()),
            to: Calendar.current.startOfDay(for: expiryDate)
        ).day ?? 0
        return days <= 1 ? .orange : .secondary
    }

    private var expiryDate: Date? {
        guard let value = publication.validUntil else { return nil }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "da_DK")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.dateFormat = "dd.MM.yyyy"
        return formatter.date(from: value)
    }
}

private struct NativeFlyerReader: View {
    let publication: OfferPublication
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var model: AppModel
    @State private var offers: [GroceryOffer] = []
    @State private var page = 1
    @State private var pendingOffer: GroceryOffer?
    @State private var addedName: String?
    @State private var errorMessage: String?
    @State private var pendingCheaperAddition: PendingOfferAddition?
    private let api = APIClient()

    var body: some View {
        NavigationStack {
            Group {
                if publication.pageImageURLs.isEmpty {
                    ContentUnavailableView("Avisen mangler sidebilleder", systemImage: "doc.text.magnifyingglass")
                } else {
                    TabView(selection: $page) {
                        ForEach(Array(publication.pageImageURLs.enumerated()), id: \.offset) { index, url in
                            FlyerPage(url: url, offers: offers.filter { $0.pageNumber == index + 1 }) { offer in
                                choose(offer)
                            }
                            .tag(index + 1)
                        }
                    }
                    .tabViewStyle(.page(indexDisplayMode: .never))
                    .background(Color.black)
                    .overlay(alignment: .topTrailing) {
                        Text("\(page) / \(publication.pageImageURLs.count)")
                            .font(.caption.bold())
                            .padding(.horizontal, 10).padding(.vertical, 6)
                            .background(.ultraThinMaterial, in: Capsule())
                            .padding(12)
                    }
                }
            }
            .navigationTitle(publication.retailer)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarLeading) { Button("Luk", systemImage: "xmark") { dismiss() } } }
            .task { await loadOffers() }
            .sheet(item: $pendingOffer) { offer in
                OfferPicker(offer: offer) { name in add(name, from: offer); pendingOffer = nil }
                    .presentationDetents([.medium, .large])
                    .presentationDragIndicator(.visible)
            }
            .alert("Tilføjet til indkøbslisten", isPresented: Binding(
                get: { addedName != nil }, set: { if !$0 { addedName = nil } }
            )) { Button("OK") { addedName = nil } } message: { Text(addedName ?? "") }
            .alert("Kunne ikke hente avisens varer", isPresented: Binding(
                get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } }
            )) { Button("OK") { errorMessage = nil } } message: { Text(errorMessage ?? "") }
            .sheet(item: $pendingCheaperAddition) { pending in
                CheaperOffersSheet(pending: pending) { offer in
                    addWithoutPriceCheck(pending.itemName, from: offer)
                    pendingCheaperAddition = nil
                } ignore: {
                    addWithoutPriceCheck(pending.itemName, from: pending.selectedOffer)
                    pendingCheaperAddition = nil
                }
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
            }
        }
        .preferredColorScheme(.light)
    }

    private func choose(_ offer: GroceryOffer) {
        switch offer.choiceState {
        case .direct(let variant): add(offer.shoppingItemName(variant: variant), from: offer)
        case .variants, .unspecified: pendingOffer = offer
        }
    }

    private func add(_ name: String, from selectedOffer: GroceryOffer? = nil) {
        let offer = selectedOffer ?? pendingOffer ?? offers.first { $0.variants.contains(where: { $0.name == name }) }
        guard let offer else { return }
        Task {
            let cheaper = await OfferPriceGuard().cheaperOffers(for: name, than: offer)
            if !cheaper.isEmpty {
                pendingCheaperAddition = PendingOfferAddition(
                    itemName: name,
                    selectedOffer: offer,
                    cheaperOffers: cheaper
                )
                return
            }
            addWithoutPriceCheck(name, from: offer)
        }
    }

    private func addWithoutPriceCheck(_ name: String, from offer: GroceryOffer) {
        Task {
            if await model.addItem(
                name,
                retailer: offer.retailer,
                offerPrice: offer.price,
                offerValidFrom: offer.validFrom,
                offerValidUntil: offer.validUntil,
                offerID: offer.id,
                publicationID: offer.publicationID,
                matchedItemName: name,
                offerSnapshot: offer
            ) { addedName = name }
        }
    }

    @MainActor private func loadOffers() async {
        do { offers = try await api.fetchOffers(publicationID: publication.id).offers }
        catch { errorMessage = error.localizedDescription }
    }
}

private struct FlyerPage: View {
    let url: URL
    let offers: [GroceryOffer]
    let select: (GroceryOffer) -> Void

    var body: some View {
        GeometryReader { proxy in
            AsyncImage(url: url) { phase in
                if let image = phase.image {
                    image.resizable().scaledToFit()
                        .overlay { hotspots(in: proxy.size) }
                } else if phase.error != nil {
                    ContentUnavailableView("Siden kunne ikke hentes", systemImage: "photo.badge.exclamationmark")
                } else { ProgressView() }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    @ViewBuilder private func hotspots(in container: CGSize) -> some View {
        // MENY's native pages are 694 × 1007. scaledToFit may letterbox,
        // therefore normalized coordinates are placed inside the fitted rect.
        let ratio = 694.0 / 1007.0
        let width = min(container.width, container.height * ratio)
        let height = width / ratio
        let offsetX = (container.width - width) / 2
        let offsetY = (container.height - height) / 2
        ZStack(alignment: .topLeading) {
            ForEach(offers) { offer in
                if let x = offer.hotspotX, let y = offer.hotspotY,
                   let w = offer.hotspotWidth, let h = offer.hotspotHeight {
                    Button { select(offer) } label: {
                        Image(systemName: "plus")
                            .font(.caption.bold())
                            .foregroundStyle(.white)
                            .frame(width: 30, height: 30)
                            .background(.black.opacity(0.82), in: Circle())
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                            .contentShape(Rectangle())
                    }
                        .frame(width: max(44, width * w), height: max(44, height * h))
                        .offset(x: offsetX + width * x, y: offsetY + height * y)
                        .accessibilityLabel("Tilføj \(offer.productName)")
                }
            }
        }
        .frame(width: container.width, height: container.height, alignment: .topLeading)
    }
}

private struct OfferPicker: View {
    let offer: GroceryOffer
    let select: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var customName = ""

    private var names: [String] {
        if case .variants(let names) = offer.choiceState { return names }
        return []
    }

    var body: some View {
        NavigationStack {
            List {
                if let imageURL = offer.imageURL {
                    AsyncImage(url: imageURL) { image in
                        image.resizable().scaledToFit()
                    } placeholder: { ProgressView() }
                    .frame(maxWidth: .infinity, minHeight: 100, maxHeight: 180)
                    .listRowInsets(EdgeInsets())
                }

                Section {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(offer.conciseProductName).font(.headline)
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
                        VStack(alignment: .leading, spacing: 4) {
                            Text(name).font(.headline)
                            if let quantity = offer.quantity, let unit = offer.unit {
                                Text("\(quantity.formatted(.number.precision(.fractionLength(0...2)))) \(unit)")
                                    .font(.subheadline).foregroundStyle(.secondary)
                            }
                        }
                    }
                }

                Section(names.isEmpty ? "Varianten kan ikke identificeres sikkert" : "Et andet valg") {
                    Button("Tilføj uden bestemt variant") {
                        select(offer.shoppingItemName(variant: nil))
                    }
                    TextField("Skriv den konkrete vare", text: $customName)
                    Button("Tilføj skrevet variant") {
                        select(offer.shoppingItemName(customVariant: customName))
                    }
                        .disabled(customName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            .navigationTitle("Vælg vare")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Annuller") { dismiss() } } }
        }
    }
}
