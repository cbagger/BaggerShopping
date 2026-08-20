import Combine
import Foundation

final class RetailerPreferences: ObservableObject {
    static let shared = RetailerPreferences()
    static let storageKey = "kurv-disabled-retailers-v1"

    @Published private(set) var disabledRetailers: Set<String>

    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let stored = Set(defaults.stringArray(forKey: Self.storageKey) ?? [])
        disabledRetailers = Set(
            stored.compactMap(Self.canonicalRetailer)
        )
    }

    var enabledRetailers: [String] {
        RetailerCatalog.all.filter { !disabledRetailers.contains($0) }
    }

    var enabledCount: Int {
        enabledRetailers.count
    }

    func isEnabled(_ retailer: String) -> Bool {
        guard let canonical = Self.canonicalRetailer(retailer) else { return false }
        return !disabledRetailers.contains(canonical)
    }

    func canDisable(_ retailer: String) -> Bool {
        isEnabled(retailer) && enabledCount > 1
    }

    func setEnabled(_ enabled: Bool, for retailer: String) {
        guard let canonical = Self.canonicalRetailer(retailer) else { return }
        var next = disabledRetailers
        if enabled {
            next.remove(canonical)
        } else {
            guard canDisable(canonical) else { return }
            next.insert(canonical)
        }
        guard next != disabledRetailers else { return }
        disabledRetailers = next
        persist()
    }

    func enableAll() {
        guard !disabledRetailers.isEmpty else { return }
        disabledRetailers = []
        persist()
    }

    func effectiveRetailers(requested: [String]) -> [String] {
        let enabled = Set(enabledRetailers)
        guard !requested.isEmpty else { return enabledRetailers }
        let requestedCanonical = Set(requested.compactMap(Self.canonicalRetailer))
        return RetailerCatalog.all.filter {
            enabled.contains($0) && requestedCanonical.contains($0)
        }
    }

    private func persist() {
        defaults.set(
            RetailerCatalog.all.filter { disabledRetailers.contains($0) },
            forKey: Self.storageKey
        )
    }

    private static func canonicalRetailer(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return RetailerCatalog.all.first {
            $0.caseInsensitiveCompare(trimmed) == .orderedSame
        }
    }
}
