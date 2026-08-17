import Foundation

extension GroceryOffer {
    var memberPriceActivationHint: String? {
        guard memberPrice != nil, memberPriceRequiresActivation else { return nil }
        let app = memberPriceApp?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return app.isEmpty ? "Kræver aktivering" : "Kræver aktivering i \(app)"
    }
}
