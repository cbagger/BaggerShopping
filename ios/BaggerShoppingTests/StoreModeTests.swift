import XCTest
@testable import BaggerShopping

final class StoreModeTests: XCTestCase {
    func testStoreModeIncludesCurrentRetailerAndUnassignedButNotOtherStores() {
        let store = StoreVisitContext(
            id: "netto-skoring",
            retailer: "Netto",
            address: "Jyllandsgade 12, 9520 Skørping",
            latitude: 56.84,
            longitude: 9.89
        )

        XCTAssertTrue(StoreModeService.includes(assignedRetailer: "Netto", in: store))
        XCTAssertTrue(StoreModeService.includes(assignedRetailer: nil, in: store))
        XCTAssertFalse(StoreModeService.includes(assignedRetailer: "MENY", in: store))
    }

    func testDefaultWalkingRouteMatchesRequestedStoreFlow() {
        XCTAssertEqual(StoreModeService.defaultCategoryOrder, [
            .fruitAndVegetables,
            .bakery,
            .pantry,
            .meat,
            .deli,
            .dairy,
            .frozen,
            .beverages,
            .household,
            .personalCare,
            .other,
        ])
    }

    func testSameRetailerAtTwoAddressesHasSeparateIdentity() {
        let first = StoreVisitContext.automaticallyDetected(
            retailer: "Netto",
            address: "Jyllandsgade 12, Skørping",
            latitude: 56.836,
            longitude: 9.891
        )
        let second = StoreVisitContext.automaticallyDetected(
            retailer: "Netto",
            address: "Hobrovej 450, Aalborg",
            latitude: 57.001,
            longitude: 9.910
        )

        XCTAssertNotEqual(first.id, second.id)
    }

    func testOverlappingGeofencesExposeEveryNearbyStoreForSelection() {
        let netto = StoreVisitContext(
            id: "saved:netto",
            retailer: "Netto",
            address: "Jyllandsgade 12, Skørping",
            latitude: 56.836,
            longitude: 9.891
        )
        let rema = StoreVisitContext(
            id: "saved:rema",
            retailer: "REMA 1000",
            address: "Jyllandsgade 14, Skørping",
            latitude: 56.8361,
            longitude: 9.8912
        )
        let contexts = [
            "store:netto": netto,
            "store:rema": rema,
        ]

        XCTAssertEqual(
            StoreModeService.nearbyStores(
                insideRegionIdentifiers: ["store:netto", "store:rema"],
                contextsByIdentifier: contexts
            ),
            [netto, rema]
        )
        XCTAssertEqual(
            StoreModeService.nearbyStores(
                insideRegionIdentifiers: ["store:rema"],
                contextsByIdentifier: contexts
            ),
            [rema]
        )
    }

    func testStoreModeProgressTracksRemainingAndPurchasedItems() {
        let underway = StoreModeService.progress(remaining: 4, purchased: 2)
        XCTAssertEqual(underway.total, 6)
        XCTAssertEqual(underway.completedFraction, 1.0 / 3.0, accuracy: 0.0001)
        XCTAssertFalse(underway.isComplete)

        let complete = StoreModeService.progress(remaining: 0, purchased: 6)
        XCTAssertEqual(complete.completedFraction, 1)
        XCTAssertTrue(complete.isComplete)
    }

    func testStoreModeUsesACompactReadableAddress() {
        XCTAssertEqual(
            StoreModeService.compactAddress("Skørping Center 16, 9520 Skørping, Danmark"),
            "Skørping Center 16"
        )
        XCTAssertEqual(StoreModeService.compactAddress("Jyllandsgade 12"), "Jyllandsgade 12")
        XCTAssertEqual(StoreModeService.compactAddress("   "), "Denne butik")
    }

    @MainActor
    func testLayoutLearningIsScopedToExactPhysicalStore() {
        let suite = "StoreModeTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defaults.removePersistentDomain(forName: suite)
        defer { defaults.removePersistentDomain(forName: suite) }

        let learning = StoreLayoutLearning(defaults: defaults, storageKey: "layout")
        let first = StoreVisitContext(
            id: "netto-skoring",
            retailer: "Netto",
            latitude: 56.836,
            longitude: 9.891
        )
        let second = StoreVisitContext(
            id: "netto-aalborg",
            retailer: "Netto",
            latitude: 57.001,
            longitude: 9.910
        )

        learning.beginSession(for: first)
        learning.recordPurchased(category: .household, at: first)

        XCTAssertLessThan(
            learning.rank(for: .household, at: first),
            learning.rank(for: .household, at: second)
        )
        XCTAssertEqual(
            learning.rank(for: .household, at: second),
            StoreModeService.defaultRank(for: .household)
        )
    }

    func testNotificationPayloadRestoresExactStoreContext() throws {
        let original = StoreVisitContext(
            id: "saved:123",
            retailer: "REMA 1000",
            address: "Himmerlandsvej 112, 9520 Skørping",
            latitude: 56.84,
            longitude: 9.89
        )

        let restored = try XCTUnwrap(
            StoreVisitContext.fromNotificationUserInfo(original.notificationUserInfo)
        )

        XCTAssertEqual(restored, original)
    }

    func testRetailerCatalogRecognizesBranchNames() {
        XCTAssertEqual(RetailerCatalog.canonicalRetailer("Netto Skørping"), "Netto")
        XCTAssertEqual(RetailerCatalog.canonicalRetailer("REMA 1000 Aalborg SV"), "REMA 1000")
        XCTAssertNil(RetailerCatalog.canonicalRetailer("Lokal blomsterbutik"))
    }

    func testActiveStoreSessionSurvivesRelaunchWithPurchasedItems() throws {
        let suite = "StoreModeSessionTests-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defaults.removePersistentDomain(forName: suite)
        defer { defaults.removePersistentDomain(forName: suite) }
        let storage = StoreModeSessionStore(defaults: defaults, storageKey: "session")
        let meny = StoreVisitContext(
            id: "saved:meny",
            retailer: "MENY",
            address: "Skørping Center 16",
            latitude: 56.84,
            longitude: 9.89
        )

        storage.save(store: meny, purchasedItemIDs: ["milk-1", "milk-1", "bread-1"])

        let restored = try XCTUnwrap(storage.load())
        XCTAssertEqual(restored.store, meny)
        XCTAssertEqual(restored.purchasedItemIDs, ["milk-1", "bread-1"])
    }

    func testExpiredStoreSessionDoesNotRestartAnOldTrip() throws {
        let suite = "StoreModeSessionTests-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defaults.removePersistentDomain(forName: suite)
        defer { defaults.removePersistentDomain(forName: suite) }
        let storage = StoreModeSessionStore(defaults: defaults, storageKey: "session")
        let store = StoreVisitContext(id: "saved:meny", retailer: "MENY", latitude: 56.84, longitude: 9.89)
        storage.save(store: store, purchasedItemIDs: [])

        XCTAssertNil(storage.load(maxAge: 60, now: Date().addingTimeInterval(61)))
    }
}
