import XCTest
@testable import BaggerShopping

final class Build60SelectedOfferTests: XCTestCase {
    func testTrailingStkPackCountBecomesProductPackNotShoppingQuantity() throws {
        let offer = try decodeOffer()

        XCTAssertEqual(
            offer.samsungSafeShoppingItemName("Hamburger Buns 6 Stk"),
            "Hamburger Buns 6-pak"
        )
        XCTAssertEqual(
            offer.samsungSafeShoppingItemName("Hotdog Buns 4 stk."),
            "Hotdog Buns 4-pak"
        )
    }

    func testWeightVolumeAndLeadingCountsRemainUntouched() throws {
        let offer = try decodeOffer()

        XCTAssertEqual(
            offer.samsungSafeShoppingItemName("Coca-Cola Zero 1,5 l"),
            "Coca-Cola Zero 1,5 l"
        )
        XCTAssertEqual(
            offer.samsungSafeShoppingItemName("6 Stk servietter"),
            "6 Stk servietter"
        )
        XCTAssertEqual(
            offer.samsungSafeShoppingItemName("Salling Seafoodmix"),
            "Salling Seafoodmix"
        )
    }

    func testDirectShoppingItemNameAlsoProtectsTrailingPackCount() throws {
        let offer = try decodeOffer(productName: "Hamburger Buns")

        XCTAssertEqual(
            offer.shoppingItemName(variant: "Hamburger Buns 6 Stk"),
            "Hamburger Buns 6-pak"
        )
    }

    private func decodeOffer(productName: String = "Quickbury Fastfood Buns") throws -> GroceryOffer {
        let payload: [String: Any] = [
            "id": "meny-buns",
            "retailer": "MENY",
            "publication_id": "meny-current",
            "publication_title": "Uge 34",
            "product_name": productName,
            "price": 14,
            "member_price": 9.95,
            "member_price_label": "MENY medlemspris",
            "member_price_requires_activation": true,
            "source_url": "https://example.test",
            "raw_text": "Hot Dog Buns, Hamburger Buns eller Mega Burger Buns",
            "safe_to_add": true,
            "variant_confidence": 0.99,
            "variants": [
                ["id": "v1", "name": "Hamburger Buns 6 Stk"],
                ["id": "v2", "name": "Hotdog Buns 4 Stk"],
                ["id": "v3", "name": "Mega Burger Buns 4 Stk"]
            ]
        ]
        let data = try JSONSerialization.data(withJSONObject: payload)
        return try JSONDecoder().decode(GroceryOffer.self, from: data)
    }
}
