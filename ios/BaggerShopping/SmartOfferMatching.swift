import Foundation
import SwiftUI

struct SmartOfferMatchGroup: Codable {
    let itemName: String
    let offers: [GroceryOffer]

    enum CodingKeys: String, CodingKey {
        case offers
        case itemName = "item_name"
    }
}

struct SmartOfferMatchesResponse: Codable {
    let ok: Bool
    let itemCount: Int
    let offerCount: Int
    let matches: [SmartOfferMatchGroup]

    enum CodingKeys: String, CodingKey {
        case ok, matches
        case itemCount = "item_count"
        case offerCount = "offer_count"
    }
}

@MainActor
final class SmartOfferMatchService: ObservableObject {
    @Published private(set) var matchesByItem: [String: [GroceryOffer]] = [:]
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?

    private let api = APIClient()
    private let baseURL = URL(string: "https://shopping.chewbagger.dk")!

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

        do {
            guard let token = KeychainStore.loadToken(), !token.isEmpty else {
                throw APIClient.APIError.missingToken
            }
            let url = baseURL.appending(path: "/api/mobile/v1/offers/matches")
            var request = URLRequest(url: url)
            request.httpMethod = "POST"
            request.timeoutInterval = 25
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            request.setValue("application/json", forHTTPHeaderField: "Accept")
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: ["items": names])

            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                throw APIClient.APIError.invalidResponse
            }
            guard 200..<300 ~= http.statusCode else {
                throw APIClient.APIError.server(
                    http.statusCode,
                    String(data: data, encoding: .utf8) ?? "Ukendt fejl"
                )
            }

            let payload = try JSONDecoder().decode(SmartOfferMatchesResponse.self, from: data)
            matchesByItem = Dictionary(
                uniqueKeysWithValues: payload.matches.map { (key($0.itemName), $0.offers) }
            )
            errorMessage = nil
        } catch {
            // Smart matches are an enhancement; a temporary offer-service error
            // must never make the ordinary Samsung shopping list unusable.
            errorMessage = error.localizedDescription
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
    let focusItem: ShoppingItem?

    @State private var applyingOfferID: String?

    private struct MatchGroup: Identifiable {
        let item: ShoppingItem
        let offers: [GroceryOffer]
        var id: String { item.stableID }
    }

    private var groups: [MatchGroup] {
        let active = model.shoppingList?.items.filter { !$0.checked } ?? []
        let source: [ShoppingItem]
        if let focusItem {
            source = active.filter { $0.stableID == focusItem.stableID || $0.name == focusItem.name }
        } else {
            source = active
        }
        return source.compactMap { item in
            let offers = service.matches(for: item)
            return offers.isEmpty ? nil : MatchGroup(item: item, offers: offers)
        }
    }

    var body: some View {
        NavigationStack {
            Group {
                if groups.isEmpty {
                    ContentUnavailableView(
                        "Ingen tilbudsforslag",
                        systemImage: "tag.slash",
                        description: Text("Der er ingen aktuelle forslag til denne del af indkøbslisten.")
                    )
                } else {
                    List {
                        Section {
                            Label(
                                "Du vælger altid selv. Et forslag ændrer først varen, når du trykker Brug tilbud.",
                                systemImage: "hand.tap"
                            )
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        }

                        ForEach(groups) { group in
                            Section {
                                ForEach(group.offers) { offer in
                                    offerRow(offer, item: group.item)
                                }
                            } header: {
                                HStack {
                                    Text(group.item.name)
                                    if let quantity = group.item.displayQuantity {
                                        Text(quantity)
                                    }
                                }
                            }
                        }
                    }
                    .listStyle(.insetGrouped)
                }
            }
            .navigationTitle(focusItem == nil ? "Tilbud til din liste" : "Tilbudsforslag")
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
    private func offerRow(_ offer: GroceryOffer, item: ShoppingItem) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(alignment: .firstTextBaseline) {
                Label(offer.retailer, systemImage: "storefront")
                    .font(.headline)
                Spacer()
                if let price = offer.price {
                    Text(
                        price,
                        format: .currency(code: "DKK")
                            .precision(.fractionLength(price.rounded() == price ? 0 : 2))
                    )
                    .font(.headline)
                }
            }

            Text(offer.conciseProductName)
                .font(.subheadline.weight(.semibold))

            let variants = offer.variants.map(\.name)
            if !variants.isEmpty {
                Text(variants.prefix(3).joined(separator: " · "))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }

            if let validUntil = offer.validUntil {
                Label("Gælder til \(validUntil)", systemImage: "calendar")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Text("Kobles til den eksisterende vare “\(item.name)” — navn og antal bevares.")
                .font(.caption2)
                .foregroundStyle(.secondary)

            Button {
                Task {
                    applyingOfferID = offer.id
                    let success = await service.approve(offer, for: item, model: model)
                    applyingOfferID = nil
                    if success, focusItem != nil { dismiss() }
                }
            } label: {
                HStack {
                    if applyingOfferID == offer.id {
                        ProgressView().controlSize(.small)
                    }
                    Text("Brug \(offer.retailer)-tilbud")
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .disabled(applyingOfferID != nil)
        }
        .padding(.vertical, 4)
    }
}
