import XCTest
@testable import BaggerShopping

final class OfferImageRecognitionTests: XCTestCase {
    func testDecodesServerEvidenceAndKeepsConfirmationRequired() throws {
        let data = Data(#"""
        {
          "ok": true,
          "observed_text": "DET GODE SOLSIKKERUGBRØD",
          "variants": [{
            "name": "Schulstad Det Gode Solsikkerugbrød",
            "confidence": 0.91,
            "match_level": "probably_same",
            "explanation": "Samme produktfamilie.",
            "evidence": ["source:apple-vision", "family:bread"]
          }],
          "confidence": 0.91,
          "requires_confirmation": true
        }
        """#.utf8)

        let response = try JSONDecoder().decode(OfferImageEvidenceResponse.self, from: data)

        XCTAssertTrue(response.requiresConfirmation)
        XCTAssertEqual(response.variants.first?.name, "Schulstad Det Gode Solsikkerugbrød")
        XCTAssertEqual(response.variants.first?.matchLevel, "probably_same")
        XCTAssertEqual(response.variants.first?.evidence, ["source:apple-vision", "family:bread"])
    }
}
