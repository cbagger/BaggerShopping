import Foundation

enum OfferChoiceState: Equatable {
    case direct(String)
    case variants([String])
    case unspecified
}
extension GroceryOffer {
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
        let base = productName.trimmingCharacters(in: CharacterSet(charactersIn: "* "))
        guard let variant = variant?.trimmingCharacters(in: .whitespacesAndNewlines),
              !variant.isEmpty,
              variant.caseInsensitiveCompare(base) != .orderedSame else { return base }

        if variant.range(of: base, options: [.caseInsensitive, .diacriticInsensitive]) != nil {
            return variant
        }
        return "\(base) – \(variant)"
    }
}
