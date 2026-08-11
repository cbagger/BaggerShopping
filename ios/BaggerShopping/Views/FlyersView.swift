import SwiftUI

struct FlyersView: View {
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
                    List(publications) { publication in
                        Button { selectedPublication = publication } label: {
                            VStack(alignment: .leading, spacing: 7) {
                                HStack {
                                    Text(publication.retailer).font(.title3.bold())
                                    Spacer()
                                    Text(publication.status == "upcoming" ? "KOMMER SNART" : "AKTUEL")
                                        .font(.caption2.bold())
                                        .foregroundStyle(publication.status == "upcoming" ? .orange : .green)
                                }
                                Text(publication.title).font(.headline)
                                if let from = publication.validFrom, let until = publication.validUntil {
                                    Text("Gyldig \(from)–\(until)").foregroundStyle(.secondary)
                                }
                                Text("\(publication.pageCount) sider · Åbn avis")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            .padding(.vertical, 6)
                        }
                        .buttonStyle(.plain)
                    }
                    .refreshable { await load() }
                }
            }
            .navigationTitle("Aviser")
            .task { await load() }
            .fullScreenCover(item: $selectedPublication) { NativeFlyerReader(publication: $0) }
        }
    }

    @MainActor private func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do { publications = try await api.fetchOfferPublications().publications }
        catch { errorMessage = error.localizedDescription }
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
                OfferPicker(offer: offer) { name in add(name); pendingOffer = nil }
                    .presentationDetents([.medium, .large])
                    .presentationDragIndicator(.visible)
            }
            .alert("Tilføjet til indkøbslisten", isPresented: Binding(
                get: { addedName != nil }, set: { if !$0 { addedName = nil } }
            )) { Button("OK") { addedName = nil } } message: { Text(addedName ?? "") }
            .alert("Kunne ikke hente avisens varer", isPresented: Binding(
                get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } }
            )) { Button("OK") { errorMessage = nil } } message: { Text(errorMessage ?? "") }
        }
        .preferredColorScheme(.light)
    }

    private func choose(_ offer: GroceryOffer) {
        if offer.variants.count == 1, let variant = offer.variants.first { add(variant.name) }
        else { pendingOffer = offer }
    }

    private func add(_ name: String) {
        Task { if await model.addItem(name, retailer: publication.retailer) { addedName = name } }
    }

    @MainActor private func loadOffers() async {
        do { offers = try await api.fetchCurrentOffers().offers }
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

    var body: some View {
        NavigationStack {
            List(offer.variants) { variant in
                Button { select(variant.name) } label: {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(variant.name).font(.headline)
                        if let quantity = variant.quantity, let unit = variant.unit {
                            Text("\(quantity.formatted(.number.precision(.fractionLength(0...2)))) \(unit)")
                                .font(.subheadline).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .navigationTitle("Vælg vare")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Annuller") { dismiss() } } }
        }
    }
}
