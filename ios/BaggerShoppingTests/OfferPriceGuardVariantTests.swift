import XCTest
@testable import BaggerShopping

final class OfferPriceGuardVariantTests: XCTestCase {
    func testBakkedalMixedCampaignFindsCheaperConcreteBakkedalOffer() throws {
        let selected = try decodeOffer(
            id: "foetex-1495",
            retailer: "føtex",
            price: 14.95,
            productName: "AMA fedtstof eller Bakkedal smørbar",
            offerQuantity: 500,
            offerUnit: "g",
            productUnitPrice: nil,
            variantName: "Bakkedal smørbar",
            variantQuantity: 200,
            variantUnit: "g",
            variantUnitPrice: 74.75
        )
        let cheaper = try decodeOffer(
            id: "bilka-12",
            retailer: "Bilka",
            price: 12,
            productName: "Bakkedal smørbar",
            offerQuantity: 200,
            offerUnit: "g",
            productUnitPrice: 60,
            variantName: "Bakkedal smørbar",
            variantQuantity: 200,
            variantUnit: "g",
            variantUnitPrice: 60
        )

        let result = OfferPriceGuard().cheaperOffers(
            from: [selected, cheaper],
            for: "Bakkedal smørbar",
            than: selected
        )

        XCTAssertEqual(result.map(\.id), ["bilka-12"])
        XCTAssertEqual(result.first?.price, 12)
    }

    private func decodeOffer(
        id: String,
        retailer: String,
        price: Double,
        productName: String,
        offerQuantity: Double,
        offerUnit: String,
        productUnitPrice: Double?,
        variantName: String,
        variantQuantity: Double,
        variantUnit: String,
        variantUnitPrice: Double
    ) throws -> GroceryOffer {
        var productIdentity: [String: Any] = [
            "product": productName,
            "flavours": [],
            "types": [],
            "pack_count": 1,
            "unit_price_unit": "kg"
        ]
        if let productUnitPrice {
            productIdentity["unit_price"] = productUnitPrice
        }

        let variantIdentity: [String: Any] = [
            "product": variantName,
            "flavours": [],
            "types": [],
            "pack_count": 1,
            "unit_price": variantUnitPrice,
            "unit_price_unit": "kg"
        ]

        let payload: [String: Any] = [
            "id": id,
            "retailer": retailer,
            "publication_id": "publication-\(id)",
            "publication_title": "Uge 34",
            "product_name": productName,
            "price": price,
            "quantity": offerQuantity,
            "unit": offerUnit,
            "source_url": "https://example.test",
            "raw_text": "",
            "safe_to_add": true,
            "product_identity": productIdentity,
            "variants": [[
                "id": "variant-\(id)",
                "name": variantName,
                "quantity": variantQuantity,
                "unit": variantUnit,
                "identity": variantIdentity
            ]]
        ]

        return try JSONDecoder().decode(
            GroceryOffer.self,
            from: JSONSerialization.data(withJSONObject: payload)
        )
    }
}
