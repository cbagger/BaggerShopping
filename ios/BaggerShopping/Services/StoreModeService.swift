import Foundation

struct StoreVisitContext: Codable, Hashable, Identifiable {
    let id: String
    let retailer: String
    let address: String
    let latitude: Double
    let longitude: Double

    init(
        id: String,
        retailer: String,
        address: String = "",
        latitude: Double,
        longitude: Double
    ) {
        self.id = id
        self.retailer = RetailerCatalog.canonicalRetailer(retailer) ?? retailer.trimmingCharacters(in: .whitespacesAndNewlines)
        self.address = address.trimmingCharacters(in: .whitespacesAndNewlines)
        self.latitude = latitude
        self.longitude = longitude
    }

    init(store: StoreLocation) {
        self.init(
            id: "saved:\(store.id.uuidString.lowercased())",
            retailer: store.name,
            address: store.address,
            latitude: store.latitude,
            longitude: store.longitude
        )
    }

    static func automaticallyDetected(
        retailer: String,
        address: String,
        latitude: Double,
        longitude: Double
    ) -> StoreVisitContext {
        let latitudeKey = String(format: "%.5f", latitude)
        let longitudeKey = String(format: "%.5f", longitude)
        return StoreVisitContext(
            id: "automatic:\(ShoppingCategoryService.normalize(retailer)):\(latitudeKey):\(longitudeKey)",
            retailer: retailer,
            address: address,
            latitude: latitude,
            longitude: longitude
        )
    }

    var notificationUserInfo: [AnyHashable: Any] {
        [
            "route": "store-mode",
            "store_id": id,
            "retailer": retailer,
            "address": address,
            "latitude": latitude,
            "longitude": longitude,
        ]
    }

    static func fromNotificationUserInfo(_ userInfo: [AnyHashable: Any]) -> StoreVisitContext? {
        guard let retailer = userInfo["retailer"] as? String else { return nil }
        let id = userInfo["store_id"] as? String ?? "legacy:\(ShoppingCategoryService.normalize(retailer))"
        return StoreVisitContext(
            id: id,
            retailer: retailer,
            address: userInfo["address"] as? String ?? "",
            latitude: userInfo["latitude"] as? Double ?? 0,
            longitude: userInfo["longitude"] as? Double ?? 0
        )
    }
}

enum StoreModeService {
    static let defaultCategoryOrder: [ShoppingCategory] = [
        .fruitAndVegetables,
        .bakery,
        .pantry,
        .meat,
        .deli,
        .dairy,
        .frozen,
        .beverages,
        .household,
        .personalCare,
        .other,
    ]

    static func includes(assignedRetailer: String?, in context: StoreVisitContext) -> Bool {
        guard let assignedRetailer else { return true }
        let assigned = RetailerCatalog.canonicalRetailer(assignedRetailer) ?? assignedRetailer
        return assigned.caseInsensitiveCompare(context.retailer) == .orderedSame
    }

    static func defaultRank(for category: ShoppingCategory) -> Double {
        Double(defaultCategoryOrder.firstIndex(of: category) ?? defaultCategoryOrder.count)
    }

    static func nearbyStores(
        insideRegionIdentifiers: Set<String>,
        contextsByIdentifier: [String: StoreVisitContext]
    ) -> [StoreVisitContext] {
        let stores = insideRegionIdentifiers.compactMap { contextsByIdentifier[$0] }
        return Dictionary(grouping: stores, by: \.id)
            .compactMap { $0.value.first }
            .sorted { lhs, rhs in
                let retailerOrder = lhs.retailer.localizedCaseInsensitiveCompare(rhs.retailer)
                if retailerOrder != .orderedSame { return retailerOrder == .orderedAscending }
                return lhs.address.localizedCaseInsensitiveCompare(rhs.address) == .orderedAscending
            }
    }

    static func progress(remaining: Int, purchased: Int) -> StoreModeProgress {
        StoreModeProgress(
            remaining: max(remaining, 0),
            purchased: max(purchased, 0)
        )
    }

    static func compactAddress(_ address: String) -> String {
        let trimmed = address.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return "Denne butik" }

        guard let street = trimmed
            .split(separator: ",", maxSplits: 1, omittingEmptySubsequences: true)
            .first?
            .trimmingCharacters(in: .whitespacesAndNewlines),
              !street.isEmpty else { return trimmed }
        return street
    }
}

struct StoreModeProgress: Equatable {
    let remaining: Int
    let purchased: Int

    var total: Int { remaining + purchased }
    var completedFraction: Double {
        guard total > 0 else { return 0 }
        return Double(purchased) / Double(total)
    }
    var isComplete: Bool { total > 0 && remaining == 0 }
}

@MainActor
final class StoreLayoutLearning: ObservableObject {
    private struct CategoryStat: Codable {
        var averagePosition: Double
        var samples: Int
    }

    private struct StoreRecord: Codable {
        var categories: [String: CategoryStat]
    }

    private let defaults: UserDefaults
    private let storageKey: String
    private var records: [String: StoreRecord]
    private var sessionPositions: [String: Int] = [:]

    init(defaults: UserDefaults = .standard, storageKey: String = "kurv-store-layout-learning-v1") {
        self.defaults = defaults
        self.storageKey = storageKey
        if let data = defaults.data(forKey: storageKey),
           let decoded = try? JSONDecoder().decode([String: StoreRecord].self, from: data) {
            records = decoded
        } else {
            records = [:]
        }
    }

    func beginSession(for context: StoreVisitContext) {
        sessionPositions[context.id] = 0
    }

    func recordPurchased(category: ShoppingCategory, at context: StoreVisitContext) {
        let position = sessionPositions[context.id, default: 0]
        sessionPositions[context.id] = position + 1

        var record = records[context.id] ?? StoreRecord(categories: [:])
        let key = category.rawValue
        let previous = record.categories[key] ?? CategoryStat(
            averagePosition: StoreModeService.defaultRank(for: category),
            samples: 0
        )
        let samples = previous.samples + 1
        let average = ((previous.averagePosition * Double(previous.samples)) + Double(position)) / Double(samples)
        record.categories[key] = CategoryStat(averagePosition: average, samples: samples)
        records[context.id] = record
        save()
        objectWillChange.send()
    }

    func rank(for category: ShoppingCategory, at context: StoreVisitContext) -> Double {
        let fallback = StoreModeService.defaultRank(for: category)
        guard let stat = records[context.id]?.categories[category.rawValue] else { return fallback }

        // Three virtual default observations keep the route stable initially;
        // repeated real shopping trips gradually take ownership of the order.
        return ((fallback * 3) + (stat.averagePosition * Double(stat.samples))) / Double(3 + stat.samples)
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(records) else { return }
        defaults.set(data, forKey: storageKey)
    }
}
