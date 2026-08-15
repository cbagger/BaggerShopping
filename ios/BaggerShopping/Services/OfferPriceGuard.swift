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

        // Search is intentionally broad (e.g. "cola" may return Coca-Cola,
        // Pepsi Max and other sodavand). The cheaper-offer guard is not broad:
        // once the user selected a concrete variant, only campaigns that
        // actually contain that selected product are allowed through.
        let candidates = response.offers.compactMap { candidate -> (GroceryOffer, String)? in
            guard candidate.id != selected.id,
                  candidate.publicationID != selected.publicationID,
                  candidate.price != nil,
                  let matchedName = candidate.matchingSelectedItemName(itemName) else {
                return nil
            }
            return (candidate, matchedName)
        }

        var verified: [GroceryOffer] = []
        for (candidate, matchedName) in candidates {
            guard let comparison = try? await api.compareProducts(
                left: itemName,
                leftQuantity: selected.quantity,
                leftUnit: selected.unit,
                leftPrice: selected.price,
                right: matchedName,
                rightQuantity: candidate.quantity,
                rightUnit: candidate.unit,
                rightPrice: candidate.price
            ) else { continue }

            // A generic category-level match must never trigger "Billigere
            // tilbud fundet". The concrete selected identity has to survive the
            // server comparison too, after the local campaign/variant check.
            guard comparison.level == "same_item" else { continue }

            let directCheaper = comparison.directPriceComparison
                && (candidate.price ?? .greatestFiniteMagnitude) < selectedPrice
            let selectedLowestUnitPrice = comparison.left.unitPriceMin ?? comparison.left.unitPrice
            let candidateHighestUnitPrice = comparison.right.unitPriceMax ?? comparison.right.unitPrice
            let safelyCheaperPerUnit = candidateHighestUnitPrice.map { candidateValue in
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
        matchingSelectedItemName(selectedName) != nil
    }

    func matchingSelectedItemName(_ selectedName: String) -> String? {
        let wanted = Self.priceGuardKey(selectedName)
        guard !wanted.isEmpty else { return nil }

        // Prefer actual variants over a broad campaign heading. This is what
        // makes "Coca-Cola, Fanta, Squash eller Schweppes" a valid cheaper
        // Coca-Cola offer while rejecting a Pepsi/Faxe Kondi campaign.
        let variantCandidates = variants.flatMap { variant in
            [variant.name, shoppingItemName(variant: variant.name)]
        }
        if let match = variantCandidates.first(where: { Self.priceGuardKey($0) == wanted }) {
            return match
        }

        let campaignCandidates = [productName, conciseProductName]
        return campaignCandidates.first(where: { Self.priceGuardKey($0) == wanted })
    }

    private static func priceGuardKey(_ value: String) -> String {
        let genericCategoryWords: Set<String> = ["sodavand", "sodavande"]
        return value
            .folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "da_DK"))
            .replacingOccurrences(of: #"[^a-z0-9]+"#, with: " ", options: .regularExpression)
            .split(whereSeparator: \.isWhitespace)
            .map(String.init)
            .filter { !genericCategoryWords.contains($0) }
            .joined(separator: " ")
    }
}
