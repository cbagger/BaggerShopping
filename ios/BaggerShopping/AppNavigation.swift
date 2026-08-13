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

extension Notification.Name {
    static let openShoppingListRetailer = Notification.Name("kurv.openShoppingListRetailer")
}

@MainActor
final class AppNavigation: ObservableObject {
    @Published var selectedTab: AppTab = .shoppingList
    @Published private(set) var shoppingListRoute: ShoppingListRoute?

    init() {
        NotificationCenter.default.addObserver(
            forName: .openShoppingListRetailer,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            guard let retailer = notification.object as? String else { return }
            Task { @MainActor in self?.openShoppingList(retailer: retailer) }
        }
    }

    func openShoppingList(retailer: String) {
        let trimmed = retailer.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        selectedTab = .shoppingList
        shoppingListRoute = ShoppingListRoute(retailer: trimmed)
    }
}
