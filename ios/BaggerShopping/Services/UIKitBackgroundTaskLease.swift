import UIKit

/// Owns one UIApplication background-task identifier and balances it exactly
/// once, regardless of whether normal completion or expiration wins the race.
///
/// The factory also handles the rare case where UIKit invokes the expiration
/// handler synchronously before `beginBackgroundTask` returns its identifier.
@MainActor
final class UIKitBackgroundTaskLease {
    typealias Begin = @MainActor (
        _ name: String,
        _ expirationHandler: @escaping @MainActor @Sendable () -> Void
    ) -> UIBackgroundTaskIdentifier
    typealias End = @MainActor (_ identifier: UIBackgroundTaskIdentifier) -> Void

    private var identifier = UIBackgroundTaskIdentifier.invalid
    private var finishRequested = false
    private let end: End

    private init(end: @escaping End) {
        self.end = end
    }

    static func start(
        name: String,
        begin: @escaping Begin = { name, expirationHandler in
            UIApplication.shared.beginBackgroundTask(
                withName: name,
                expirationHandler: expirationHandler
            )
        },
        end: @escaping End = { identifier in
            UIApplication.shared.endBackgroundTask(identifier)
        }
    ) -> UIKitBackgroundTaskLease {
        let lease = UIKitBackgroundTaskLease(end: end)
        let identifier = begin(name) { [weak lease] in
            lease?.finish()
        }
        lease.identifier = identifier

        if lease.finishRequested {
            lease.finish()
        }
        return lease
    }

    func finish() {
        finishRequested = true
        guard identifier != .invalid else { return }

        // Invalidate before calling UIKit so re-entrancy cannot end the same
        // assertion twice.
        let identifierToEnd = identifier
        identifier = .invalid
        end(identifierToEnd)
    }
}
