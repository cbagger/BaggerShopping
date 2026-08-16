import XCTest
@testable import BaggerShopping

final class MemberPriceTests: XCTestCase {
    override func setUp() {
        super.setUp()
        MemberPricePresence.clear()
    }

    override func tearDown() {
        MemberPricePresence.clear()
        super.tearDown()
    }

    func testOfferKeepsOrdinaryAndMemberPriceSeparate() throws {
        let offer = try decodeOffer(
            id: "meny-member",
            retailer: "MENY",
            productName: "Pågen gifflar",
            price: 16,
            memberPrice: 9.95,
            label: "MENY medlemspris",
            app: "MENY-appen"
        )

        XCTAssertEqual(offer.price, 16)
        XCTAssertEqual(offer.memberPrice, 9.95)
        XCTAssertEqual(offer.memberPriceDisplayLabel, "MENY medlemspris")
        XCTAssertEqual(offer.lowestListedPrice, 9.95)
        XCTAssertTrue(offer.lowestListedPriceRequiresMembership)
    }

    func testPriceGuardCanSurfaceCheaperMemberPriceForSameItem() throws {
        let selected = try decodeOffer(
            id: "selected",
            retailer: "MENY",
            productName: "Innocent juice",
            price: 20
        )
        let candidate = try decodeOffer(
            id: "candidate",
            retailer: "Lidl",
            productName: "Innocent juice",
            price: 22,
            memberPrice: 15,
            label: "Lidl Plus",
            app: "Lidl Plus"
        )

        let result = OfferPriceGuard().cheaperOffers(
            from: [selected, candidate],
            for: "Innocent juice",
            than: selected
        )

        XCTAssertEqual(result.map(\.id), ["candidate"])
        XCTAssertEqual(result.first?.price, 22)
        XCTAssertEqual(result.first?.memberPrice, 15)
    }

    func testShoppingListReminderRequiresConfirmedPresence() throws {
        let fixture = try reminderFixture(retailer: "Lidl", app: "Lidl Plus")

        XCTAssertNil(
            MemberPriceReminder.message(
                retailer: "Lidl",
                storeItems: [fixture.item],
                metadata: [fixture.metadata],
                now: fixture.now
            )
        )

        MemberPricePresence.setInside(true, storeName: "Lidl Skørping")
        XCTAssertEqual(
            MemberPriceReminder.message(
                retailer: "Lidl",
                storeItems: [fixture.item],
                metadata: [fixture.metadata],
                now: fixture.now
            ),
            "Husk at aktivere tilbuddet i Lidl Plus."
        )

        MemberPricePresence.setInside(false, storeName: "Lidl Skørping")
        XCTAssertNil(
            MemberPriceReminder.message(
                retailer: "Lidl",
                storeItems: [fixture.item],
                metadata: [fixture.metadata],
                now: fixture.now
            )
        )
    }

    func testPresenceAtAnotherRetailerNeverShowsReminder() throws {
        let fixture = try reminderFixture(retailer: "føtex", app: "føtex Plus")
        MemberPricePresence.setInside(true, storeName: "MENY Skørping")

        XCTAssertNil(
            MemberPriceReminder.message(
                retailer: "føtex",
                storeItems: [fixture.item],
                metadata: [fixture.metadata],
                now: fixture.now
            )
        )
    }

    func testMemberPriceReminderIsNeverAddedToGeofenceNotification() throws {
        let fixture = try reminderFixture(retailer: "MENY", app: "MENY-appen")
        MemberPricePresence.setInside(true, storeName: "MENY Skørping")

        XCTAssertNil(
            MemberPriceGeofenceReminder.message(
                retailer: "MENY",
                storeItems: [fixture.item],
                metadata: [fixture.metadata]
            )
        )
    }

    private func reminderFixture(
        retailer: String,
        app: String
    ) throws -> (item: ShoppingItem, metadata: OfferMetadataDTO, now: Date) {
        let itemName = "Testvare"
        let offer = try decodeOffer(
            id: "member-\(retailer)",
            retailer: retailer,
            productName: itemName,
            price: 20,
            memberPrice: 15,
            label: app,
            app: app
        )
        let metadata = OfferMetadataDTO(
            itemName: itemName,
            retailer: retailer,
            price: 20,
            validFrom: "14.08.2026",
            validUntil: "20.08.2026",
            offerID: offer.id,
            publicationID: offer.publicationID,
            matchedItemName: itemName,
            offerSnapshot: offer
        )
        let item = ShoppingItem(
            id: "item-1",
            name: itemName,
            checked: false,
            quantity: nil,
            unit: nil
        )
        let now = Calendar(identifier: .gregorian).date(
            from: DateComponents(year: 2026, month: 8, day: 16)
        )!
        return (item, metadata, now)
    }

    private func decodeOffer(
        id: String,
        retailer: String,
        productName: String,
        price: Double? = nil,
        memberPrice: Double? = nil,
        label: String? = nil,
        app: String? = nil
    ) throws -> GroceryOffer {
        var payload: [String: Any] = [
            "id": id,
            "retailer": retailer,
            "publication_id": "publication-\(id)",
            "publication_title": "Uge 34",
            "product_name": productName,
            "source_url": "https://example.test",
            "raw_text": productName,
            "safe_to_add": true,
            "variants": []
        ]
        if let price { payload["price"] = price }
        if let memberPrice {
            payload["member_price"] = memberPrice
            payload["member_price_requires_activation"] = true
        }
        if let label { payload["member_price_label"] = label }
        if let app { payload["member_price_app"] = app }

        return try JSONDecoder().decode(
            GroceryOffer.self,
            from: JSONSerialization.data(withJSONObject: payload)
        )
    }
}
