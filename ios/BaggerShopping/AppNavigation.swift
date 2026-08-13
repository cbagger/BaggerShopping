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
    let retailer: String
}

struct FlyerRoute: Equatable {
    let id = UUID()
    let publicationID: String
    let retailer: String?
}

extension Notification.Name {
    static let openShoppingListRetailer = Notification.Name("kurv.openShoppingListRetailer")
    static let openFlyerPublication = Notification.Name("kurv.openFlyerPublication")
}

@MainActor
final class AppNavigation: ObservableObject {
    @Published var selectedTab: AppTab = .shoppingList
    @Published private(set) var shoppingListRoute: ShoppingListRoute?
    @Published private(set) var flyerRoute: FlyerRoute?

    init() {
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
        selectedTab = .shoppingList
        shoppingListRoute = ShoppingListRoute(retailer: trimmed)
    }

    func openFlyer(publicationID: String, retailer: String? = nil) {
        let trimmed = publicationID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        selectedTab = .flyers
        flyerRoute = FlyerRoute(publicationID: trimmed, retailer: retailer)
    }
}
