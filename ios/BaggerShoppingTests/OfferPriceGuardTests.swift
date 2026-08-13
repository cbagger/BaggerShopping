import XCTest
@testable import BaggerShopping

final class OfferPriceGuardTests: XCTestCase {
    func testExactVariantMatchesComposedShoppingItemName() throws {
        let offer = try decodeOffer(
            productName: "Coca-Cola, Fanta eller Squash sodavand",
            variants: ["Coca-Cola", "Fanta", "Squash sodavand"]
        )
        XCTAssertTrue(offer.exactlyMatchesSelectedItem("Coca-Cola"))
        XCTAssertTrue(offer.exactlyMatchesSelectedItem("Squash sodavand"))
    }

    func testDifferentVariantAndBroadCampaignDoNotMatch() throws {
        let offer = try decodeOffer(
            productName: "Coca-Cola, Fanta eller Squash sodavand",
            variants: ["Coca-Cola", "Fanta", "Squash sodavand"]
        )
        XCTAssertFalse(offer.exactlyMatchesSelectedItem("Coca-Cola Zero"))
        XCTAssertFalse(offer.exactlyMatchesSelectedItem("Sodavand"))
    }

    private func decodeOffer(productName: String, variants: [String]) throws -> GroceryOffer {
        let payload: [String: Any] = [
            "id": "offer", "retailer": "MENY", "publication_id": "publication",
            "publication_title": "Uge 34", "product_name": productName,
            "source_url": "https://example.test", "raw_text": "",
            "safe_to_add": true,
            "variants": variants.enumerated().map { ["id": "v\($0.offset)", "name": $0.element] }
        ]
        return try JSONDecoder().decode(GroceryOffer.self, from: JSONSerialization.data(withJSONObject: payload))
    }
}
