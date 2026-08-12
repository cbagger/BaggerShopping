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

    var choiceState: OfferChoiceState {
        let names = variants
            .map(\.name)
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .reduce(into: [String]()) { result, name in
                if !result.contains(where: { $0.caseInsensitiveCompare(name) == .orderedSame }) {
                    result.append(name)
                }
            }

        if names.count > 1 { return .variants(names) }
        if names.count == 1, !hasUnresolvedVariantLanguage { return .direct(names[0]) }
        return .unspecified
    }

    var hasUnresolvedVariantLanguage: Bool {
        let value = "\(productName) \(rawText)".lowercased()
        return ["frit valg", "flere varianter", "udvalgte varianter", "assorteret"]
            .contains(where: value.contains)
    }

    func shoppingItemName(variant: String?) -> String {
        let base = conciseProductName
        guard let variant = variant?.trimmingCharacters(in: .whitespacesAndNewlines),
              !variant.isEmpty,
              variant.caseInsensitiveCompare(base) != .orderedSame else { return base }

        if variant.range(of: base, options: [.caseInsensitive, .diacriticInsensitive]) != nil {
            return variant
        }
        let words = variant.split(whereSeparator: \.isWhitespace)
        let leadingJoinWords = ["i", "med", "uden", "af", "til"]
        let beginsAsSuffix = words.first.map { leadingJoinWords.contains($0.lowercased()) } ?? false
        if (words.count >= 2 && !beginsAsSuffix)
            || variant.contains("-")
            || variant.contains("!") {
            return variant
        }
        return "\(base) – \(variant)"
    }
}
