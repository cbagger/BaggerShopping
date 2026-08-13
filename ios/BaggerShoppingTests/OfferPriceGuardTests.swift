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

    func testAllCheaperExactOffersAreReturnedPriceFirst() throws {
        let selected = try decodeOffer(id: "rema", retailer: "REMA 1000", price: 15, productName: "Coca-Cola", variants: ["Coca-Cola"])
        let meny = try decodeOffer(id: "meny", retailer: "MENY", price: 10, productName: "Coca-Cola", variants: ["Coca-Cola"])
        let netto = try decodeOffer(id: "netto", retailer: "Netto", price: 10, productName: "Coca-Cola", variants: ["Coca-Cola"])
        let bilka = try decodeOffer(id: "bilka", retailer: "Bilka", price: 12, productName: "Coca-Cola", variants: ["Coca-Cola"])
        let wrongVariant = try decodeOffer(id: "zero", retailer: "føtex", price: 8, productName: "Coca-Cola Zero", variants: ["Coca-Cola Zero"])

        let result = OfferPriceGuard().cheaperOffers(
            from: [selected, bilka, netto, wrongVariant, meny],
            for: "Coca-Cola",
            than: selected
        )

        XCTAssertEqual(result.map(\.retailer), ["MENY", "Netto", "Bilka"])
        XCTAssertEqual(result.compactMap(\.price), [10, 10, 12])
    }

    private func decodeOffer(
        id: String = "offer",
        retailer: String = "MENY",
        price: Double? = nil,
        productName: String,
        variants: [String]
    ) throws -> GroceryOffer {
        let payload: [String: Any] = [
            "id": id, "retailer": retailer, "publication_id": "publication-\(id)",
            "publication_title": "Uge 34", "product_name": productName,
            "source_url": "https://example.test", "raw_text": "",
            "safe_to_add": true,
            "variants": variants.enumerated().map { ["id": "v\($0.offset)", "name": $0.element] }
        ]
        var mutablePayload = payload
        if let price { mutablePayload["price"] = price }
        return try JSONDecoder().decode(GroceryOffer.self, from: JSONSerialization.data(withJSONObject: mutablePayload))
    }
}
