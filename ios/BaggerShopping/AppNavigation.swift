import Foundation

enum AppTab: Hashable {
    case shoppingList
    case offers
    case flyers
    case stores
    case settings
}

struct ShoppingListRoute: Equatable {
    let id = UUID()
    let store: StoreVisitContext
}

struct FlyerRoute: Equatable {
    let id = UUID()
    let publicationID: String
    let retailer: String?
}

extension Notification.Name {
    static let openShoppingListRetailer = Notification.Name("kurv.openShoppingListRetailer")
    static let openStoreMode = Notification.Name("kurv.openStoreMode")
    static let openFlyerPublication = Notification.Name("kurv.openFlyerPublication")
}

@MainActor
final class AppNavigation: ObservableObject {
    private static let inactivityResetInterval: TimeInterval = 30 * 60

    @Published var selectedTab: AppTab = .shoppingList
    @Published private(set) var rootResetID = UUID()
    @Published private(set) var shoppingListRoute: ShoppingListRoute?
    @Published private(set) var flyerRoute: FlyerRoute?
    private var backgroundedAt: Date?
    private var lastExternalNavigationAt: Date?

    init() {
        NotificationCenter.default.addObserver(
            forName: .openStoreMode,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            guard let store = notification.object as? StoreVisitContext else { return }
            Task { @MainActor in self?.openStoreMode(store) }
        }
        NotificationCenter.default.addObserver(
            forName: .openShoppingListRetailer,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            guard let retailer = notification.object as? String else { return }
            Task { @MainActor in self?.openShoppingList(retailer: retailer) }
        }
        NotificationCenter.default.addObserver(
            forName: .openFlyerPublication,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            guard let payload = notification.object as? [String: String],
                  let publicationID = payload["publication_id"] else { return }
            Task { @MainActor in
                self?.openFlyer(publicationID: publicationID, retailer: payload["retailer"])
            }
        }
    }

    func openShoppingList(retailer: String) {
        let trimmed = retailer.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        openStoreMode(StoreVisitContext(
            id: "legacy:\(ShoppingCategoryService.normalize(trimmed))",
            retailer: trimmed,
            latitude: 0,
            longitude: 0
        ))
    }

    func openStoreMode(_ store: StoreVisitContext) {
        guard !store.retailer.isEmpty else { return }
        selectedTab = .shoppingList
        lastExternalNavigationAt = Date()
        shoppingListRoute = ShoppingListRoute(store: store)
    }

    func endStoreMode() {
        shoppingListRoute = nil
    }

    func openFlyer(publicationID: String, retailer: String? = nil) {
        let trimmed = publicationID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        selectedTab = .flyers
        lastExternalNavigationAt = Date()
        flyerRoute = FlyerRoute(publicationID: trimmed, retailer: retailer)
    }

    func didEnterBackground(at date: Date = Date()) {
        backgroundedAt = date
    }

    @discardableResult
    func resetAfterLongInactivityIfNeeded(at date: Date = Date()) -> Bool {
        guard let backgroundedAt,
              date.timeIntervalSince(backgroundedAt) >= Self.inactivityResetInterval else {
            return false
        }

        self.backgroundedAt = nil
        if let lastExternalNavigationAt,
           date.timeIntervalSince(lastExternalNavigationAt) < 10 {
            return false
        }

        selectedTab = .shoppingList
        shoppingListRoute = nil
        flyerRoute = nil
        rootResetID = UUID()
        return true
    }
}
