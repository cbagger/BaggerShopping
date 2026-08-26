import XCTest
import UIKit
@testable import BaggerShopping

@MainActor
final class UIKitBackgroundTaskLeaseTests: XCTestCase {
    private let identifier = UIBackgroundTaskIdentifier(rawValue: 76)

    func testNormalCompletionEndsIdentifierExactlyOnce() {
        var ended: [UIBackgroundTaskIdentifier] = []
        let lease = UIKitBackgroundTaskLease.start(
            name: "test",
            begin: { _, _ in self.identifier },
            end: { ended.append($0) }
        )

        lease.finish()
        lease.finish()

        XCTAssertEqual(ended, [identifier])
    }

    func testExpirationAndNormalCompletionCannotDoubleEnd() throws {
        var expiration: (@MainActor @Sendable () -> Void)?
        var ended: [UIBackgroundTaskIdentifier] = []
        let lease = UIKitBackgroundTaskLease.start(
            name: "test",
            begin: { _, handler in
                expiration = handler
                return self.identifier
            },
            end: { ended.append($0) }
        )

        try XCTUnwrap(expiration)()
        lease.finish()

        XCTAssertEqual(ended, [identifier])
    }

    func testSynchronousExpirationBeforeBeginReturnsStillEndsOnce() {
        var ended: [UIBackgroundTaskIdentifier] = []
        let lease = UIKitBackgroundTaskLease.start(
            name: "test",
            begin: { _, expiration in
                expiration()
                return self.identifier
            },
            end: { ended.append($0) }
        )

        lease.finish()

        XCTAssertEqual(ended, [identifier])
    }
}
