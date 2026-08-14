import XCTest
@testable import BaggerShopping

@MainActor
final class AppNavigationTests: XCTestCase {
    func testShortBackgroundPausePreservesSelectedTab() {
        let navigation = AppNavigation()
        navigation.selectedTab = .settings
        let start = Date(timeIntervalSince1970: 1_000)

        navigation.didEnterBackground(at: start)

        XCTAssertFalse(navigation.resetAfterLongInactivityIfNeeded(at: start.addingTimeInterval(60)))
        XCTAssertEqual(navigation.selectedTab, .settings)
    }

    func testLongBackgroundPauseReturnsToShoppingList() {
        let navigation = AppNavigation()
        navigation.selectedTab = .settings
        let originalRoot = navigation.rootResetID
        let start = Date(timeIntervalSince1970: 1_000)

        navigation.didEnterBackground(at: start)

        XCTAssertTrue(navigation.resetAfterLongInactivityIfNeeded(at: start.addingTimeInterval(30 * 60)))
        XCTAssertEqual(navigation.selectedTab, .shoppingList)
        XCTAssertNotEqual(navigation.rootResetID, originalRoot)
    }
}
