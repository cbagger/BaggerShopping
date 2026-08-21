import Foundation

struct PendingCheckedMutation: Codable, Equatable {
    let operationID: UUID
    let itemID: String
    let itemName: String
    let checked: Bool
    let quantity: Double?
    let unit: String?
    let updatedAt: Date
    var acknowledgedAt: Date?

    init(item: ShoppingItem, checked: Bool, updatedAt: Date = Date()) {
        operationID = UUID()
        itemID = item.id ?? ""
        itemName = item.name
        self.checked = checked
        quantity = item.quantity
        unit = item.unit
        self.updatedAt = updatedAt
        acknowledgedAt = nil
    }

    var item: ShoppingItem {
        ShoppingItem(
            id: itemID,
            name: itemName,
            checked: checked,
            quantity: quantity,
            unit: unit
        )
    }
}

/// A durable outbox for checkbox changes. Enqueueing is synchronous so the
/// user's intent is on disk before iOS can suspend the app after screen lock.
struct PendingCheckedMutationStore {
    private let defaults: UserDefaults
    private let storageKey: String

    init(
        defaults: UserDefaults = .standard,
        storageKey: String = "kurv-pending-checked-mutations-v1"
    ) {
        self.defaults = defaults
        self.storageKey = storageKey
    }

    func load() -> [String: PendingCheckedMutation] {
        guard let data = defaults.data(forKey: storageKey),
              let mutations = try? JSONDecoder().decode(
                  [String: PendingCheckedMutation].self,
                  from: data
              ) else {
            return [:]
        }
        return mutations
    }

    @discardableResult
    func enqueue(item: ShoppingItem, checked: Bool, updatedAt: Date = Date()) -> PendingCheckedMutation? {
        guard let itemID = item.id, !itemID.isEmpty else { return nil }
        let mutation = PendingCheckedMutation(item: item, checked: checked, updatedAt: updatedAt)
        var pending = load()
        pending[itemID] = mutation
        save(pending)
        return mutation
    }

    func removeIfCurrent(_ mutation: PendingCheckedMutation) {
        var pending = load()
        guard pending[mutation.itemID]?.operationID == mutation.operationID else { return }
        pending.removeValue(forKey: mutation.itemID)
        save(pending)
    }

    func markAcknowledgedIfCurrent(_ mutation: PendingCheckedMutation, at date: Date = Date()) {
        var pending = load()
        guard pending[mutation.itemID]?.operationID == mutation.operationID else { return }
        var acknowledged = mutation
        acknowledged.acknowledgedAt = date
        pending[mutation.itemID] = acknowledged
        save(pending)
    }

    /// Removes only acknowledgements that Samsung's read side has actually
    /// caught up with. Until then the local overlay prevents a stale GET from
    /// visually undoing a successfully written checkmark.
    func reconcileAcknowledged(with remote: ShoppingListResponse) {
        var pending = load()
        let confirmedIDs = pending.values.compactMap { mutation -> String? in
            guard mutation.acknowledgedAt != nil,
                  let remoteItem = remote.items.first(where: { $0.id == mutation.itemID }),
                  remoteItem.checked == mutation.checked else { return nil }
            return mutation.itemID
        }
        for itemID in confirmedIDs {
            pending.removeValue(forKey: itemID)
        }
        save(pending)
    }

    func ordered() -> [PendingCheckedMutation] {
        load().values.filter { $0.acknowledgedAt == nil }.sorted {
            if $0.updatedAt != $1.updatedAt { return $0.updatedAt < $1.updatedAt }
            return $0.itemID < $1.itemID
        }
    }

    var unacknowledgedItemIDs: Set<String> {
        Set(load().values.filter { $0.acknowledgedAt == nil }.map(\.itemID))
    }

    func applying(to list: ShoppingListResponse) -> ShoppingListResponse {
        let pending = load()
        guard !pending.isEmpty else { return list }
        var items = list.items
        for index in items.indices {
            guard let itemID = items[index].id,
                  let mutation = pending[itemID] else { continue }
            items[index].checked = mutation.checked
        }
        return ShoppingListResponse(
            ok: list.ok,
            name: list.name,
            count: items.count,
            hasItems: !items.isEmpty,
            items: items
        )
    }

    private func save(_ mutations: [String: PendingCheckedMutation]) {
        guard !mutations.isEmpty else {
            defaults.removeObject(forKey: storageKey)
            return
        }
        guard let data = try? JSONEncoder().encode(mutations) else { return }
        defaults.set(data, forKey: storageKey)
    }
}
