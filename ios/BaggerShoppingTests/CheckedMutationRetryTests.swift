import XCTest
@testable import BaggerShopping

final class CheckedMutationRetryTests: XCTestCase {
    func testPatchRequestsRetryTransientFailures() {
        XCTAssertEqual(APIClient.maximumAttempts(for: "PATCH"), 3)
        XCTAssertEqual(APIClient.maximumAttempts(for: "patch"), 3)
    }

    func testNonIdempotentPostIsNotRetried() {
        XCTAssertEqual(APIClient.maximumAttempts(for: "POST"), 1)
    }

    func testForegroundRetryScheduleIsBoundedAndIncreasing() {
        XCTAssertEqual(CheckedMutationRetryPolicy.delays, [2, 5, 10, 20])
    }
}
