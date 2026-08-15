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

        // Recall and precision are deliberately separated here. Search may be
        // broad enough to discover a mixed campaign where the selected product
        // is only one variant, while the identity checks below stay strict.
        // This avoids both failure modes:
        // 1) missing a cheaper Coca-Cola campaign because the search query also
        //    contained the generic word "sodavand"
        // 2) suggesting Pepsi/Faxe Kondi just because they share the category.
        var discovered: [String: GroceryOffer] = [:]
        for term in GroceryOffer.priceGuardSearchTerms(for: itemName) {
            guard let response = try? await api.searchOffers(query: term) else { continue }
            for offer in response.offers {
                discovered["\(offer.id)|\(offer.publicationID)"] = offer
            }
        }

        let candidates = discovered.values.compactMap { candidate -> (GroceryOffer, String)? in
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

            // A category-level neighbour is never enough for a price warning.
            // The selected concrete identity must survive server verification.
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

        // Prefer actual variants over a broad campaign heading. A mixed
        // campaign is valid only when the concrete selected item is present.
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

        // These words are useful category descriptors for display, but can make
        // catalogue search too narrow when the cheaper campaign names only the
        // brand/variant. They are removed for discovery only; strict identity
        // matching still happens afterwards.
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
