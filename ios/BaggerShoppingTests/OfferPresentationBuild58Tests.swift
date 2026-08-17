import XCTest
@testable import BaggerShopping

final class OfferPresentationBuild58Tests: XCTestCase {
    func testWeakDuplicateCampaignHeadingIsNotShownAsFakeVariant() throws {
        let offer = try decodeOffer(
            productName: "SPIR plantedrik",
            rawText: "SPIR plantedrik 1 liter",
            variants: ["SPIR plantedrik"],
            variantConfidence: 0.62,
            qualitySignals: []
        )
        XCTAssertEqual(offer.resolvedVariantNames, [])
        XCTAssertEqual(offer.choiceState, .unspecified)
    }

    func testLunaMultipleProductsSignalBlocksSingletonEvenAtHighConfidence() throws {
        let offer = try decodeOffer(
            productName: "PÅLÆGSSLAGTEREN Pålæg",
            rawText: "70-150 g",
            variants: ["PÅLÆGSSLAGTEREN Pålæg"],
            variantConfidence: 0.99,
            qualitySignals: ["luna-semantic-audited", "luna-multiple-products"]
        )
        XCTAssertTrue(offer.hasUnresolvedVariantLanguage)
        XCTAssertEqual(offer.resolvedVariantNames, [])
        XCTAssertEqual(offer.choiceState, .unspecified)
    }

    func testLunaNamedMultipleVariantsAreShownInPicker() throws {
        let offer = try decodeOffer(
            productName: "Cheasy eller Riberhus ost",
            rawText: "Flere varianter",
            variants: ["Cheasy havarti", "Riberhus skiveost"],
            variantConfidence: 0.99,
            qualitySignals: ["luna-semantic-audited", "luna-multiple-products", "luna-verified-variants"]
        )
        XCTAssertEqual(
            offer.choiceState,
            .variants(["Cheasy havarti", "Riberhus skiveost"])
        )
    }

    private func decodeOffer(
        productName: String,
        rawText: String,
        variants: [String],
        variantConfidence: Double,
        qualitySignals: [String]
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
            "quality_signals": qualitySignals,
            "variants": variants.enumerated().map {
                ["id": "v\($0.offset)", "name": $0.element]
            }
        ]
        let data = try JSONSerialization.data(withJSONObject: payload)
        return try JSONDecoder().decode(GroceryOffer.self, from: data)
    }
}
