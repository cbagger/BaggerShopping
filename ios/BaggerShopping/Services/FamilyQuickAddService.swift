import Foundation

enum FamilyQuickAddService {
    static let minimumPurchases = 3
    static let maximumItems = 10

    nonisolated static func suggestions(
        from rankedItems: [FamilyQuickAddItem],
        excluding currentItems: [ShoppingItem]
    ) -> [FamilyQuickAddItem] {
        let currentNames = Set(currentItems.map { normalize($0.name) })
        return rankedItems
            .filter { $0.eligible && $0.purchaseCount >= minimumPurchases }
            .filter { !currentNames.contains(normalize($0.name)) }
            .sorted { $0.rank < $1.rank }
            .prefix(maximumItems)
            .map { $0 }
    }

    nonisolated static func normalize(_ name: String) -> String {
        ShoppingCategoryService.normalize(name)
    }
}
