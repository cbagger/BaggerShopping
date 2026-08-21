import Foundation

@MainActor
final class ShoppingListMutationQueue: ObservableObject {
    private var tail: Task<Void, Never>?

    func enqueue(_ operation: @escaping @MainActor () async -> Void) {
        let previous = tail
        tail = Task { @MainActor in
            if let previous {
                await previous.value
            }
            guard !Task.isCancelled else { return }
            await operation()
        }
    }

    deinit {
        tail?.cancel()
    }
}

extension AppModel {
    /// Fast path for ordinary text entries. The optimistic row is persisted in
    /// the local cache immediately and is never replaced by a stale Samsung
    /// read while SyncItems is still eventually consistent.
    @MainActor
    func addManualItemResponsive(_ rawName: String) async -> Bool {
        let name = rawName
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
        guard !name.isEmpty else { return false }
        guard beginItemAddition() else { return false }
        defer { finishItemAddition() }

        let provisional = ShoppingItem(
            id: nil,
            name: name,
            checked: false,
            quantity: nil,
            unit: nil
        )
        appendProvisionalItemIfNeeded(provisional)

        do {
            try await APIClient().addItem(name: name)
            errorMessage = nil
            reconcileManualItem(name: name)
            return true
        } catch {
            if Self.isLikelyTimeout(error) {
                // Samsung may have accepted the mutation even though the HTTP
                // request timed out. Keep the visible row and verify in the
                // background instead of making it disappear in front of the user.
                errorMessage = "Tilføjelsen tager længere tid end normalt. Kurv kontrollerer Samsung Food i baggrunden."
                reconcileManualItem(name: name)
                return true
            }

            removeProvisionalItem(named: name)
            errorMessage = Self.userFacingMutationError(error)
            return false
        }
    }

    @MainActor
    private func appendProvisionalItemIfNeeded(_ item: ShoppingItem) {
        guard let list = shoppingList else { return }
        let wanted = Self.normalizedManualName(item.name)
        guard !list.items.contains(where: { Self.normalizedManualName($0.name) == wanted }) else { return }
        let updated = Self.listResponse(list, items: list.items + [item])
        shoppingList = updated
        ShoppingListCache.save(updated)
    }

    @MainActor
    private func removeProvisionalItem(named name: String) {
        guard let list = shoppingList else { return }
        let wanted = Self.normalizedManualName(name)
        let updatedItems = list.items.filter {
            !($0.id == nil && Self.normalizedManualName($0.name) == wanted)
        }
        let updated = Self.listResponse(list, items: updatedItems)
        shoppingList = updated
        ShoppingListCache.save(updated)
    }

    @MainActor
    private func reconcileManualItem(name: String) {
        let wanted = Self.normalizedManualName(name)
        Task { @MainActor [weak self] in
            guard let self else { return }
            for delay in [1, 2, 4, 8] {
                try? await Task.sleep(for: .seconds(delay))
                guard !Task.isCancelled else { return }
                guard let remote = try? await APIClient().fetchList(),
                      let persisted = remote.items.first(where: {
                          $0.id != nil && Self.normalizedManualName($0.name) == wanted
                      }) else {
                    continue
                }

                guard let current = self.shoppingList else { return }
                var items = current.items
                if let index = items.firstIndex(where: {
                    $0.id == nil && Self.normalizedManualName($0.name) == wanted
                }) {
                    items[index] = persisted
                } else if !items.contains(where: { $0.id == persisted.id }) {
                    items.append(persisted)
                }
                let updated = Self.listResponse(current, items: items)
                self.shoppingList = updated
                ShoppingListCache.save(updated)
                return
            }
        }
    }

    private static func listResponse(_ source: ShoppingListResponse, items: [ShoppingItem]) -> ShoppingListResponse {
        ShoppingListResponse(
            ok: source.ok,
            name: source.name,
            count: items.count,
            hasItems: !items.isEmpty,
            items: items
        )
    }

    private static func normalizedManualName(_ value: String) -> String {
        value
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "da_DK"))
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
    }

    private static func isLikelyTimeout(_ error: Error) -> Bool {
        if let urlError = error as? URLError, urlError.code == .timedOut {
            return true
        }
        let text = error.localizedDescription.lowercased()
        return text.contains("timed out") || text.contains("timeout") || text.contains("tidsgrænse")
    }

    static func userFacingMutationError(_ error: Error) -> String {
        if isLikelyTimeout(error) {
            return "Forbindelsen tog for lang tid. Kurv kontrollerer synkroniseringen i baggrunden."
        }
        return "Ændringen kunne ikke synkroniseres med indkøbslisten. Prøv igen om et øjeblik."
    }

    static func userFacingMutationMessage(_ raw: String) -> String {
        let lower = raw.lowercased()
        if lower.contains("timed out") || lower.contains("timeout") {
            return "Forbindelsen tog for lang tid. Prøv igen om et øjeblik."
        }
        return raw
    }
}
