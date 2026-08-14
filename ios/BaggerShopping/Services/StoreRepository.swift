import Foundation

@MainActor
final class StoreRepository: ObservableObject {
    @Published private(set) var stores: [StoreLocation] = []
    private let key = "bagger-shopping-stores"

    init() { load() }

    func add(_ store: StoreLocation) {
        stores.append(store)
        save()
    }

    func update(_ store: StoreLocation) {
        guard let index = stores.firstIndex(where: { $0.id == store.id }) else { return }
        stores[index] = store
        save()
    }

    func delete(at offsets: IndexSet) {
        stores.remove(atOffsets: offsets)
        save()
    }

    func delete(id: UUID) {
        stores.removeAll { $0.id == id }
        save()
    }

    func setEnabled(_ enabled: Bool, for id: UUID) {
        guard let index = stores.firstIndex(where: { $0.id == id }) else { return }
        stores[index].enabled = enabled
        save()
    }

    func setRadius(_ radius: Double, for id: UUID) {
        guard let index = stores.firstIndex(where: { $0.id == id }) else { return }
        stores[index].radius = min(max(radius, 100), 500)
        save()
    }

    func setAddress(_ address: String, for id: UUID) {
        guard let index = stores.firstIndex(where: { $0.id == id }) else { return }
        stores[index].address = address.trimmingCharacters(in: .whitespacesAndNewlines)
        save()
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(stores) else { return }
        UserDefaults.standard.set(data, forKey: key)
    }

    private func load() {
        guard let data = UserDefaults.standard.data(forKey: key) else { return }
        if let decoded = try? JSONDecoder().decode([StoreLocation].self, from: data) {
            stores = decoded.map { store in
                var migrated = store
                migrated.radius = min(max(store.radius, 100), 500)
                return migrated
            }
            if stores != decoded { save() }
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
                StoreLocation(
                    id: $0.id,
                    name: $0.name,
                    latitude: $0.latitude,
                    longitude: $0.longitude,
                    radius: min(max($0.radius, 100), 500),
                    enabled: $0.enabled
                )
            }
            save()
        }
    }
}
