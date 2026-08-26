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

    func testStaleShoppingItemConflictIsNotTransient() {
        let error = APIClient.APIError.server(409, "Shopping item not found")

        XCTAssertTrue(APIClient.isStaleShoppingItem(error))
    }

    func testOrdinaryGatewayFailureIsNotTreatedAsStaleItem() {
        let error = APIClient.APIError.server(502, "Gateway timeout")

        XCTAssertFalse(APIClient.isStaleShoppingItem(error))
    }
}
