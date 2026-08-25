import XCTest
@testable import BaggerShopping

final class FamilyQuickAddTests: XCTestCase {
    func testSuggestionsRequireThreePurchasesAndExcludeCurrentListItems() {
        let ranked = [
            FamilyQuickAddItem(name: "Mælk", purchaseCount: 8, rank: 1, eligible: true),
            FamilyQuickAddItem(name: "Rugbrød", purchaseCount: 4, rank: 2, eligible: true),
            FamilyQuickAddItem(name: "Smør", purchaseCount: 2, rank: 3, eligible: false),
        ]
        let current = [ShoppingItem(id: "milk", name: " mælk ", checked: false)]

        let suggestions = FamilyQuickAddService.suggestions(from: ranked, excluding: current)

        XCTAssertEqual(suggestions.map(\.name), ["Rugbrød"])
    }

    func testSuggestionsKeepServerRankAndNeverExceedTopTen() {
        let ranked = (1...12).reversed().map { rank in
            FamilyQuickAddItem(
                name: "Vare \(rank)",
                purchaseCount: rank,
                rank: rank,
                eligible: true
            )
        }

        let suggestions = FamilyQuickAddService.suggestions(from: ranked, excluding: [])

        XCTAssertEqual(suggestions.count, 10)
        XCTAssertEqual(suggestions.map(\.rank), Array(1...10))
    }
}
