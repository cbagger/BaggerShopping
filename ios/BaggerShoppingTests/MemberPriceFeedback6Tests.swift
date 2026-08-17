import XCTest
@testable import BaggerShopping

final class MemberPriceFeedback6Tests: XCTestCase {
    func testActivationHintIsOnlyShownForExplicitActivationRequirement() throws {
        let ordinary = try decodeOffer(
            id: "bilka-becel",
            retailer: "Bilka",
            productName: "Becel flydende",
            price: 15,
            memberPrice: 12,
            label: "Bilka Plus",
            app: "Bilka Plus",
            requiresActivation: false
        )
        XCTAssertNil(ordinary.memberPriceActivationHint)

        let activated = try decodeOffer(
            id: "meny-puck",
            retailer: "MENY",
            productName: "Puck hvid ost",
            price: 28,
            memberPrice: 22,
            label: "MENY medlemspris",
            app: "MENY-appen",
            requiresActivation: true
        )
        XCTAssertEqual(activated.memberPriceActivationHint, "Kræver aktivering i MENY-appen")
    }

    func testActivationHintFallsBackWhenNoAppNameIsKnown() throws {
        let offer = try decodeOffer(
            id: "generic-member",
            retailer: "SPAR",
            productName: "Testvare",
            price: 45,
            memberPrice: 35,
            label: "Medlemspris",
            app: nil,
            requiresActivation: true
        )
        XCTAssertEqual(offer.memberPriceActivationHint, "Kræver aktivering")
    }

    private func decodeOffer(
        id: String,
        retailer: String,
        productName: String,
        price: Double,
        memberPrice: Double,
        label: String,
        app: String?,
        requiresActivation: Bool
    ) throws -> GroceryOffer {
        var payload: [String: Any] = [
            "id": id,
            "retailer": retailer,
            "publication_id": "publication-\(id)",
            "publication_title": "Uge 34",
            "product_name": productName,
            "price": price,
            "member_price": memberPrice,
            "member_price_label": label,
            "member_price_requires_activation": requiresActivation,
            "source_url": "https://example.test",
            "raw_text": productName,
            "safe_to_add": true,
            "variants": []
        ]
        if let app { payload["member_price_app"] = app }

        return try JSONDecoder().decode(
            GroceryOffer.self,
            from: JSONSerialization.data(withJSONObject: payload)
        )
    }
}
