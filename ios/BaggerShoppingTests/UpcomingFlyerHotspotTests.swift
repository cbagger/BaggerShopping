import XCTest
@testable import BaggerShopping

final class UpcomingFlyerHotspotTests: XCTestCase {
    func testDisplayOnlyHotspotDecodesButUpcomingOfferCannotAdd() throws {
        let payload: [String: Any] = [
            "id": "future-offer",
            "retailer": "365discount",
            "publication_id": "future-paper",
            "publication_title": "365 Fødselsdag",
            "valid_from": "27.08.2026",
            "valid_until": "02.09.2026",
            "product_name": "Lurpak smør",
            "source_url": "https://example.test/flyer",
            "page_number": 1,
            "hotspot_x": NSNull(),
            "hotspot_y": NSNull(),
            "hotspot_width": NSNull(),
            "hotspot_height": NSNull(),
            "display_hotspot_x": 0.10,
            "display_hotspot_y": 0.20,
            "display_hotspot_width": 0.30,
            "display_hotspot_height": 0.15,
            "raw_text": "Lurpak smør 12 kr.",
            "safe_to_add": false,
            "publication_status": "upcoming",
            "variants": [["id": "v1", "name": "Lurpak smør"]],
            "variant_confidence": 1.0,
        ]

        let data = try JSONSerialization.data(withJSONObject: payload)
        let offer = try JSONDecoder().decode(GroceryOffer.self, from: data)

        XCTAssertEqual(offer.hotspotX, 0.10)
        XCTAssertEqual(offer.hotspotY, 0.20)
        XCTAssertEqual(offer.hotspotWidth, 0.30)
        XCTAssertEqual(offer.hotspotHeight, 0.15)
        XCTAssertFalse(offer.safeToAdd)
        XCTAssertEqual(offer.choiceState, .unspecified)
        XCTAssertEqual(offer.addAvailabilityTitle, "Tilbuddet er ikke aktivt endnu")
        XCTAssertTrue(offer.addAvailabilityMessage?.contains("27.08.2026") == true)
    }
}
