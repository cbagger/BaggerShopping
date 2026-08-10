import Foundation

struct CachedShoppingList: Codable {
    let savedAt: Date
    let list: ShoppingListResponse
}

enum ShoppingListCache {
    private static let key = "bagger-shopping-cached-list-v1"

    static func save(_ list: ShoppingListResponse) {
        let cached = CachedShoppingList(savedAt: Date(), list: list)
        guard let data = try? JSONEncoder().encode(cached) else { return }
        UserDefaults.standard.set(data, forKey: key)
    }

    static func load(maxAge: TimeInterval = 6 * 60 * 60) -> CachedShoppingList? {
        guard
            let data = UserDefaults.standard.data(forKey: key),
            let cached = try? JSONDecoder().decode(CachedShoppingList.self, from: data),
            Date().timeIntervalSince(cached.savedAt) <= maxAge
        else {
            return nil
        }

        return cached
    }
}
