import Foundation

@MainActor
final class AppModel: ObservableObject {
    @Published var shoppingList: ShoppingListResponse?
    @Published var isLoading = false
    @Published var errorMessage: String?
    @Published var tokenConfigured = KeychainStore.loadToken() != nil
    @Published var mutatingItemIDs: Set<String> = []

    let stores = StoreRepository()
    let geofence = GeofenceManager()
    let categories = ShoppingCategoryService()
    private let api = APIClient()

    func bootstrap() async {
        geofence.sync(stores: stores.stores)
        if tokenConfigured { await refresh() }
    }

    func refresh() async {
        guard tokenConfigured else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            let list = try await api.fetchList()
            shoppingList = list
            ShoppingListCache.save(list)
            errorMessage = nil
        }
        catch { errorMessage = error.localizedDescription }
    }

    func addItem(_ name: String) async -> Bool {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return false }
        do { try await api.addItem(name: trimmed); await refresh(); return true }
        catch { errorMessage = error.localizedDescription; return false }
    }

    func setChecked(_ item: ShoppingItem, checked: Bool) async {
        let key = item.stableID
        mutatingItemIDs.insert(key)
        defer { mutatingItemIDs.remove(key) }
        do { try await api.setChecked(item: item, checked: checked); await refresh() }
        catch { errorMessage = error.localizedDescription }
    }

    func deleteItem(_ item: ShoppingItem) async {
        let key = item.stableID
        mutatingItemIDs.insert(key)
        defer { mutatingItemIDs.remove(key) }
        do { try await api.deleteItem(item); await refresh() }
        catch { errorMessage = error.localizedDescription }
    }

    func clearChecked() async {
        guard let items = shoppingList?.items.filter(\.checked) else { return }
        for item in items { await deleteItem(item) }
    }

    func category(for item: ShoppingItem) -> ShoppingCategory {
        categories.category(for: item.name)
    }

    func setCategory(_ category: ShoppingCategory, for item: ShoppingItem) {
        categories.setCategory(category, for: item.name)
        objectWillChange.send()
    }

    func saveToken(_ token: String) throws {
        try KeychainStore.saveToken(token.trimmingCharacters(in: .whitespacesAndNewlines))
        tokenConfigured = true
    }

    func syncGeofences() { geofence.sync(stores: stores.stores) }
}
