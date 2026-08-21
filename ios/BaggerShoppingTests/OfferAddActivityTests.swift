import XCTest
@testable import BaggerShopping

final class OfferAddActivityTests: XCTestCase {
    @MainActor
    func testOfferAddActivityTransitionsAreImmediate() {
        let activity = OfferAddActivity.shared
        activity.clear()

        activity.beginChecking()
        XCTAssertEqual(activity.phase, .checking)
        XCTAssertEqual(activity.phase.message, "Tjekker billigere tilbud …")
        XCTAssertTrue(activity.phase.showsProgress)

        activity.beginAdding()
        XCTAssertEqual(activity.phase, .adding)
        XCTAssertEqual(activity.phase.message, "Tilføjer til indkøbslisten …")
        XCTAssertTrue(activity.phase.showsProgress)

        activity.markAdded()
        XCTAssertEqual(activity.phase, .added)
        XCTAssertEqual(activity.phase.message, "Tilføjet")
        XCTAssertFalse(activity.phase.showsProgress)

        activity.clear()
        XCTAssertEqual(activity.phase, .idle)
        XCTAssertNil(activity.phase.message)
    }

    @MainActor
    func testSecondOfferAdditionIsRejectedUntilFirstFinishes() {
        let activity = OfferAddActivity.shared
        activity.clear()

        XCTAssertTrue(activity.tryBeginChecking())
        XCTAssertFalse(activity.tryBeginChecking())

        activity.beginAdding()
        XCTAssertFalse(activity.tryBeginChecking())

        activity.clear()
        XCTAssertTrue(activity.tryBeginChecking())
        activity.clear()
    }

    @MainActor
    func testAppModelAllowsOnlyOneSamsungAddWriteAtATime() {
        let model = AppModel()

        XCTAssertTrue(model.beginItemAddition())
        XCTAssertTrue(model.isAddingItem)
        XCTAssertFalse(model.beginItemAddition())

        model.finishItemAddition()
        XCTAssertFalse(model.isAddingItem)
        XCTAssertTrue(model.beginItemAddition())
        model.finishItemAddition()
    }
}
