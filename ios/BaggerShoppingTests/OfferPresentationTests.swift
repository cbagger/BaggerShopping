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

    func testGenericVariantsKeepTheProductIdentity() throws {
        let coffee = try decodeOffer(productName: "BKI kaffe", rawText: "Frit valg", variants: ["BKI formalet", "instant kaffe"])
        XCTAssertEqual(coffee.shoppingItemName(variant: "BKI formalet"), "BKI formalet kaffe")
        XCTAssertEqual(coffee.shoppingItemName(variant: "instant kaffe"), "BKI kaffe – instant kaffe")

        let drink = try decodeOffer(productName: "Rynkeby frugtdrik eller ice tea", rawText: "Frit valg", variants: ["Rynkeby frugtdrik", "ice tea"])
        XCTAssertEqual(drink.shoppingItemName(variant: "ice tea"), "Rynkeby – ice tea")

        let bacon = try decodeOffer(productName: "Tulip Bacon", rawText: "Frit valg", variants: ["5-pak i skiver", "2-pak i tern"])
        XCTAssertEqual(bacon.shoppingItemName(variant: "5-pak i skiver"), "Tulip Bacon – 5-pak i skiver")

        let chicken = try decodeOffer(productName: "MADVÆRKET Kyllingebrystfilet eller hele -lår med ryg", rawText: "Frit valg", variants: ["MADVÆRKET Kyllingebrystfilet", "hele -lår med ryg"])
        XCTAssertEqual(chicken.shoppingItemName(variant: "hele -lår med ryg"), "MADVÆRKET – hele kyllingelår med ryg")
    }

    func testGroupedTitlesDoNotGetRepeatedInSelectedName() throws {
        let milk = try decodeOffer(productName: "REMA 1000 Minimælk eller letmælk", rawText: "Frit valg", variants: ["REMA 1000 Minimælk", "letmælk"])
        XCTAssertEqual(milk.shoppingItemName(variant: "letmælk"), "REMA 1000 – letmælk")

        let butter = try decodeOffer(productName: "Thise økologisk smør eller smørbart", rawText: "Frit valg", variants: ["Thise økologisk smør", "smørbart"])
        XCTAssertEqual(butter.shoppingItemName(variant: "smørbart"), "Thise – smørbart")
    }

    func testManualVariantsAlwaysKeepAConciseProductIdentity() throws {
        let biscuits = try decodeOffer(productName: "Karen Volf marked", rawText: "Flere varianter", variants: [])
        XCTAssertEqual(biscuits.shoppingItemName(customVariant: "Havreflager"), "Karen Volf – Havreflager")

        let juice = try decodeOffer(productName: "Økologisk Godmorgen juice", rawText: "Frit valg", variants: [])
        XCTAssertEqual(juice.shoppingItemName(customVariant: "Æble"), "Godmorgen juice – Æble")

        let chicken = try decodeOffer(productName: "Crispy Kylling", rawText: "Flere varianter", variants: [])
        XCTAssertEqual(chicken.shoppingItemName(customVariant: "Sprøde Kyllingebidder"), "Crispy Kylling – Sprøde Kyllingebidder")

        let bacon = try decodeOffer(productName: "Tulip bacon i skiver eller i tern", rawText: "Frit valg", variants: [])
        XCTAssertEqual(bacon.shoppingItemName(customVariant: "i tern"), "Tulip bacon – i tern")
    }

    func testSharedAndPropertyVariantsKeepProductIdentity() throws {
        let chicken = try decodeOffer(productName: "Coop kyllingeover- eller underlår", rawText: "Frit valg", variants: ["Coop kyllingeover-", "underlår"])
        XCTAssertEqual(chicken.shoppingItemName(variant: "underlår"), "Coop kyllingeunderlår")

        let cheese = try decodeOffer(productName: "Klovborg Skæreost", rawText: "Mellemlagret 45+ eller Mild 45+", variants: ["Mellemlagret 45+", "Mild 45+"])
        XCTAssertEqual(cheese.shoppingItemName(variant: "Mellemlagret 45+"), "Klovborg Mellemlagret 45+ skæreost")
        XCTAssertEqual(cheese.shoppingItemName(variant: "Mild 45+"), "Klovborg Mild 45+ skæreost")
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
