import XCTest
@testable import BaggerShopping

final class PerformanceCacheTests: XCTestCase {
    func testFlyerPublicationCacheReturnsRecentlySavedShelf() throws {
        let publication = try decodePublication()
        FlyerPublicationCache.save([publication])

        let cached = try XCTUnwrap(FlyerPublicationCache.load(maxAge: 60))

        XCTAssertEqual(cached.publications.map(\.id), ["week-34"])
        XCTAssertEqual(cached.publications.first?.retailer, "365discount")
    }

    func testSmartOfferMatchResponseDecodesSingleBatchForMultipleItems() throws {
        let data = Data(#"""
        {
          "ok": true,
          "item_count": 2,
          "offer_count": 2,
          "matches": [
            {"item_name":"Mælk","offers":[]},
            {"item_name":"Rugbrød","offers":[]}
          ]
        }
        """#.utf8)

        let response = try JSONDecoder().decode(SmartOfferMatchResponse.self, from: data)

        XCTAssertEqual(response.itemCount, 2)
        XCTAssertEqual(response.offerCount, 2)
        XCTAssertEqual(response.matches.map(\.itemName), ["Mælk", "Rugbrød"])
    }

    private func decodePublication() throws -> OfferPublication {
        let data = Data(#"""
        {
          "id":"week-34",
          "retailer":"365discount",
          "title":"Uge 34",
          "valid_from":"14.08.2026",
          "valid_until":"20.08.2026",
          "status":"current",
          "source_url":"https://example.test",
          "page_count":1,
          "page_image_urls":["https://example.test/page.jpg"],
          "reader_url":"https://example.test",
          "reader_kind":"tjek-pages",
          "searchable":true
        }
        """#.utf8)
        return try JSONDecoder().decode(OfferPublication.self, from: data)
    }
}
