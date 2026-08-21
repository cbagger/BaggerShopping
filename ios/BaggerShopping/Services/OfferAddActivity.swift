import Foundation

@MainActor
final class OfferAddActivity: ObservableObject {
    enum Phase: Equatable {
        case idle
        case checking
        case adding
        case added

        var message: String? {
            switch self {
            case .idle: nil
            case .checking: "Tjekker billigere tilbud …"
            case .adding: "Tilføjer til indkøbslisten …"
            case .added: "Tilføjet"
            }
        }

        var showsProgress: Bool {
            self == .checking || self == .adding
        }

        var blocksNewAdditions: Bool {
            self == .checking || self == .adding
        }
    }

    static let shared = OfferAddActivity()

    @Published private(set) var phase: Phase = .idle
    private var clearTask: Task<Void, Never>?

    private init() {}

    func beginChecking() {
        clearTask?.cancel()
        phase = .checking
    }

    @discardableResult
    func tryBeginChecking() -> Bool {
        guard !phase.blocksNewAdditions else { return false }
        beginChecking()
        return true
    }

    func beginAdding() {
        clearTask?.cancel()
        phase = .adding
    }

    func markAdded() {
        clearTask?.cancel()
        phase = .added
        clearTask = Task { @MainActor [weak self] in
            try? await Task.sleep(for: .milliseconds(1100))
            guard !Task.isCancelled else { return }
            self?.phase = .idle
        }
    }

    func clear() {
        clearTask?.cancel()
        clearTask = nil
        phase = .idle
    }
}
