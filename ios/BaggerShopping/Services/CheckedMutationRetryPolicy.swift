import Foundation

enum CheckedMutationRetryPolicy {
    /// Bounded foreground retries. Pending operations remain on disk and are
    /// resumed on the next activation if iOS suspends the app in between.
    static let delays: [TimeInterval] = [2, 5, 10, 20]
}
