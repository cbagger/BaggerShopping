import Foundation

struct PendingOfferAddition: Identifiable {
    let id = UUID()
    let itemName: String
    let selectedOffer: GroceryOffer
    let cheaperOffers: [GroceryOffer]
}

struct OfferPriceGuard {
    private let api = APIClient()

    func cheaperOffers(
        for itemName: String,
        than selected: GroceryOffer,
        knownOffers: [GroceryOffer] = []
    ) async -> [GroceryOffer] {
        await OfferAddActivity.shared.beginChecking()
        guard selected.price != nil else {
            await OfferAddActivity.shared.beginAdding()
            return []
        }

        // A complete Tilbud search already contains the candidates the user is
        // looking at. Reuse it instead of performing another network round-trip.
        if !knownOffers.isEmpty {
            let local = cheaperOffers(from: knownOffers, for: itemName, than: selected)
            if !local.isEmpty {
                await OfferAddActivity.shared.clear()
                return local
            }
        }

        let terms = GroceryOffer.priceGuardSearchTerms(for: itemName)
        guard let discoveryTerm = terms.last else {
            await OfferAddActivity.shared.beginAdding()
            return []
        }

        var discovered: [String: GroceryOffer] = [:]
        for term in terms {
            if let cached = OfferSearchCache.load(query: term, retailers: []) {
                for offer in cached.offers {
                    discovered[offerKey(offer)] = offer
                }
            }
        }

        // One broad discovery query is enough. The previous implementation did
        // sequential queries and stopped after the first hit, which both added
        // latency and caused valid cheaper offers later in the result set to be
        // missed (for example Lurpak/Tuborg campaigns).
        if let response = try? await api.searchOffers(query: discoveryTerm) {
            OfferSearchCache.save(response.offers, query: discoveryTerm, retailers: [])
            for offer in response.offers {
                discovered[offerKey(offer)] = offer
            }
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
            guard candidate.id != selected.id || candidate.publicationID != selected.publicationID,
                  candidate.matchingSelectedItemName(itemName) != nil,
                  let candidatePrice = candidate.price else {
                return false
            }

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

    private func offerKey(_ offer: GroceryOffer) -> String {
        "\(offer.id)|\(offer.publicationID)"
    }

    private func sortedUnique(_ offers: [GroceryOffer]) -> [GroceryOffer] {
        offers.reduce(into: [String: GroceryOffer]()) { result, offer in
                result[offerKey(offer)] = offer
            }
            .values
            .sorted {
                let leftUnit = $0.productIdentity?.unitPrice ?? .greatestFiniteMagnitude
                let rightUnit = $1.productIdentity?.unitPrice ?? .greatestFiniteMagnitude
                if leftUnit != rightUnit { return leftUnit < rightUnit }
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
            [
                (variant.name, variant.identity),
                (shoppingItemName(variant: variant.name), variant.identity),
            ]
        }
        if let match = variantCandidates.first(where: {
            Self.priceGuardIdentityMatches(selectedName: selectedName, candidateName: $0.0, identity: $0.1)
        }) {
            return match.0
        }

        let campaignCandidates = [
            (productName, productIdentity),
            (conciseProductName, productIdentity),
        ]
        return campaignCandidates.first(where: {
            Self.priceGuardIdentityMatches(selectedName: selectedName, candidateName: $0.0, identity: $0.1)
        })?.0
    }

    static func priceGuardSearchTerms(for value: String) -> [String] {
        let original = normalizedPriceGuardText(value)
        guard !original.isEmpty else { return [] }

        let descriptors: Set<String> = [
            "sodavand", "sodavande", "drik", "drikke", "vand",
            "mælk", "maelk", "smør", "smor", "smørbar", "smorbar",
            "brød", "brod", "kaffe", "yoghurt", "juice", "ost",
            "pålæg", "palaeg", "kød", "kod", "kylling", "svinekød", "svinekod",
            "classic", "klassisk", "pilsner", "øl", "ol"
        ]

        let tokens = original.split(whereSeparator: \.isWhitespace).map(String.init)
        let withoutDescriptors = tokens.filter { !descriptors.contains($0) }.joined(separator: " ")
        let prefix = tokens.prefix(2).joined(separator: " ")
        let first = tokens.first ?? ""

        // Order from narrow to broad-but-useful so `last` is normally the
        // product/brand phrase rather than a single overly broad token.
        let broadCandidates = [first, prefix, withoutDescriptors]
            .filter { !$0.isEmpty && $0.count >= 4 && $0 != original }

        var terms = [original]
        for candidate in broadCandidates where !terms.contains(candidate) {
            terms.append(candidate)
        }
        return terms
    }

    private static func priceGuardIdentityMatches(
        selectedName: String,
        candidateName: String,
        identity: ProductIdentityAnalysis?
    ) -> Bool {
        let wanted = priceGuardKey(selectedName)
        let candidate = priceGuardKey(candidateName)
        guard !candidate.isEmpty else { return false }
        if candidate == wanted { return true }

        let wantedTokens = Set(normalizedPriceGuardText(selectedName).split(whereSeparator: \.isWhitespace).map(String.init))
        let candidateTokens = Set(normalizedPriceGuardText(candidateName).split(whereSeparator: \.isWhitespace).map(String.init))

        guard let identity,
              let brand = identity.brand.map(normalizedPriceGuardText),
              !brand.isEmpty else {
            return false
        }

        let brandTokens = Set(brand.split(whereSeparator: \.isWhitespace).map(String.init))
        guard brandTokens.isSubset(of: wantedTokens) else { return false }

        if let family = identity.canonicalFamily,
           selectedNameSupportsFamily(selectedName, family: family),
           (candidateTokens.isSubset(of: wantedTokens) || wantedTokens.isSubset(of: candidateTokens)) {
            return typesAndFlavoursAreCompatible(selectedName: selectedName, identity: identity)
        }

        return false
    }

    private static func selectedNameSupportsFamily(_ value: String, family: String) -> Bool {
        let normalized = normalizedPriceGuardText(value)
        let tokens = Set(normalized.split(whereSeparator: \.isWhitespace).map(String.init))
        let hints: [String: Set<String>] = [
            "cola": ["cola", "coca", "pepsi"],
            "soft_drink": ["sodavand", "fanta", "sprite", "squash", "schweppes"],
            "bread": ["brød", "brod", "toast"],
            "fermented_dairy": ["yoghurt", "yogurt", "skyr"],
            "butter_spread": ["smør", "smor", "smørbar", "smorbar", "lurpak", "kærgården", "kaergarden"],
            "household_paper": ["toiletpapir", "køkkenrulle", "kokkenrulle"],
            "milk": ["mælk", "maelk", "sødmælk", "sodmaelk", "letmælk", "letmaelk", "minimælk", "minimaelk", "skummetmælk", "skummetmaelk"],
        ]
        guard let familyHints = hints[family] else { return false }
        return !tokens.isDisjoint(with: familyHints)
    }

    private static func typesAndFlavoursAreCompatible(
        selectedName: String,
        identity: ProductIdentityAnalysis
    ) -> Bool {
        let selected = normalizedPriceGuardText(selectedName)
        let selectedTokens = Set(selected.split(whereSeparator: \.isWhitespace).map(String.init))
        let typeHints: [String: Set<String>] = [
            "zero": ["zero", "sukkerfri"],
            "light": ["light", "let"],
            "organic": ["økologisk", "okologisk", "øko", "oko"],
            "lactose_free": ["laktosefri"],
            "gluten_free": ["glutenfri"],
            "alcohol_free": ["alkoholfri"],
        ]
        for (type, hints) in typeHints {
            let selectedHasType = !selectedTokens.isDisjoint(with: hints)
            let candidateHasType = identity.types.contains(type)
            if selectedHasType != candidateHasType {
                return false
            }
        }
        if !identity.flavours.isEmpty {
            let normalizedFlavours = Set(identity.flavours.map(normalizedPriceGuardText))
            let selectedFlavours = selectedTokens.intersection(normalizedFlavours)
            if !selectedFlavours.isEmpty && selectedFlavours != normalizedFlavours {
                return false
            }
        }
        return true
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
            .replacingOccurrences(of: #"[^a-z0-9æøå]+"#, with: " ", options: .regularExpression)
            .split(whereSeparator: \.isWhitespace)
            .joined(separator: " ")
    }
}
