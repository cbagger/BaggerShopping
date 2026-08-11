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
    private let offerMetadataKey = "bagger-shopping-offer-metadata-v2"
    private var offerMetadata: [String: OfferItemMetadata]

    init() {
        if let data = UserDefaults.standard.data(forKey: offerMetadataKey),
           let decoded = try? JSONDecoder().decode([String: OfferItemMetadata].self, from: data) {
            offerMetadata = decoded
        } else {
            offerMetadata = [:]
        }
    }

    func bootstrap() async {
        geofence.sync(stores: stores.stores)
        if tokenConfigured {
            await refresh()
            await syncSharedCategories()
        }
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
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func syncSharedCategories() async {
        guard tokenConfigured else { return }
        do {
            let shared = try await api.fetchCategoryOverrides()
            categories.replaceWithSharedOverrides(shared.overrides)
            objectWillChange.send()
        } catch {
            // Category sharing is additive. Keep the local cache usable if the
            // backend is temporarily unavailable or has not yet been upgraded.
        }
    }

    func addItem(
        _ name: String,
        retailer: String? = nil,
        offerPrice: Double? = nil,
        offerValidUntil: String? = nil
    ) async -> Bool {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return false }

        let previous = shoppingList
        if let list = shoppingList {
            let provisional = ShoppingItem(
                id: nil,
                name: trimmed,
                checked: false,
                quantity: nil,
                unit: nil
            )
            shoppingList = replacingItems(in: list, with: list.items + [provisional])
        }

        do {
            try await api.addItem(name: trimmed)
            if let retailer, !retailer.isEmpty {
                offerMetadata[offerRetailerNameKey(trimmed)] = OfferItemMetadata(
                    retailer: retailer,
                    price: offerPrice,
                    validUntil: offerValidUntil
                )
                saveOfferMetadata()
                objectWillChange.send()
            } else {
                // Items typed in the app use the same plain Samsung Food flow
                // as fridge-created items and must not inherit old offer data.
                offerMetadata.removeValue(forKey: offerRetailerNameKey(trimmed))
                saveOfferMetadata()
                objectWillChange.send()
            }
            errorMessage = nil
            // Samsung can be eventually consistent after SyncItems. Keep the
            // optimistic row instead of immediately replacing it with stale data.
            Task {
                try? await Task.sleep(for: .seconds(4))
                await refresh()
            }
            return true
        } catch {
            shoppingList = previous
            errorMessage = error.localizedDescription
            return false
        }
    }

    func setChecked(_ item: ShoppingItem, checked: Bool) async {
        guard item.id != nil else { return }
        let previous = shoppingList
        updateLocalItem(item.stableID) { changed in
            changed.checked = checked
        }

        let key = item.stableID
        mutatingItemIDs.insert(key)
        defer { mutatingItemIDs.remove(key) }
        do {
            try await api.setChecked(item: item, checked: checked)
            errorMessage = nil
        } catch {
            shoppingList = previous
            errorMessage = error.localizedDescription
        }
    }

    func setQuantity(_ item: ShoppingItem, quantity: Double) async {
        guard item.id != nil, quantity > 0 else { return }
        let previous = shoppingList
        updateLocalItem(item.stableID) { changed in
            changed.quantity = quantity
            changed.unit = changed.unit ?? "stk"
        }

        let key = item.stableID
        mutatingItemIDs.insert(key)
        defer { mutatingItemIDs.remove(key) }
        do {
            try await api.setQuantity(item: item, quantity: quantity, unit: item.unit ?? "stk")
            errorMessage = nil
        } catch {
            shoppingList = previous
            errorMessage = error.localizedDescription
        }
    }

    func deleteItem(_ item: ShoppingItem) async {
        guard item.id != nil else { return }
        let previous = shoppingList
        if let list = shoppingList {
            shoppingList = replacingItems(
                in: list,
                with: list.items.filter { $0.stableID != item.stableID }
            )
        }

        let key = item.stableID
        mutatingItemIDs.insert(key)
        defer { mutatingItemIDs.remove(key) }
        do {
            try await api.deleteItem(item)
            offerMetadata.removeValue(forKey: offerRetailerNameKey(item.name))
            saveOfferMetadata()
            errorMessage = nil
        } catch {
            shoppingList = previous
            errorMessage = error.localizedDescription
        }
    }

    func clearChecked() async {
        guard let items = shoppingList?.items.filter(\.checked) else { return }
        for item in items { await deleteItem(item) }
    }

    func category(for item: ShoppingItem) -> ShoppingCategory {
        categories.category(for: item.name)
    }

    func offerRetailer(for item: ShoppingItem) -> String? {
        currentOfferMetadata(for: item)?.retailer
    }

    func offerPrice(for item: ShoppingItem) -> Double? {
        currentOfferMetadata(for: item)?.price
    }

    func setCategory(_ category: ShoppingCategory, for item: ShoppingItem) {
        categories.setCategory(category, for: item.name)
        objectWillChange.send()
        Task {
            do {
                try await api.setCategoryOverride(itemName: item.name, category: category)
            } catch {
                errorMessage = "Kategorien er gemt på denne iPhone, men kunne ikke deles endnu: \(error.localizedDescription)"
            }
        }
    }

    func hasCategoryOverride(for item: ShoppingItem) -> Bool {
        categories.hasOverride(for: item.name)
    }

    func resetCategory(for item: ShoppingItem) {
        categories.removeOverride(for: item.name)
        objectWillChange.send()
        Task {
            do {
                try await api.removeCategoryOverride(itemName: item.name)
            } catch {
                errorMessage = "Kategori-reset kunne ikke synkroniseres: \(error.localizedDescription)"
            }
        }
    }

    func clearLearnedCategories() {
        categories.removeAllOverrides()
        objectWillChange.send()
        Task {
            do {
                try await api.clearCategoryOverrides()
            } catch {
                errorMessage = "De lokale kategorier blev nulstillet, men serveren kunne ikke opdateres: \(error.localizedDescription)"
            }
        }
    }

    func saveToken(_ token: String) throws {
        try KeychainStore.saveToken(token.trimmingCharacters(in: .whitespacesAndNewlines))
        tokenConfigured = true
    }

    func syncGeofences() { geofence.sync(stores: stores.stores) }

    private func updateLocalItem(_ stableID: String, mutate: (inout ShoppingItem) -> Void) {
        guard let list = shoppingList,
              let index = list.items.firstIndex(where: { $0.stableID == stableID }) else { return }
        var items = list.items
        mutate(&items[index])
        shoppingList = replacingItems(in: list, with: items)
    }

    private func replacingItems(in list: ShoppingListResponse, with items: [ShoppingItem]) -> ShoppingListResponse {
        ShoppingListResponse(
            ok: list.ok,
            name: list.name,
            count: items.count,
            hasItems: !items.isEmpty,
            items: items
        )
    }

    private func offerRetailerNameKey(_ name: String) -> String {
        name.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    private func currentOfferMetadata(for item: ShoppingItem) -> OfferItemMetadata? {
        guard let metadata = offerMetadata[offerRetailerNameKey(item.name)] else { return nil }
        guard let validUntil = metadata.validUntil else { return metadata }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "da_DK")
        formatter.dateFormat = "dd.MM.yyyy"
        guard let expiry = formatter.date(from: validUntil) else { return metadata }
        return Calendar.current.startOfDay(for: expiry) >= Calendar.current.startOfDay(for: Date()) ? metadata : nil
    }

    private func saveOfferMetadata() {
        guard let data = try? JSONEncoder().encode(offerMetadata) else { return }
        UserDefaults.standard.set(data, forKey: offerMetadataKey)
    }
}

private struct OfferItemMetadata: Codable {
    let retailer: String
    let price: Double?
    let validUntil: String?
}
