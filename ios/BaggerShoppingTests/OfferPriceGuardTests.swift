import XCTest
@testable import BaggerShopping

final class OfferPriceGuardTests: XCTestCase {
    func testExactVariantMatchesComposedShoppingItemName() throws {
        let offer = try decodeOffer(
            productName: "Coca-Cola, Fanta eller Squash sodavand",
            variants: ["Coca-Cola", "Fanta", "Squash sodavand"]
        )
        XCTAssertTrue(offer.exactlyMatchesSelectedItem("Coca-Cola"))
        XCTAssertTrue(offer.exactlyMatchesSelectedItem("Coca Cola sodavand"))
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

    func testPepsiCampaignCannotReplaceSelectedCocaCola() throws {
        let pepsi = try decodeOffer(
            productName: "Pepsi Max eller Faxe Kondi",
            variants: ["Pepsi Max", "Faxe Kondi"]
        )
        XCTAssertFalse(pepsi.exactlyMatchesSelectedItem("Coca Cola sodavand"))
        XCTAssertNil(pepsi.matchingSelectedItemName("Coca Cola sodavand"))
    }

    func testMixedCampaignReturnsConcreteCocaColaVariant() throws {
        let rema = try decodeOffer(
            productName: "Coca-Cola, Fanta, Tuborg Squash eller Schweppes",
            variants: ["Coca-Cola", "Fanta", "Tuborg Squash", "Schweppes"]
        )
        XCTAssertEqual(rema.matchingSelectedItemName("Coca Cola sodavand"), "Coca-Cola")
    }

    func testDiscoverySearchBroadensGenericDescriptorWithoutLosingOriginal() {
        XCTAssertEqual(
            GroceryOffer.priceGuardSearchTerms(for: "Coca Cola sodavand"),
            ["coca cola sodavand", "coca cola"]
        )
        XCTAssertEqual(
            GroceryOffer.priceGuardSearchTerms(for: "Lurpak smør"),
            ["lurpak smor", "lurpak"]
        )
        XCTAssertEqual(
            GroceryOffer.priceGuardSearchTerms(for: "Schulstad Signaturbrød"),
            ["schulstad signaturbrod"]
        )
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

    func testCheaperListKeepsCocaColaCampaignAndRejectsPepsi() throws {
        let selected = try decodeOffer(
            id: "foetex", retailer: "føtex", price: 17,
            productName: "Cocio eller Coca Cola sodavand",
            variants: ["Cocio", "Coca Cola sodavand"]
        )
        let rema = try decodeOffer(
            id: "rema", retailer: "REMA 1000", price: 14,
            productName: "Coca-Cola, Fanta, Tuborg Squash eller Schweppes",
            variants: ["Coca-Cola", "Fanta", "Tuborg Squash", "Schweppes"]
        )
        let pepsi = try decodeOffer(
            id: "pepsi", retailer: "REMA 1000", price: 15,
            productName: "Pepsi Max eller Faxe Kondi",
            variants: ["Pepsi Max", "Faxe Kondi"]
        )

        let result = OfferPriceGuard().cheaperOffers(
            from: [pepsi, rema],
            for: "Coca Cola sodavand",
            than: selected
        )

        XCTAssertEqual(result.map(\.id), ["rema"])
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
