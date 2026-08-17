import XCTest
@testable import BaggerShopping

final class OfferPresentationBuild57Tests: XCTestCase {
    func testWeakSingletonCampaignHeadingNeverDirectAdds() throws {
        let offer = try decodeOffer(
            productName: "PÅLÆGSSLAGTEREN Pålæg",
            rawText: "70-150 g. Maks. 6 pk. pr. kunde.",
            variants: ["PÅLÆGSSLAGTEREN Pålæg"],
            variantConfidence: 0.62
        )
        XCTAssertEqual(offer.choiceState, .unspecified)
    }

    func testHighConfidenceSingletonCanStillDirectAdd() throws {
        let offer = try decodeOffer(
            productName: "Gestus Bacon i skiver",
            rawText: "Gestus Bacon i skiver",
            variants: ["Gestus Bacon i skiver"],
            variantConfidence: 0.97
        )
        XCTAssertEqual(offer.choiceState, .direct("Gestus Bacon i skiver"))
    }

    func testAdditionalExplicitMultiVariantLanguageBlocksDirectAdd() throws {
        for phrase in ["Forskellige varianter", "Flere slags", "Vælg mellem varianterne"] {
            let offer = try decodeOffer(
                productName: "Chokolade",
                rawText: phrase,
                variants: ["Chokolade"],
                variantConfidence: 0.99
            )
            XCTAssertEqual(offer.choiceState, .unspecified, phrase)
        }
    }

    private func decodeOffer(
        productName: String,
        rawText: String,
        variants: [String],
        variantConfidence: Double
    ) throws -> GroceryOffer {
        let payload: [String: Any] = [
            "id": "offer",
            "retailer": "Netto",
            "publication_id": "paper",
            "publication_title": "Uge 34",
            "product_name": productName,
            "source_url": "https://example.test",
            "raw_text": rawText,
            "safe_to_add": true,
            "variant_confidence": variantConfidence,
            "variants": variants.enumerated().map {
                ["id": "v\($0.offset)", "name": $0.element]
            }
        ]
        let data = try JSONSerialization.data(withJSONObject: payload)
        return try JSONDecoder().decode(GroceryOffer.self, from: data)
    }
}
