import XCTest
@testable import BaggerShopping

final class OfferSearchRankerTests: XCTestCase {
    func testAPIClientRejectsWhitespaceOnlySearchBeforeNetworking() async {
        do {
            _ = try await APIClient().searchOffers(query: "   \n ")
            XCTFail("Expected an empty-search error")
        } catch APIClient.APIError.emptySearch {
            // Expected: no token or network access should be attempted.
        } catch {
            XCTFail("Unexpected error: \(error)")
        }
    }

    func testButterCoreMatchesRankAboveUnrelatedCompounds() {
        let exact = OfferSearchRanker.textConfidence(query: "Smør", candidate: "Lurpak Smør Saltet")
        let spreadable = OfferSearchRanker.textConfidence(query: "Smør", candidate: "Kærgården smørbar")
        let creamCheese = OfferSearchRanker.textConfidence(query: "Smør", candidate: "Smøreost natural")
        let openSandwich = OfferSearchRanker.textConfidence(query: "Smør", candidate: "Frisklavet luksus smørrebrød")

        XCTAssertGreaterThan(exact, spreadable)
        XCTAssertGreaterThan(spreadable, creamCheese)
        XCTAssertGreaterThan(spreadable, openSandwich)
    }

    func testFullBrandAndProductCoverageBeatsPartialProductOnlyMatch() {
        let full = OfferSearchRanker.textConfidence(query: "Lurpak smør", candidate: "Lurpak Smør Saltet")
        let partial = OfferSearchRanker.textConfidence(query: "Lurpak smør", candidate: "Salling Smørbar smør")

        XCTAssertGreaterThan(full, partial)
    }

    func testRankingIsGlobalInsteadOfPreservingRetailerOrder() throws {
        let offers = [
            try makeOffer(
                id: "bilka-cheese",
                retailer: "Bilka",
                productName: "Smøreost natural, cheese dippers eller ostesnack i strimler",
                price: 10,
                variants: ["Smøreost natural"]
            ),
            try makeOffer(
                id: "fotex-sandwich",
                retailer: "føtex",
                productName: "Frisklavet luksus smørrebrød",
                price: 35,
                variants: ["Frisklavet luksus smørrebrød"]
            ),
            try makeOffer(
                id: "meny-lurpak",
                retailer: "MENY",
                productName: "Lurpak",
                price: 18,
                variants: ["Lurpak Smør Saltet", "Lurpak Smørbar Saltet"]
            ),
            try makeOffer(
                id: "365-lurpak",
                retailer: "365discount",
                productName: "Lurpak smør eller smørbar",
                price: 20,
                variants: ["Lurpak smør", "Lurpak smørbar"]
            )
        ]

        let ranked = OfferSearchRanker.rank(offers, for: "Smør")
        let firstTwo = Set(ranked.prefix(2).map(\.id))

        XCTAssertEqual(firstTwo, Set(["meny-lurpak", "365-lurpak"]))
        XCTAssertTrue(ranked.suffix(2).allSatisfy { ["bilka-cheese", "fotex-sandwich"].contains($0.id) })
    }

    func testEqualRelevanceUsesPriceOnlyAsTieBreaker() throws {
        let expensive = try makeOffer(
            id: "expensive",
            retailer: "MENY",
            productName: "Smør",
            price: 20,
            variants: ["Smør"]
        )
        let cheap = try makeOffer(
            id: "cheap",
            retailer: "føtex",
            productName: "Smør",
            price: 10,
            variants: ["Smør"]
        )

        XCTAssertEqual(OfferSearchRanker.rank([expensive, cheap], for: "Smør").map(\.id), ["cheap", "expensive"])
    }

    func testFamilyFavoriteAlwaysRanksBeforeOtherRelevantOffers() throws {
        let cheapOther = try makeOffer(
            id: "heinz",
            retailer: "MENY",
            productName: "Heinz ketchup",
            price: 10,
            variants: ["Heinz ketchup"]
        )
        let favorite = try makeOffer(
            id: "beauvais",
            retailer: "Bilka",
            productName: "Beauvais ketchup 500 ml",
            price: 20,
            variants: ["Beauvais ketchup 500 ml"],
            familyFavoriteScore: 340
        )

        XCTAssertEqual(
            OfferSearchRanker.rank([cheapOther, favorite], for: "ketchup").map(\.id),
            ["beauvais", "heinz"]
        )
    }

    func testExactFavoritePackageOnlyBreaksTieInsideFavoriteGroup() throws {
        let relatedSize = try makeOffer(
            id: "beauvais-500",
            retailer: "MENY",
            productName: "Beauvais ketchup 500 ml",
            price: 10,
            variants: ["Beauvais ketchup 500 ml"],
            familyFavoriteScore: 340
        )
        let exactSize = try makeOffer(
            id: "beauvais-1000",
            retailer: "Bilka",
            productName: "Beauvais ketchup 1 kg",
            price: 20,
            variants: ["Beauvais ketchup 1 kg"],
            familyFavoriteScore: 365
        )

        XCTAssertEqual(
            OfferSearchRanker.rank([relatedSize, exactSize], for: "ketchup").map(\.id),
            ["beauvais-1000", "beauvais-500"]
        )
    }

    private func makeOffer(
        id: String,
        retailer: String,
        productName: String,
        price: Double,
        variants: [String],
        familyFavoriteScore: Int = 0
    ) throws -> GroceryOffer {
        let payload: [String: Any] = [
            "id": id,
            "retailer": retailer,
            "publication_id": "pub-\(retailer)",
            "publication_title": "Aktuel avis",
            "product_name": productName,
            "price": price,
            "source_url": "https://example.com/offer",
            "raw_text": "",
            "safe_to_add": true,
            "family_favorite_score": familyFavoriteScore,
            "variants": variants.enumerated().map { index, name in
                [
                    "id": "\(id)-\(index)",
                    "name": name,
                    "matches_query": true
                ] as [String: Any]
            }
        ]
        let data = try JSONSerialization.data(withJSONObject: payload)
        return try JSONDecoder().decode(GroceryOffer.self, from: data)
    }
}
