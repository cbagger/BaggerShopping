import Foundation

enum OfferChoiceState: Equatable {
    case direct(String)
    case variants([String])
    case unspecified
}
extension GroceryOffer {
    var conciseProductName: String {
        let value = productName.trimmingCharacters(in: CharacterSet(charactersIn: "* "))
        let folded = value.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: Locale(identifier: "da_DK"))
        if folded.contains("coca-cola") && (folded.contains("fanta") || folded.contains("squash")) {
            return "Sodavand og drikkevarer"
        }
        if folded.contains("koldskal") && folded.contains("kefir") { return "Koldskål og kefir" }
        if folded.contains("madvaerket") && folded.contains("kylling") { return "MADVÆRKET kylling" }
        if folded.contains("buko") && (folded.contains("smelteost") || folded.contains("flodeost")) { return "Buko ost" }
        if folded.contains("bki") && folded.contains("kaffe") { return "BKI kaffe" }
        return value
    }

    var addAvailabilityTitle: String? {
        guard !safeToAdd else { return nil }
        return publicationStatus == "upcoming"
            ? "Tilbuddet er ikke aktivt endnu"
            : "Tilbuddet kan ikke tilføjes sikkert"
    }

    var addAvailabilityMessage: String? {
        guard !safeToAdd else { return nil }
        if publicationStatus == "upcoming" {
            if let validFrom, !validFrom.isEmpty {
                return "Tilbuddet gælder først fra \(validFrom). Du kan se det i avisen nu, men Kurv tilføjer det ikke før startdatoen."
            }
            return "Tilbuddet starter senere. Du kan se det i avisen nu, men Kurv tilføjer det ikke før det er aktivt."
        }
        return "Kurv mangler sikre oplysninger til at tilføje dette tilbud automatisk."
    }

    var choiceState: OfferChoiceState {
        // Flyer hotspots may stay visible for upcoming campaigns. Never let a
        // visible marker become an add action while the server says fail-closed.
        guard safeToAdd else { return .unspecified }

        let names = resolvedVariantNames
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .reduce(into: [String]()) { result, name in
                if !result.contains(where: { $0.caseInsensitiveCompare(name) == .orderedSame }) {
                    result.append(name)
                }
            }

        if names.count > 1 { return .variants(names) }
        if names.count == 1,
           !hasUnresolvedVariantLanguage,
           variantConfidence >= 0.90 {
            return .direct(names[0])
        }
        return .unspecified
    }

    var resolvedVariantNames: [String] {
        let structured = variants.map(\.name).filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        if !structured.isEmpty {
            if qualitySignals.contains("luna-multiple-products") && structured.count <= 1 {
                return []
            }
            if structured.count == 1 && variantConfidence < 0.90 {
                let only = structured[0].folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "da_DK"))
                let product = productName.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "da_DK"))
                let concise = conciseProductName.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "da_DK"))
                if only == product || only == concise {
                    return []
                }
            }
            return structured
        }

        let source = productName
            .replacingOccurrences(of: "\n", with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard source.range(of: #"\s+(?:eller|/)\s+"#, options: [.regularExpression, .caseInsensitive]) != nil else {
            return []
        }

        let pieces = source.components(separatedBy: VariantChoiceSeparator.regex)
            .map { $0.trimmingCharacters(in: CharacterSet.whitespacesAndNewlines.union(CharacterSet(charactersIn: ",.;"))) }
            .filter { candidate in
                let words = candidate.split(whereSeparator: \.isWhitespace)
                return !candidate.isEmpty && words.count <= 9
                    && candidate.range(of: #"\d+(?:[.,]\d+)?\s*(?:kr|,-)"#, options: [.regularExpression, .caseInsensitive]) == nil
            }

        guard (2...5).contains(pieces.count) else { return [] }
        return pieces
    }

    var hasUnresolvedVariantLanguage: Bool {
        if qualitySignals.contains("luna-multiple-products") { return true }
        let value = "\(productName) \(rawText)".lowercased()
        return [
            "frit valg",
            "flere varianter",
            "forskellige varianter",
            "flere slags",
            "smagsvarianter",
            "udvalgte varianter",
            "assorteret",
            "assorterede",
            "vælg mellem",
            "vælg imellem",
            " eller "
        ].contains(where: value.contains)
    }

    func samsungSafeShoppingItemName(_ rawName: String) -> String {
        var value = rawName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return value }

        if let structuredVariant = variants.first(where: { variant in
            let variantName = variant.name.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !variantName.isEmpty else { return false }
            return value.caseInsensitiveCompare(variantName) == .orderedSame
                || value.range(of: variantName, options: [.caseInsensitive, .diacriticInsensitive]) != nil
        }),
           let quantity = structuredVariant.quantity,
           quantity > 1,
           let rawUnit = structuredVariant.unit?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased(),
           ["stk", "styk", "stykker", "pk", "pakke", "pakker"].contains(rawUnit),
           value.range(
               of: #"\b\d+(?:[.,]\d+)?\s*(?:stk\.?|styk(?:ker)?|pk\.?|pakke(?:r)?|-pak)\s*$"#,
               options: [.regularExpression, .caseInsensitive]
           ) == nil {
            let count = quantity.rounded() == quantity
                ? String(Int(quantity))
                : quantity.formatted(.number.precision(.fractionLength(0...1)))
            value += " \(count)-pak"
        }

        return value.replacingOccurrences(
            of: #"\s+(\d+)\s*(?:stk\.?|styk(?:ker)?)\s*$"#,
            with: " $1-pak",
            options: [.regularExpression, .caseInsensitive]
        )
    }

    func shoppingItemName(variant: String?) -> String {
        let base = conciseProductName
        guard let variant = variant?.trimmingCharacters(in: .whitespacesAndNewlines),
              !variant.isEmpty,
              variant.caseInsensitiveCompare(base) != .orderedSame else {
            return samsungSafeShoppingItemName(base)
        }

        if let composed = composedSharedAlternative(base: base, variant: variant) {
            return samsungSafeShoppingItemName(composed)
        }
        if let composed = composedPropertyVariant(base: base, variant: variant) {
            return samsungSafeShoppingItemName(composed)
        }

        if variant.range(of: base, options: [.caseInsensitive, .diacriticInsensitive]) != nil {
            return samsungSafeShoppingItemName(variant)
        }
        let words = variant.split(whereSeparator: \.isWhitespace)
        let leadingJoinWords = ["i", "med", "uden", "af", "til"]
        let beginsAsSuffix = words.first.map { leadingJoinWords.contains($0.lowercased()) } ?? false
        let firstWord = words.first.map(String.init) ?? ""
        let beginsWithBrandLikeName = firstWord.first?.isUppercase == true
            && !firstWord.allSatisfy(\.isUppercase)
        let beginsWithExplicitBrand = firstWord.contains("!")
            || firstWord.allSatisfy { $0.isUppercase || $0.isNumber || $0 == "’" || $0 == "'" }
        if !beginsAsSuffix && !isGenericVariantSuffix(variant)
            && (beginsWithBrandLikeName || beginsWithExplicitBrand) {
            return samsungSafeShoppingItemName(variant)
        }
        if isGenericVariantSuffix(variant), variant.lowercased().hasPrefix(firstWordOf(base).lowercased() + " "),
           let productNoun = base.split(whereSeparator: \.isWhitespace).last.map(String.init),
           variant.range(of: productNoun, options: [.caseInsensitive, .diacriticInsensitive]) == nil {
            return samsungSafeShoppingItemName("\(variant) \(productNoun)")
        }
        return samsungSafeShoppingItemName("\(variantBaseName(from: base)) – \(cleanedVariantSuffix(variant))")
    }

    private func composedSharedAlternative(base: String, variant: String) -> String? {
        let foldedBase = base.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "da_DK"))
        let foldedVariant = variant.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "da_DK"))
        if let relative = composedLeadingHyphenAlternative(base: base, variant: variant) {
            return relative
        }
        guard foldedBase.contains("kyllingeover-") && foldedBase.contains("underlar"),
              foldedVariant == "underlar" else { return nil }
        let prefix = base.components(separatedBy: "kyllingeover-").first?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return [prefix, "kyllingeunderlår"].filter { !$0.isEmpty }.joined(separator: " ")
    }

    private func composedLeadingHyphenAlternative(base: String, variant: String) -> String? {
        let cleaned = variant.trimmingCharacters(in: .whitespacesAndNewlines)
        guard cleaned.hasPrefix("-") else { return nil }
        let suffix = cleaned.drop(while: { $0 == "-" || $0.isWhitespace })
        guard !suffix.isEmpty else { return nil }
        let folded = base.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "da_DK"))
        let roots = ["kalkun", "kylling", "svine", "okse", "lamme", "kalve"]
        guard let root = roots.first(where: { folded.hasPrefix($0) }) else { return nil }
        let originalRoot = String(base.prefix(root.count))
        return originalRoot + suffix
    }

    private func composedPropertyVariant(base: String, variant: String) -> String? {
        let folded = variant.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "da_DK"))
        guard folded.range(of: #"^(?:mellem|mild|ekstra|vellagret|lagret|stærk)"#, options: .regularExpression) != nil,
              let noun = base.split(whereSeparator: \.isWhitespace).last.map(String.init),
              variant.range(of: noun, options: [.caseInsensitive, .diacriticInsensitive]) == nil else { return nil }
        let identity = base.split(whereSeparator: \.isWhitespace).dropLast().joined(separator: " ")
        return [identity, variant, noun.lowercased()].filter { !$0.isEmpty }.joined(separator: " ")
    }

    func shoppingItemName(customVariant: String) -> String {
        let base = manualVariantBaseName
        let custom = customVariant.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !custom.isEmpty else { return samsungSafeShoppingItemName(base) }
        if custom.range(of: base, options: [.caseInsensitive, .diacriticInsensitive]) != nil {
            return samsungSafeShoppingItemName(custom)
        }
        return samsungSafeShoppingItemName("\(base) – \(custom)")
    }

    func familyFavoriteName(variant: OfferVariant? = nil) -> String {
        let selected = variant.map { shoppingItemName(variant: $0.name) }
            ?? samsungSafeShoppingItemName(conciseProductName)
        let quantity = variant?.quantity ?? self.quantity
        let unit = variant?.unit ?? self.unit
        guard let quantity, quantity > 0,
              let unit = unit?.trimmingCharacters(in: .whitespacesAndNewlines),
              !unit.isEmpty,
              selected.range(
                  of: #"\b\d+(?:[.,]\d+)?\s*(?:kg|g|ml|cl|l|stk\.?|pk\.?)\b"#,
                  options: [.regularExpression, .caseInsensitive]
              ) == nil else { return selected }
        let amount = quantity.rounded() == quantity
            ? String(Int(quantity))
            : quantity.formatted(.number.precision(.fractionLength(0...2)))
        return "\(selected) \(amount) \(unit)"
    }

    private var manualVariantBaseName: String {
        let base = conciseProductName
        let folded = base.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "da_DK"))
        if folded.contains("karen volf") { return "Karen Volf" }
        if folded.contains("godmorgen") && folded.contains("juice") { return "Godmorgen juice" }
        if folded.contains("tulip") && folded.contains("bacon") { return "Tulip bacon" }
        if folded.contains("arla") && folded.contains("cheasy") && folded.contains("koldskal") { return "Koldskål" }
        return variantBaseName(from: base)
    }

    private func firstWordOf(_ value: String) -> String {
        value.split(whereSeparator: \.isWhitespace).first.map(String.init) ?? value
    }

    private func isGenericVariantSuffix(_ variant: String) -> Bool {
        let value = variant.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "da_DK"))
        let exact = ["kakao", "barista", "instant kaffe", "ice tea", "letmaelk", "smorbart"]
        return exact.contains(value)
            || value.hasSuffix(" formalet")
            || value.range(of: #"^\d+-pak\s+i\s+"#, options: .regularExpression) != nil
            || value.hasPrefix("hele -lar")
    }

    private func variantBaseName(from base: String) -> String {
        let alternatives = base.components(separatedBy: RegexChoiceSeparator.regex)
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        guard alternatives.count > 1 else { return base }

        let firstWords = alternatives[0].split(whereSeparator: \.isWhitespace).map(String.init)
        guard firstWords.count > 1 else { return base }
        let brandWords = firstWords.dropLast().prefix { word in
            word.first?.isUppercase == true || word.allSatisfy(\.isNumber)
        }
        return brandWords.isEmpty ? base : brandWords.joined(separator: " ")
    }

    private func cleanedVariantSuffix(_ variant: String) -> String {
        variant.replacingOccurrences(of: #"^hele\s+-lår"#, with: "hele kyllingelår", options: [.regularExpression, .caseInsensitive])
    }
}

private enum RegexChoiceSeparator {
    static let regex = try! NSRegularExpression(pattern: #"\s+(?:eller|/|,)\s+"#, options: [.caseInsensitive])
}

private enum VariantChoiceSeparator {
    static let regex = try! NSRegularExpression(pattern: #"\s+(?:eller|/)\s+|\s*,\s*"#, options: [.caseInsensitive])
}

private extension String {
    func components(separatedBy regex: NSRegularExpression) -> [String] {
        let range = NSRange(startIndex..., in: self)
        var result: [String] = []
        var cursor = startIndex
        for match in regex.matches(in: self, range: range) {
            guard let matchRange = Range(match.range, in: self) else { continue }
            result.append(String(self[cursor..<matchRange.lowerBound]))
            cursor = matchRange.upperBound
        }
        result.append(String(self[cursor...]))
        return result
    }
}
