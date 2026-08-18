import XCTest
@testable import BaggerShopping

final class VariantPackSelectionTests: XCTestCase {
    func testStructuredPieceCountBecomesPackDescriptorInsteadOfShoppingQuantity() throws {
        let offer = try decodeOffer(
            productName: "Quickbury Fastfood Buns",
            variants: [
                ["id": "hamburger", "name": "Hamburger Buns", "quantity": 6, "unit": "stk"],
                ["id": "hotdog", "name": "Hotdog Buns", "quantity": 8, "unit": "stk"],
            ]
        )

        XCTAssertEqual(
            offer.shoppingItemName(variant: "Hamburger Buns"),
            "Hamburger Buns 6-pak"
        )
        XCTAssertEqual(
            offer.shoppingItemName(variant: "Hotdog Buns"),
            "Hotdog Buns 8-pak"
        )
    }

    func testPhysicalWeightDoesNotBecomeShoppingPackCount() throws {
        let offer = try decodeOffer(
            productName: "Rugbrød",
            variants: [
                ["id": "bread", "name": "Solsikkerugbrød", "quantity": 950, "unit": "g"],
            ]
        )

        XCTAssertEqual(
            offer.shoppingItemName(variant: "Solsikkerugbrød"),
            "Solsikkerugbrød"
        )
    }

    func testExistingPieceSuffixIsNormalizedOnce() throws {
        let offer = try decodeOffer(
            productName: "Quickbury Fastfood Buns",
            variants: [
                ["id": "hamburger", "name": "Hamburger Buns 6 Stk", "quantity": 6, "unit": "stk"],
            ]
        )

        XCTAssertEqual(
            offer.shoppingItemName(variant: "Hamburger Buns 6 Stk"),
            "Hamburger Buns 6-pak"
        )
    }

    private func decodeOffer(
        productName: String,
        variants: [[String: Any]]
    ) throws -> GroceryOffer {
        let payload: [String: Any] = [
            "id": "offer",
            "retailer": "MENY",
            "publication_id": "paper",
            "publication_title": "Uge 34",
            "product_name": productName,
            "source_url": "https://example.test",
            "raw_text": "Frit valg",
            "safe_to_add": true,
            "variant_confidence": 0.99,
            "variants": variants,
        ]
        let data = try JSONSerialization.data(withJSONObject: payload)
        return try JSONDecoder().decode(GroceryOffer.self, from: data)
    }
}
