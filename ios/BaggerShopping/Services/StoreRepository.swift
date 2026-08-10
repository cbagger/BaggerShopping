import Foundation

@MainActor
final class StoreRepository: ObservableObject {
    @Published private(set) var stores: [StoreLocation] = []
    private let key = "bagger-shopping-stores"

    init() { load() }

    func add(_ store: StoreLocation) { stores.append(store); save() }
    func update(_ store: StoreLocation) {
        guard let index = stores.firstIndex(where: { $0.id == store.id }) else { return }
        stores[index] = store
        save()
    }
    func delete(at offsets: IndexSet) { stores.remove(atOffsets: offsets); save() }
    func setEnabled(_ enabled: Bool, for id: UUID) {
        guard let index = stores.firstIndex(where: { $0.id == id }) else { return }
        stores[index].enabled = enabled
        save()
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(stores) else { return }
        UserDefaults.standard.set(data, forKey: key)
    }

    private func load() {
        guard let data = UserDefaults.standard.data(forKey: key) else { return }
        if let decoded = try? JSONDecoder().decode([StoreLocation].self, from: data) {
            stores = decoded
            return
        }
        // v0.1 migration: old stores had no address field.
        struct LegacyStore: Codable {
            let id: UUID
            var name: String
            var latitude: Double
            var longitude: Double
            var radius: Double
            var enabled: Bool
        }
        if let legacy = try? JSONDecoder().decode([LegacyStore].self, from: data) {
            stores = legacy.map {
                StoreLocation(id: $0.id, name: $0.name, latitude: $0.latitude, longitude: $0.longitude, radius: $0.radius, enabled: $0.enabled)
            }
            save()
        }
    }
}
