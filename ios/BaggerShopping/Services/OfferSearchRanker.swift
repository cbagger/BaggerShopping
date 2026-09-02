import Foundation

enum OfferSearchRanker {
    private static let stopwords: Set<String> = [
        "af", "de", "den", "det", "eller", "fra", "i", "med", "og", "pak",
        "pk", "pr", "på", "stk", "til", "uden", "x"
    ]

    // Suffixes that normally keep the original grocery product as the core
    // meaning rather than turning it into another product. Example: smørbar is
    // still a useful result for "smør", while smøreost/smørrebrød are weaker.
    private static let preservingSuffixes: Set<String> = [
        "bar", "bart", "bare", "fri", "frit", "frie"
    ]

    static func rank(_ offers: [GroceryOffer], for query: String) -> [GroceryOffer] {
        offers.enumerated()
            .sorted { lhs, rhs in
                let leftCurrent = lhs.element.publicationStatus != "upcoming"
                let rightCurrent = rhs.element.publicationStatus != "upcoming"
                if leftCurrent != rightCurrent { return leftCurrent }

                let leftScore = confidence(for: lhs.element, query: query)
                let rightScore = confidence(for: rhs.element, query: query)
                if leftScore != rightScore { return leftScore > rightScore }

                // Relevance always wins. Price is useful only when two offers
                // are equally convincing matches.
                let leftPrice = lhs.element.price ?? .greatestFiniteMagnitude
                let rightPrice = rhs.element.price ?? .greatestFiniteMagnitude
                if leftPrice != rightPrice { return leftPrice < rightPrice }

                let retailerOrder = lhs.element.retailer.localizedCaseInsensitiveCompare(rhs.element.retailer)
                if retailerOrder != .orderedSame { return retailerOrder == .orderedAscending }

                let productOrder = lhs.element.productName.localizedCaseInsensitiveCompare(rhs.element.productName)
                if productOrder != .orderedSame { return productOrder == .orderedAscending }

                // Stable final tie-breaker preserves server order for otherwise
                // identical candidates.
                return lhs.offset < rhs.offset
            }
            .map(\.element)
    }

    static func confidence(for offer: GroceryOffer, query: String) -> Int {
        var candidates: [(text: String, variantMatch: Bool)] = [
            (offer.productName, false)
        ]

        if let brand = offer.brand, !brand.isEmpty {
            candidates.append(("\(brand) \(offer.productName)", false))
        }

        candidates.append(contentsOf: offer.variants.map { ($0.name, $0.matchesQuery) })

        return candidates.map { candidate in
            var score = textConfidence(query: query, candidate: candidate.text)
            if candidate.variantMatch, score > 0 {
                // The existing search engine already marked this structured
                // variant as matching. Use that signal as a small confirmation,
                // not as a substitute for lexical relevance.
                score += 8
            }
            return score
        }.max() ?? 0
    }

    static func textConfidence(query: String, candidate: String) -> Int {
        let queryTokens = tokens(query)
        let candidateTokens = tokens(candidate)
        guard !queryTokens.isEmpty, !candidateTokens.isEmpty else { return 0 }

        let perToken = queryTokens.map { queryToken in
            candidateTokens.map { tokenScore(query: queryToken, candidate: $0) }.max() ?? 0
        }

        let strongMatches = perToken.filter { $0 >= 60 }.count
        let coverage = Double(strongMatches) / Double(queryTokens.count)
        let average = perToken.reduce(0, +) / queryTokens.count
        let strongest = perToken.max() ?? 0

        var score: Int
        if strongMatches == queryTokens.count {
            // Full query coverage is the strongest general-purpose signal,
            // especially for brand + product searches such as "Lurpak smør".
            score = average + 80
        } else if strongMatches > 0 {
            score = strongest + Int((coverage * 55).rounded()) - Int(((1 - coverage) * 20).rounded())
        } else {
            // Keep weak substring matches available at the bottom of the list;
            // the existing search engine has already decided they are eligible.
            score = strongest
        }

        let normalizedQuery = normalized(query)
        let normalizedCandidate = normalized(candidate)
        if normalizedQuery == normalizedCandidate {
            score += 45
        } else if normalizedCandidate.hasPrefix(normalizedQuery + " ")
                    || normalizedCandidate.hasSuffix(" " + normalizedQuery) {
            score += 18
        }

        return score
    }

    private static func tokenScore(query: String, candidate: String) -> Int {
        if query == candidate { return 100 }
        if !inflectionForms(query).isDisjoint(with: inflectionForms(candidate)) { return 92 }

        guard query.count >= 4 else { return 0 }

        // Danish grocery compounds often put the core product at the end:
        // sødmælk, multifrugtjuice, sandwichrugbrød.
        if candidate.hasSuffix(query) { return 88 }

        if candidate.hasPrefix(query) {
            let suffix = String(candidate.dropFirst(query.count))
            if preservingSuffixes.contains(suffix) { return 86 }
            // The query is present, but the compound may describe a different
            // product: smøreost / smørrebrød for a "smør" search.
            return 38
        }

        if candidate.contains(query) { return 32 }

        // A more specific query may still reasonably match a generic product
        // token, but it ranks below direct coverage.
        if candidate.count >= 4, query.contains(candidate) { return 58 }

        return 0
    }

    private static func tokens(_ value: String) -> [String] {
        value
            .lowercased(with: Locale(identifier: "da_DK"))
            .split(whereSeparator: { !$0.isLetter && !$0.isNumber })
            .map(String.init)
            .filter { !stopwords.contains($0) }
    }

    private static func normalized(_ value: String) -> String {
        tokens(value).joined(separator: " ")
    }

    private static func inflectionForms(_ token: String) -> Set<String> {
        var forms: Set<String> = [token]
        guard token.count >= 6 else { return forms }

        for ending in ["erne", "ene", "eren", "er", "en", "et", "e", "s"] where token.hasSuffix(ending) {
            let stem = String(token.dropLast(ending.count))
            if stem.count >= 4 { forms.insert(stem) }
        }
        return forms
    }
}
