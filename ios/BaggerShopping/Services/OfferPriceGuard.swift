import Foundation

struct PendingOfferAddition: Identifiable {
    let id = UUID()
    let itemName: String
    let selectedOffer: GroceryOffer
    let cheaperOffers: [GroceryOffer]
}

struct OfferPriceGuard {
    private let api = APIClient()

    func cheaperOffers(for itemName: String, than selected: GroceryOffer) async -> [GroceryOffer] {
        guard let selectedPrice = selected.price else { return [] }
        guard let response = try? await api.searchOffers(query: itemName) else { return [] }
        let candidates = response.offers.filter {
            $0.id != selected.id
                && $0.publicationID != selected.publicationID
                && $0.price != nil
                && ["same_item", "compatible_variant"].contains($0.identityMatch?.level ?? "")
        }
        var verified: [GroceryOffer] = []
        for candidate in candidates {
            guard let comparison = try? await api.compareProducts(
                left: selected.productName,
                leftQuantity: selected.quantity,
                leftUnit: selected.unit,
                leftPrice: selected.price,
                right: candidate.productName,
                rightQuantity: candidate.quantity,
                rightUnit: candidate.unit,
                rightPrice: candidate.price
            ) else { continue }
            let directCheaper = comparison.level == "same_item"
                && comparison.directPriceComparison
                && (candidate.price ?? .greatestFiniteMagnitude) < selectedPrice
            let selectedLowestUnitPrice = comparison.left.unitPriceMin ?? comparison.left.unitPrice
            let candidateHighestUnitPrice = comparison.right.unitPriceMax ?? comparison.right.unitPrice
            let safelyCheaperPerUnit = ["same_item", "compatible_variant"].contains(comparison.level)
                && candidateHighestUnitPrice.map { candidateValue in
                    selectedLowestUnitPrice.map { candidateValue < $0 } ?? false
                } == true
            guard directCheaper || safelyCheaperPerUnit else { continue }
            verified.append(candidate)
        }
        return sortedUnique(verified)
    }

    func cheaperOffers(
        from offers: [GroceryOffer],
        for itemName: String,
        than selected: GroceryOffer
    ) -> [GroceryOffer] {
        guard let selectedPrice = selected.price else { return [] }

        return sortedUnique(offers
            .filter { candidate in
                candidate.id != selected.id
                    && candidate.publicationID != selected.publicationID
                    && candidate.price.map { $0 < selectedPrice } == true
                    && candidate.exactlyMatchesSelectedItem(itemName)
            }
        )
    }

    private func sortedUnique(_ offers: [GroceryOffer]) -> [GroceryOffer] {
        offers.reduce(into: [String: GroceryOffer]()) { result, offer in
                let key = "\(offer.id)|\(offer.publicationID)"
                result[key] = offer
            }
            .values
            .sorted {
                let leftPrice = $0.price ?? .greatestFiniteMagnitude
                let rightPrice = $1.price ?? .greatestFiniteMagnitude
                if leftPrice != rightPrice { return leftPrice < rightPrice }
                return $0.retailer.localizedCaseInsensitiveCompare($1.retailer) == .orderedAscending
            }
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
