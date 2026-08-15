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
            variants: ["Pepsi Max", "Faxe Kondi"],
            brand: "pepsi",
            canonicalFamily: "cola"
        )
        XCTAssertFalse(pepsi.exactlyMatchesSelectedItem("Coca Cola sodavand"))
        XCTAssertNil(pepsi.matchingSelectedItemName("Coca Cola sodavand"))
    }

    func testRegularCocaColaCannotReplaceSelectedZeroVariant() throws {
        let regular = try decodeOffer(
            id: "regular", retailer: "Netto", price: 10,
            productName: "Coca-Cola", variants: ["Coca-Cola"],
            brand: "coca cola", canonicalFamily: "cola"
        )
        let zero = try decodeOffer(
            id: "zero", retailer: "føtex", price: 15,
            productName: "Coca-Cola Zero", variants: ["Coca-Cola Zero"],
            brand: "coca cola", canonicalFamily: "cola", types: ["zero"]
        )

        let result = OfferPriceGuard().cheaperOffers(
            from: [regular],
            for: "Coca-Cola Zero",
            than: zero
        )

        XCTAssertTrue(result.isEmpty)
        XCTAssertNil(regular.matchingSelectedItemName("Coca-Cola Zero"))
    }

    func testMixedCampaignReturnsConcreteCocaColaVariant() throws {
        let rema = try decodeOffer(
            productName: "Coca-Cola, Fanta, Tuborg Squash eller Schweppes",
            variants: ["Coca-Cola", "Fanta", "Tuborg Squash", "Schweppes"]
        )
        XCTAssertEqual(rema.matchingSelectedItemName("Coca Cola sodavand"), "Coca-Cola")
    }

    func testDiscoverySearchBroadensWithoutUsingCategoryNeighbours() {
        XCTAssertEqual(
            GroceryOffer.priceGuardSearchTerms(for: "Coca Cola sodavand"),
            ["coca cola sodavand", "coca", "coca cola"]
        )
        XCTAssertEqual(
            GroceryOffer.priceGuardSearchTerms(for: "Lurpak smør"),
            ["lurpak smor", "lurpak"]
        )
        XCTAssertEqual(
            GroceryOffer.priceGuardSearchTerms(for: "Schulstad Signaturbrød"),
            ["schulstad signaturbrod", "schulstad"]
        )
        XCTAssertEqual(
            GroceryOffer.priceGuardSearchTerms(for: "Tuborg Classic"),
            ["tuborg classic", "tuborg"]
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
            variants: ["Pepsi Max", "Faxe Kondi"],
            brand: "pepsi",
            canonicalFamily: "cola"
        )

        let result = OfferPriceGuard().cheaperOffers(
            from: [pepsi, rema],
            for: "Coca Cola sodavand",
            than: selected
        )

        XCTAssertEqual(result.map(\.id), ["rema"])
    }

    func testLurpakShortCampaignNameStillReturnsEveryCheaperLurpakOffer() throws {
        let selected = try decodeOffer(
            id: "selected", retailer: "365discount", price: 20,
            productName: "Lurpak smør eller smørbar", variants: ["Lurpak smør", "Lurpak smørbar"],
            brand: "lurpak", canonicalFamily: "butter_spread"
        )
        let lidl10 = try decodeOffer(
            id: "lidl10", retailer: "Lidl", price: 10,
            productName: "Lurpak", variants: ["Lurpak"],
            brand: "lurpak", canonicalFamily: "butter_spread"
        )
        let meny18 = try decodeOffer(
            id: "meny18", retailer: "MENY", price: 18,
            productName: "Lurpak", variants: ["Lurpak"],
            brand: "lurpak", canonicalFamily: "butter_spread"
        )
        let lidl1995 = try decodeOffer(
            id: "lidl1995", retailer: "Lidl", price: 19.95,
            productName: "Lurpak smør eller smørbar", variants: ["Lurpak smør", "Lurpak smørbar"],
            brand: "lurpak", canonicalFamily: "butter_spread"
        )
        let kaergaarden = try decodeOffer(
            id: "wrong", retailer: "Netto", price: 8,
            productName: "Kærgården", variants: ["Kærgården"],
            brand: "kærgården", canonicalFamily: "butter_spread"
        )

        let result = OfferPriceGuard().cheaperOffers(
            from: [lidl1995, kaergaarden, meny18, lidl10],
            for: "Lurpak smør",
            than: selected
        )

        XCTAssertEqual(result.map(\.id), ["lidl10", "meny18", "lidl1995"])
        XCTAssertEqual(result.compactMap(\.price), [10, 18, 19.95])
    }

    func testTuborgClassicAt140FindsExact99Campaign() throws {
        let selected = try decodeOffer(
            id: "selected", retailer: "MENY", price: 140,
            productName: "Grøn Tuborg, Tuborg Classic eller Carlsberg Pilsner",
            variants: ["Grøn Tuborg", "Tuborg Classic", "Carlsberg Pilsner"]
        )
        let bilka = try decodeOffer(
            id: "bilka99", retailer: "Bilka", price: 99,
            productName: "Tuborg Classic, Grøn Tuborg eller Carlsberg Pilsner",
            variants: ["Tuborg Classic", "Grøn Tuborg", "Carlsberg Pilsner"]
        )
        let wrong = try decodeOffer(
            id: "royal", retailer: "Netto", price: 79,
            productName: "Royal Pilsner", variants: ["Royal Pilsner"],
            brand: "royal"
        )

        let result = OfferPriceGuard().cheaperOffers(
            from: [wrong, bilka],
            for: "Tuborg Classic",
            than: selected
        )

        XCTAssertEqual(result.map(\.id), ["bilka99"])
    }

    private func decodeOffer(
        id: String = "offer",
        retailer: String = "MENY",
        price: Double? = nil,
        productName: String,
        variants: [String],
        brand: String? = nil,
        canonicalFamily: String? = nil,
        types: [String] = [],
        unitPrice: Double? = nil,
        unitPriceUnit: String? = nil
    ) throws -> GroceryOffer {
        var identity: [String: Any] = [
            "product": productName,
            "flavours": [],
            "types": types,
            "pack_count": 1
        ]
        if let brand { identity["brand"] = brand }
        if let canonicalFamily { identity["canonical_family"] = canonicalFamily }
        if let unitPrice { identity["unit_price"] = unitPrice }
        if let unitPriceUnit { identity["unit_price_unit"] = unitPriceUnit }

        let payload: [String: Any] = [
            "id": id, "retailer": retailer, "publication_id": "publication-\(id)",
            "publication_title": "Uge 34", "product_name": productName,
            "source_url": "https://example.test", "raw_text": "",
            "safe_to_add": true,
            "product_identity": identity,
            "variants": variants.enumerated().map { ["id": "v\($0.offset)", "name": $0.element] }
        ]
        var mutablePayload = payload
        if let price { mutablePayload["price"] = price }
        return try JSONDecoder().decode(GroceryOffer.self, from: JSONSerialization.data(withJSONObject: mutablePayload))
    }
}
