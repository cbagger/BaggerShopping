import XCTest
@testable import BaggerShopping

final class GeofenceRoutingTests: XCTestCase {
    func testStoreNotificationCountsOnlyUncheckedItemsAssignedToStore() {
        let list = ShoppingListResponse(
            ok: true,
            name: "Familien",
            count: 4,
            hasItems: true,
            items: [
                ShoppingItem(id: "1", name: "Smør", checked: false),
                ShoppingItem(id: "2", name: "Mælk", checked: false),
                ShoppingItem(id: "3", name: "Bleer", checked: false),
                ShoppingItem(id: "4", name: "Juice", checked: true),
            ]
        )
        let metadata = [
            metadata(item: "Smør", retailer: "MENY"),
            metadata(item: "Mælk", retailer: "meny"),
            metadata(item: "Juice", retailer: "MENY"),
            metadata(item: "Bleer", retailer: "Netto"),
        ]

        XCTAssertEqual(GeofenceManager.items(for: "MENY", in: list, metadata: metadata).map(\.name), ["Smør", "Mælk"])
        XCTAssertEqual(GeofenceManager.unassignedItems(in: list, metadata: metadata), [])
    }

    func testUnassignedItemsRemainRelevantWhenStoreHasNoDedicatedItems() {
        let list = ShoppingListResponse(
            ok: true,
            name: "Familien",
            count: 3,
            hasItems: true,
            items: [
                ShoppingItem(id: "1", name: "Bananer", checked: false),
                ShoppingItem(id: "2", name: "Toiletpapir", checked: false),
                ShoppingItem(id: "3", name: "Mælk", checked: true),
            ]
        )

        XCTAssertTrue(GeofenceManager.items(for: "MENY", in: list, metadata: []).isEmpty)
        XCTAssertEqual(
            GeofenceManager.unassignedItems(in: list, metadata: []).map(\.name),
            ["Bananer", "Toiletpapir"]
        )
    }

    private func metadata(item: String, retailer: String) -> OfferMetadataDTO {
        OfferMetadataDTO(
            itemName: item,
            retailer: retailer,
            price: nil,
            validFrom: nil,
            validUntil: nil,
            offerID: nil,
            publicationID: nil,
            matchedItemName: nil
        )
    }
}
