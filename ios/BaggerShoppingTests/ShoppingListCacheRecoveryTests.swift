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

    func testCheckedMutationIsDurableAndOverlaysAStaleServerList() throws {
        let suite = "PendingCheckedMutationTests-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defaults.removePersistentDomain(forName: suite)
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = PendingCheckedMutationStore(defaults: defaults, storageKey: "pending")
        let item = ShoppingItem(id: "milk-1", name: "Mælk", checked: false)
        let staleList = ShoppingListResponse(
            ok: true,
            name: "Familieliste",
            count: 1,
            hasItems: true,
            items: [item]
        )

        XCTAssertNotNil(store.enqueue(item: item, checked: true))

        let restoredStore = PendingCheckedMutationStore(defaults: defaults, storageKey: "pending")
        let overlaid = restoredStore.applying(to: staleList)
        XCTAssertTrue(try XCTUnwrap(overlaid.items.first).checked)
        XCTAssertEqual(restoredStore.ordered().map(\.itemID), ["milk-1"])
    }

    func testNewestCheckedIntentWinsAndOldAcknowledgementCannotRemoveIt() throws {
        let suite = "PendingCheckedMutationTests-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defaults.removePersistentDomain(forName: suite)
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = PendingCheckedMutationStore(defaults: defaults, storageKey: "pending")
        let item = ShoppingItem(id: "milk-1", name: "Mælk", checked: false)
        let first = try XCTUnwrap(store.enqueue(
            item: item,
            checked: true,
            updatedAt: Date(timeIntervalSince1970: 1)
        ))
        _ = store.enqueue(
            item: item,
            checked: false,
            updatedAt: Date(timeIntervalSince1970: 2)
        )

        store.removeIfCurrent(first)

        XCTAssertEqual(store.ordered().map(\.checked), [false])
    }

    func testAcknowledgedCheckStaysOverlaidUntilSamsungReadCatchesUp() throws {
        let suite = "PendingCheckedMutationTests-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defaults.removePersistentDomain(forName: suite)
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = PendingCheckedMutationStore(defaults: defaults, storageKey: "pending")
        let item = ShoppingItem(id: "milk-1", name: "Mælk", checked: false)
        let mutation = try XCTUnwrap(store.enqueue(item: item, checked: true))
        store.markAcknowledgedIfCurrent(mutation)
        let stale = ShoppingListResponse(ok: true, name: "Familieliste", count: 1, hasItems: true, items: [item])

        XCTAssertTrue(try XCTUnwrap(store.applying(to: stale).items.first).checked)
        XCTAssertTrue(store.ordered().isEmpty)

        var confirmedItem = item
        confirmedItem.checked = true
        let confirmed = ShoppingListResponse(
            ok: true,
            name: "Familieliste",
            count: 1,
            hasItems: true,
            items: [confirmedItem]
        )
        store.reconcileAcknowledged(with: confirmed)

        XCTAssertTrue(store.load().isEmpty)
    }

    func testDeletingItemSuspendsPendingCheckSoItCannotRestoreTheRow() throws {
        let suite = "PendingCheckedMutationTests-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defaults.removePersistentDomain(forName: suite)
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = PendingCheckedMutationStore(defaults: defaults, storageKey: "pending")
        let item = ShoppingItem(id: "milk-1", name: "Mælk", checked: false)
        let stale = ShoppingListResponse(
            ok: true,
            name: "Familieliste",
            count: 1,
            hasItems: true,
            items: [item]
        )
        _ = store.enqueue(item: item, checked: true)

        let suspended = try XCTUnwrap(store.remove(itemID: "milk-1"))

        XCTAssertTrue(store.load().isEmpty)
        XCTAssertFalse(try XCTUnwrap(store.applying(to: stale).items.first).checked)

        store.restore(suspended)
        XCTAssertTrue(try XCTUnwrap(store.applying(to: stale).items.first).checked)
    }
}
