import Foundation

enum RetailerCatalog {
    static let all: [String] = [
        "MENY",
        "365discount",
        "REMA 1000",
        "Bilka",
        "føtex",
        "Lidl",
        "Netto",
        "SPAR",
        "SuperBrugsen",
        "Brugsen",
        "Min Købmand",
        "LET-KØB"
    ]

    static func canonicalRetailer(_ value: String) -> String? {
        let normalized = ShoppingCategoryService.normalize(value)
        return all.first { retailer in
            let candidate = ShoppingCategoryService.normalize(retailer)
            return normalized == candidate
                || normalized.hasPrefix(candidate + " ")
                || normalized.contains(" " + candidate + " ")
        }
    }
}
