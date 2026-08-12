import Foundation
import SwiftUI

@MainActor
final class SmartOfferMatchService: ObservableObject {
    @Published private(set) var matchesByItem: [String: [GroceryOffer]] = [:]
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?

    private let api = APIClient()

    func matches(for item: ShoppingItem) -> [GroceryOffer] {
        matchesByItem[key(item.name)] ?? []
    }

    func refresh(items: [ShoppingItem], model: AppModel) async {
        let eligible = items.filter {
            !$0.checked && model.offerRetailer(for: $0) == nil
        }
        let names = Array(
            Dictionary(grouping: eligible, by: { key($0.name) })
                .values
                .compactMap { $0.first?.name }
        )
        .sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }

        guard !names.isEmpty else {
            matchesByItem = [:]
            errorMessage = nil
            return
        }

        isLoading = true
        defer { isLoading = false }

        var refreshed: [String: [GroceryOffer]] = [:]
        var firstError: Error?

        // Deliberately use the exact same search path and the same
        // matchesQuery filter as OffersView. Smart matching must never grow a
        // second, subtly different product-search engine.
        for name in names {
            guard !Task.isCancelled else { return }
            do {
                let response = try await api.searchOffers(query: name)
                let offers = response.offers.filter {
                    $0.variants.contains(where: \.matchesQuery)
                }
                if !offers.isEmpty {
                    refreshed[key(name)] = offers
                }
            } catch {
                if firstError == nil { firstError = error }
            }
        }

        matchesByItem = refreshed
        if refreshed.isEmpty, let firstError {
            errorMessage = firstError.localizedDescription
        } else {
            // Suggestions are an enhancement. A single failed item search
            // must not turn the ordinary shopping list into an error state.
            errorMessage = nil
        }
    }

    func approve(_ offer: GroceryOffer, for item: ShoppingItem, model: AppModel) async -> Bool {
        let metadata = OfferMetadataDTO(
            itemName: item.name,
            retailer: offer.retailer,
            price: offer.price,
            validFrom: offer.validFrom,
            validUntil: offer.validUntil,
            offerID: offer.id,
            publicationID: offer.publicationID,
            matchedItemName: item.name
        )

        do {
            try await api.setOfferMetadata(metadata)
            await model.syncSharedOfferMetadata()
            matchesByItem.removeValue(forKey: key(item.name))
            errorMessage = nil
            return true
        } catch {
            errorMessage = "Tilbuddet kunne ikke tilknyttes endnu: \(error.localizedDescription)"
            return false
        }
    }

    private func key(_ value: String) -> String {
        value
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
            .lowercased()
    }
}

struct SmartOfferMatchesView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var service: SmartOfferMatchService
    let item: ShoppingItem

    @State private var applyingOfferID: String?

    private var offers: [GroceryOffer] {
        service.matches(for: item)
    }

    var body: some View {
        NavigationStack {
            Group {
                if offers.isEmpty {
                    ContentUnavailableView(
                        "Ingen tilbudsforslag",
                        systemImage: "tag.slash",
                        description: Text("Der er ikke længere aktuelle tilbud til \"\(item.name)\".")
                    )
                } else {
                    List {
                        Section {
                            Text("Vælg et tilbud til “\(item.name)”. Varen ændres først, når du trykker Brug tilbud — navn og antal bevares.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }

                        Section {
                            ForEach(offers) { offer in
                                offerRow(offer)
                            }
                        }
                    }
                    .listStyle(.insetGrouped)
                }
            }
            .navigationTitle("Tilbudsforslag")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Luk") { dismiss() }
                }
            }
            .alert(
                "Kunne ikke bruge tilbud",
                isPresented: Binding(
                    get: { service.errorMessage != nil },
                    set: { if !$0 { service.errorMessage = nil } }
                )
            ) {
                Button("OK", role: .cancel) { service.errorMessage = nil }
            } message: {
                Text(service.errorMessage ?? "")
            }
        }
    }

    @ViewBuilder
    private func offerRow(_ offer: GroceryOffer) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Label(offer.retailer, systemImage: "storefront")
                    .font(.subheadline.weight(.semibold))

                Spacer(minLength: 8)

                if let price = offer.price {
                    Text(
                        price,
                        format: .currency(code: "DKK")
                            .precision(.fractionLength(price.rounded() == price ? 0 : 2))
                    )
                    .font(.subheadline.bold())
                }
            }

            Text(offer.conciseProductName)
                .font(.subheadline)
                .foregroundStyle(.primary)

            let variants = offer.variants.filter(\.matchesQuery).map(\.name)
            if !variants.isEmpty {
                Text(variants.prefix(3).joined(separator: " · "))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }

            HStack(spacing: 8) {
                if let validUntil = offer.validUntil {
                    Label("Til \(validUntil)", systemImage: "calendar")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }

                Spacer(minLength: 8)

                Button {
                    Task {
                        applyingOfferID = offer.id
                        let success = await service.approve(offer, for: item, model: model)
                        applyingOfferID = nil
                        if success { dismiss() }
                    }
                } label: {
                    HStack(spacing: 5) {
                        if applyingOfferID == offer.id {
                            ProgressView().controlSize(.mini)
                        }
                        Text("Brug tilbud")
                    }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .disabled(applyingOfferID != nil)
            }
        }
        .padding(.vertical, 3)
    }
}
