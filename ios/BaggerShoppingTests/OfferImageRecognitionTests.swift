import XCTest
@testable import BaggerShopping

final class OfferImageRecognitionTests: XCTestCase {
    func testImageGeometryAcceptsTinyRoundingAndClampsToPage() throws {
        let offer = try makeOffer(x: 0.2, y: 0.1, width: 0.8001, height: 0.2)

        let hotspot = try XCTUnwrap(OfferImageGeometry.validatedHotspot(for: offer))

        XCTAssertEqual(hotspot.minX, 0.2, accuracy: 0.0001)
        XCTAssertEqual(hotspot.width, 0.8, accuracy: 0.0001)
    }

    func testImageGeometryRejectsMaterialOvershoot() throws {
        let offer = try makeOffer(x: 0.8, y: 0.1, width: 0.4, height: 0.2)

        XCTAssertNil(OfferImageGeometry.validatedHotspot(for: offer))
    }

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

    private func makeOffer(x: Double, y: Double, width: Double, height: Double) throws -> GroceryOffer {
        let payload: [String: Any] = [
            "id": "bread", "retailer": "REMA 1000",
            "publication_id": "week-34", "publication_title": "Uge 34",
            "product_name": "Schulstad brød", "source_url": "https://example.test",
            "image_url": "https://example.test/page.jpg", "raw_text": "",
            "hotspot_x": x, "hotspot_y": y,
            "hotspot_width": width, "hotspot_height": height,
            "safe_to_add": true, "variants": []
        ]
        return try JSONDecoder().decode(
            GroceryOffer.self,
            from: JSONSerialization.data(withJSONObject: payload)
        )
    }
}
