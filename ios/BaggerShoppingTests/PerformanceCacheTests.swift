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

    func testFlyerPublicationCacheDoesNotRestoreRetiredRetailer() throws {
        let active = try decodePublication()
        let retired = try decodePublication(retailer: "Kvickly", id: "shared-kvickly-week")
        FlyerPublicationCache.save([active, retired])

        let cached = try XCTUnwrap(FlyerPublicationCache.load(maxAge: 60))

        XCTAssertEqual(cached.publications.map(\.id), ["week-34"])
    }

    func testOfferRetailerShelfUsesCachedCurrentAndUpcomingPublications() throws {
        let current = try decodePublication(retailer: "365discount", id: "current")
        let upcomingData = Data(#"""
        {
          "id":"upcoming",
          "retailer":"MENY",
          "title":"Næste uge",
          "valid_from":"21.08.2026",
          "valid_until":"27.08.2026",
          "status":"upcoming",
          "source_url":"https://example.test",
          "page_count":1,
          "page_image_urls":["https://example.test/page.jpg"],
          "reader_url":"https://example.test",
          "reader_kind":"tjek-pages",
          "searchable":true
        }
        """#.utf8)
        let upcoming = try JSONDecoder().decode(OfferPublication.self, from: upcomingData)

        XCTAssertEqual(
            OfferRetailerShelf.retailers(from: [current, upcoming, current]),
            ["365discount", "MENY"]
        )
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

    private func decodePublication(
        retailer: String = "365discount",
        id: String = "week-34"
    ) throws -> OfferPublication {
        let data = Data("""
        {
          "id":"\(id)",
          "retailer":"\(retailer)",
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
        """.utf8)
        return try JSONDecoder().decode(OfferPublication.self, from: data)
    }
}
