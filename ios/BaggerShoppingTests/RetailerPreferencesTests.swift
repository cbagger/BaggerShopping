import XCTest
@testable import BaggerShopping

final class RetailerPreferencesTests: XCTestCase {
    private var suiteName: String!
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        suiteName = "RetailerPreferencesTests-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)!
        defaults.removePersistentDomain(forName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        defaults = nil
        suiteName = nil
        super.tearDown()
    }

    func testEveryRetailerIsEnabledByDefault() {
        let preferences = RetailerPreferences(defaults: defaults)

        XCTAssertEqual(preferences.enabledRetailers, RetailerCatalog.all)
        XCTAssertEqual(preferences.enabledCount, RetailerCatalog.all.count)
    }

    func testDisabledRetailerPersistsLocallyAndIsExcludedFromDiscovery() {
        let preferences = RetailerPreferences(defaults: defaults)
        preferences.setEnabled(false, for: "REMA 1000")

        XCTAssertFalse(preferences.isEnabled("REMA 1000"))
        XCTAssertFalse(preferences.effectiveRetailers(requested: []).contains("REMA 1000"))
        XCTAssertEqual(
            preferences.effectiveRetailers(requested: ["REMA 1000", "MENY"]),
            ["MENY"]
        )

        let reloaded = RetailerPreferences(defaults: defaults)
        XCTAssertFalse(reloaded.isEnabled("REMA 1000"))
        XCTAssertTrue(reloaded.isEnabled("MENY"))
    }

    func testAtLeastOneRetailerAlwaysRemainsEnabled() {
        let preferences = RetailerPreferences(defaults: defaults)
        for retailer in RetailerCatalog.all.dropLast() {
            preferences.setEnabled(false, for: retailer)
        }

        XCTAssertEqual(preferences.enabledCount, 1)
        let last = RetailerCatalog.all.last!
        preferences.setEnabled(false, for: last)

        XCTAssertEqual(preferences.enabledRetailers, [last])
    }

    func testRetailerPreferenceNeverTouchesSharedOfferMetadataCache() {
        let sentinel = Data("shared-offer-metadata".utf8)
        let offerMetadataKey = "bagger-shopping-offer-metadata-v2"
        defaults.set(sentinel, forKey: offerMetadataKey)

        let preferences = RetailerPreferences(defaults: defaults)
        preferences.setEnabled(false, for: "REMA 1000")

        XCTAssertEqual(defaults.data(forKey: offerMetadataKey), sentinel)
        XCTAssertEqual(
            defaults.stringArray(forKey: RetailerPreferences.storageKey),
            ["REMA 1000"]
        )
    }

    @MainActor
    func testDisabledRetailerIsExcludedFromPushWithoutErasingPushPreference() {
        let preferences = RetailerPreferences(defaults: defaults)
        let selectedPush: Set<String> = ["REMA 1000", "MENY", "Netto"]
        let serverRetailers = ["MENY", "REMA 1000", "Netto"]

        preferences.setEnabled(false, for: "REMA 1000")

        XCTAssertEqual(
            FlyerPushManager.activePushRetailers(
                selected: selectedPush,
                serverRetailers: serverRetailers,
                preferences: preferences
            ),
            ["MENY", "Netto"]
        )
        XCTAssertEqual(selectedPush, ["REMA 1000", "MENY", "Netto"])
    }

    @MainActor
    func testReenabledRetailerRestoresPreviousPushChoice() {
        let preferences = RetailerPreferences(defaults: defaults)
        let selectedPush: Set<String> = ["REMA 1000", "MENY"]
        let serverRetailers = ["MENY", "REMA 1000", "Netto"]

        preferences.setEnabled(false, for: "REMA 1000")
        XCTAssertEqual(
            FlyerPushManager.activePushRetailers(
                selected: selectedPush,
                serverRetailers: serverRetailers,
                preferences: preferences
            ),
            ["MENY"]
        )

        preferences.setEnabled(true, for: "REMA 1000")
        XCTAssertEqual(
            FlyerPushManager.activePushRetailers(
                selected: selectedPush,
                serverRetailers: serverRetailers,
                preferences: preferences
            ),
            ["MENY", "REMA 1000"]
        )
    }

    @MainActor
    func testPushSettingsOnlyExposeLocallyActiveRetailers() {
        let preferences = RetailerPreferences(defaults: defaults)
        preferences.setEnabled(false, for: "Kvickly")

        XCTAssertEqual(
            FlyerPushManager.availablePushRetailers(
                serverRetailers: ["MENY", "Kvickly", "Netto"],
                preferences: preferences
            ),
            ["MENY", "Netto"]
        )
    }
}
