import XCTest
@testable import BaggerShopping

final class OfferPresentationTests: XCTestCase {
    func testVariantKeepsCampaignNameWhenOnlySuffixIsReturned() throws {
        let offer = try decodeOffer(
            productName: "Naturli’ drik",
            rawText: "Flere varianter",
            variants: ["Kakao", "Barista"]
        )

        XCTAssertEqual(offer.choiceState, .variants(["Kakao", "Barista"]))
        XCTAssertEqual(offer.shoppingItemName(variant: "Kakao"), "Naturli’ drik – Kakao")
    }

    func testCompleteVariantNameIsNotDuplicated() throws {
        let offer = try decodeOffer(
            productName: "Xtra! tun",
            rawText: "Frit valg",
            variants: ["Xtra! tun i vand", "Xtra! tun i olie"]
        )

        XCTAssertEqual(offer.shoppingItemName(variant: "Xtra! tun i vand"), "Xtra! tun i vand")
    }

    func testCompleteBrandProductVariantsStandAlone() throws {
        let softDrinks = try decodeOffer(
            productName: "Coca-Cola, Fanta, Tuborg Squash eller Sprite",
            rawText: "Flere varianter",
            variants: ["Coca-Cola", "Tuborg Squash"]
        )
        XCTAssertEqual(softDrinks.shoppingItemName(variant: "Coca-Cola"), "Coca-Cola")
        XCTAssertEqual(softDrinks.shoppingItemName(variant: "Tuborg Squash"), "Tuborg Squash")

        let dairy = try decodeOffer(
            productName: "Arla eller Cheasy Koldskål eller Cultura Kefir",
            rawText: "Frit valg",
            variants: ["Cheasy Koldskål", "Cultura Kefir"]
        )
        XCTAssertEqual(dairy.shoppingItemName(variant: "Cheasy Koldskål"), "Cheasy Koldskål")
        XCTAssertEqual(dairy.shoppingItemName(variant: "Cultura Kefir"), "Cultura Kefir")
    }

    func testUnresolvedGroupedOfferDoesNotAddFallbackVariantDirectly() throws {
        let offer = try decodeOffer(
            productName: "3-stjernet pålæg",
            rawText: "Frit valg",
            variants: ["3-stjernet pålæg"]
        )

        XCTAssertEqual(offer.choiceState, .unspecified)
        XCTAssertEqual(offer.shoppingItemName(variant: nil), "3-stjernet pålæg")
    }

    private func decodeOffer(productName: String, rawText: String, variants: [String]) throws -> GroceryOffer {
        let payload: [String: Any] = [
            "id": "offer", "retailer": "365discount", "publication_id": "paper",
            "publication_title": "Uge 33", "product_name": productName,
            "source_url": "https://example.test", "raw_text": rawText, "safe_to_add": true,
            "variants": variants.enumerated().map { ["id": "v\($0.offset)", "name": $0.element] }
        ]
        let data = try JSONSerialization.data(withJSONObject: payload)
        return try JSONDecoder().decode(GroceryOffer.self, from: data)
    }
}
