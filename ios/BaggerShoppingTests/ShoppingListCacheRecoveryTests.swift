import XCTest
@testable import BaggerShopping

final class ShoppingListCacheRecoveryTests: XCTestCase {
    func testCachedListCanRestoreAfterDeletionMutation() throws {
        let remaining = ShoppingItem(
            id: "milk-1",
            name: "Mælk",
            checked: false,
            quantity: nil,
            unit: nil
        )
        let list = ShoppingListResponse(
            ok: true,
            name: "Familieliste",
            count: 1,
            hasItems: true,
            items: [remaining]
        )

        ShoppingListCache.save(list)

        let cached = try XCTUnwrap(ShoppingListCache.load(maxAge: 60))
        XCTAssertEqual(cached.list.count, 1)
        XCTAssertEqual(cached.list.items.map(\.name), ["Mælk"])
        XCTAssertFalse(cached.list.items.contains { $0.name == "Coca-Cola" })
    }

    func testEmptyPostDeletionListStillRestoresAsValidCache() throws {
        let list = ShoppingListResponse(
            ok: true,
            name: "Familieliste",
            count: 0,
            hasItems: false,
            items: []
        )

        ShoppingListCache.save(list)

        let cached = try XCTUnwrap(ShoppingListCache.load(maxAge: 60))
        XCTAssertEqual(cached.list.count, 0)
        XCTAssertFalse(cached.list.hasItems)
        XCTAssertTrue(cached.list.items.isEmpty)
    }
}
