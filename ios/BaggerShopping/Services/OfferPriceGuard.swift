import Foundation

struct PendingOfferAddition: Identifiable {
    let id = UUID()
    let itemName: String
    let selectedOffer: GroceryOffer
    let cheaperOffer: GroceryOffer
}

struct OfferPriceGuard {
    private let api = APIClient()

    func cheaperOffer(for itemName: String, than selected: GroceryOffer) async -> GroceryOffer? {
        guard let selectedPrice = selected.price else { return nil }
        guard let response = try? await api.searchOffers(query: itemName) else { return nil }

        return response.offers
            .filter { candidate in
                candidate.id != selected.id
                    && candidate.publicationID != selected.publicationID
                    && candidate.price.map { $0 < selectedPrice } == true
                    && candidate.exactlyMatchesSelectedItem(itemName)
            }
            .min { ($0.price ?? .greatestFiniteMagnitude) < ($1.price ?? .greatestFiniteMagnitude) }
    }
}

extension GroceryOffer {
    func exactlyMatchesSelectedItem(_ selectedName: String) -> Bool {
        let wanted = Self.priceGuardKey(selectedName)
        guard !wanted.isEmpty else { return false }
        let candidates = [productName, conciseProductName]
            + variants.map(\.name)
            + variants.map { shoppingItemName(variant: $0.name) }
        return candidates.contains { Self.priceGuardKey($0) == wanted }
    }

    private static func priceGuardKey(_ value: String) -> String {
        value
            .folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "da_DK"))
            .replacingOccurrences(of: #"[^a-z0-9]+"#, with: " ", options: .regularExpression)
            .split(whereSeparator: \.isWhitespace)
            .joined(separator: " ")
    }
}
