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
        await OfferAddActivity.shared.beginChecking()
        guard selected.price != nil else {
            await OfferAddActivity.shared.beginAdding()
            return []
        }

        // Discovery may be broad enough to find a mixed campaign where the
        // selected product is only one variant. Identity verification remains
        // strict and local, so category neighbours never become price warnings.
        // Stop as soon as a search term has produced a real alternative; this
        // avoids a second network round-trip in the common case.
        var discovered: [String: GroceryOffer] = [:]
        for term in GroceryOffer.priceGuardSearchTerms(for: itemName) {
            guard let response = try? await api.searchOffers(query: term) else { continue }
            for offer in response.offers {
                discovered["\(offer.id)|\(offer.publicationID)"] = offer
            }

            let hasConcreteAlternative = response.offers.contains { candidate in
                candidate.id != selected.id
                    && candidate.publicationID != selected.publicationID
                    && candidate.matchingSelectedItemName(itemName) != nil
            }
            if hasConcreteAlternative { break }
        }

        let cheaper = cheaperOffers(from: Array(discovered.values), for: itemName, than: selected)
        if cheaper.isEmpty {
            await OfferAddActivity.shared.beginAdding()
        } else {
            await OfferAddActivity.shared.clear()
        }
        return cheaper
    }

    func cheaperOffers(
        from offers: [GroceryOffer],
        for itemName: String,
        than selected: GroceryOffer
    ) -> [GroceryOffer] {
        guard let selectedPrice = selected.price else { return [] }

        return sortedUnique(offers.filter { candidate in
            guard candidate.id != selected.id,
                  candidate.publicationID != selected.publicationID,
                  candidate.matchingSelectedItemName(itemName) != nil,
                  let candidatePrice = candidate.price else {
                return false
            }

            // Prefer comparable unit prices when both offers provide them. If
            // that data is missing, an exact concrete identity may still use
            // the ordinary campaign price comparison, preserving legacy flyers.
            if let selectedUnitPrice = selected.productIdentity?.unitPrice,
               let candidateUnitPrice = candidate.productIdentity?.unitPrice,
               selected.productIdentity?.unitPriceUnit == candidate.productIdentity?.unitPriceUnit {
                return candidateUnitPrice < selectedUnitPrice
            }

            if let selectedQuantity = selected.quantity,
               let candidateQuantity = candidate.quantity,
               let selectedUnit = selected.unit,
               let candidateUnit = candidate.unit {
                let sameUnit = selectedUnit.caseInsensitiveCompare(candidateUnit) == .orderedSame
                if sameUnit, abs(selectedQuantity - candidateQuantity) < 0.0001 {
                    return candidatePrice < selectedPrice
                }
                return false
            }

            return candidatePrice < selectedPrice
        })
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

        let variantCandidates = variants.flatMap { variant in
            [variant.name, shoppingItemName(variant: variant.name)]
        }
        if let match = variantCandidates.first(where: { Self.priceGuardKey($0) == wanted }) {
            return match
        }

        let campaignCandidates = [productName, conciseProductName]
        return campaignCandidates.first(where: { Self.priceGuardKey($0) == wanted })
    }

    static func priceGuardSearchTerms(for value: String) -> [String] {
        let original = normalizedPriceGuardText(value)
        guard !original.isEmpty else { return [] }

        let discoveryDescriptors: Set<String> = [
            "sodavand", "sodavande", "drik", "drikke", "vand",
            "mælk", "maelk", "smør", "smor", "brød", "brod",
            "kaffe", "yoghurt", "juice", "ost", "pålæg", "palaeg",
            "kød", "kod", "kylling", "svinekød", "svinekod"
        ]

        let broad = original
            .split(whereSeparator: \.isWhitespace)
            .map(String.init)
            .filter { !discoveryDescriptors.contains($0) }
            .joined(separator: " ")

        var terms = [original]
        if !broad.isEmpty, broad != original {
            terms.append(broad)
        }
        return terms
    }

    private static func priceGuardKey(_ value: String) -> String {
        let genericCategoryWords: Set<String> = ["sodavand", "sodavande"]
        return normalizedPriceGuardText(value)
            .split(whereSeparator: \.isWhitespace)
            .map(String.init)
            .filter { !genericCategoryWords.contains($0) }
            .joined(separator: " ")
    }

    private static func normalizedPriceGuardText(_ value: String) -> String {
        value
            .folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "da_DK"))
            .replacingOccurrences(of: #"[^a-z0-9]+"#, with: " ", options: .regularExpression)
            .split(whereSeparator: \.isWhitespace)
            .joined(separator: " ")
    }
}
