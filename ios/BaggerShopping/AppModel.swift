import Foundation

@MainActor
final class AppModel: ObservableObject {
    @Published var shoppingList: ShoppingListResponse?
    @Published var isLoading = false
    @Published var errorMessage: String?
    @Published var tokenConfigured = KeychainStore.loadToken() != nil
    @Published var onboardingRequired = KeychainStore.loadToken() == nil
    @Published var mutatingItemIDs: Set<String> = []
    @Published var householdProfile: HouseholdProfile?
    @Published var latestRecoveryCode: String?

    let stores = StoreRepository()
    let geofence = GeofenceManager()
    let categories = ShoppingCategoryService()
    let flyerPush = FlyerPushManager()
    private let api = APIClient()
    private let offerMetadataKey = "bagger-shopping-offer-metadata-v2"
    private let offerMetadataMigrationKey = "bagger-shopping-offer-metadata-qnap-migrated-v1"
    private var offerMetadata: [String: OfferItemMetadata]
    private var reconciliationTasks: [String: Task<Void, Never>] = [:]
    private var refreshInProgress = false
    private var listMutationRevision = 0

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
            householdProfile = try? await api.fetchHouseholdProfile()
            await refresh()
            await syncSharedCategories()
        }
    }

    func refresh() async {
        guard tokenConfigured, !refreshInProgress else { return }
        refreshInProgress = true
        isLoading = true
        let revision = listMutationRevision
        defer {
            isLoading = false
            refreshInProgress = false
        }
        do {
            let list = try await api.fetchList()
            // Do not let a response that started before an optimistic mutation
            // resurrect a row that the user has just added, changed or deleted.
            guard revision == listMutationRevision else { return }

            // A DELETE is optimistic: the row disappears locally before Samsung
            // has necessarily removed it from its next read. A mutating id that
            // is absent from the local list is therefore a deletion tombstone.
            // Filter those ids from refresh responses until the short grace
            // period ends, so deleted rows never flash back into the UI.
            let pendingDeletionIDs = Set(mutatingItemIDs.filter { stableID in
                !(shoppingList?.items.contains(where: { $0.stableID == stableID }) ?? false)
            })
            let visibleItems = list.items.filter { !pendingDeletionIDs.contains($0.stableID) }
            let visibleList = replacingItems(in: list, with: visibleItems)
            shoppingList = visibleList
            let visibleIDs = Set(visibleItems.map(\.stableID))
            mutatingItemIDs = mutatingItemIDs.intersection(visibleIDs.union(pendingDeletionIDs))
            ShoppingListCache.save(visibleList)
            await syncSharedOfferMetadata()
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

    func syncSharedOfferMetadata() async {
        guard tokenConfigured else { return }
        let activeItems = shoppingList?.items ?? []
        let activeKeys = Set(activeItems.map { offerRetailerNameKey($0.name) })
        let shouldMigrateLocalCache = !UserDefaults.standard.bool(forKey: offerMetadataMigrationKey)

        do {
            let response: OfferMetadataResponse
            if shouldMigrateLocalCache {
                // v2 stored offer metadata used to live only in UserDefaults.
                // Seed still-active records into QNAP exactly once. The backend
                // merge never overwrites an existing shared value, so QNAP wins
                // conflicts with stale device data.
                let localMigration = activeItems.compactMap { item -> OfferMetadataDTO? in
                    let key = offerRetailerNameKey(item.name)
                    guard let metadata = offerMetadata[key] else { return nil }
                    return metadata.dto(itemName: item.name)
                }
                response = try await api.syncOfferMetadata(localMigration)
                UserDefaults.standard.set(true, forKey: offerMetadataMigrationKey)
            } else {
                response = try await api.fetchOfferMetadata()
            }

            // QNAP is authoritative for items that currently exist on the
            // Samsung list. Keep non-active local entries temporarily so an
            // eventually-consistent Samsung read cannot erase metadata for a
            // just-added optimistic row before it appears in Samsung Food.
            var nextMetadata = offerMetadata
            for key in activeKeys {
                nextMetadata.removeValue(forKey: key)
            }
            for record in response.metadata {
                let key = offerRetailerNameKey(record.itemName)
                guard activeKeys.contains(key) else { continue }
                nextMetadata[key] = OfferItemMetadata(dto: record)
            }
            offerMetadata = nextMetadata
            saveOfferMetadata()
            objectWillChange.send()
        } catch {
            // Keep the historic local cache usable while QNAP or the mobile API
            // is temporarily unavailable. Migration is retried until it has
            // completed successfully and the migration marker has been stored.
        }
    }

    func addItem(
        _ name: String,
        retailer: String? = nil,
        offerPrice: Double? = nil,
        offerValidFrom: String? = nil,
        offerValidUntil: String? = nil,
        offerID: String? = nil,
        publicationID: String? = nil,
        matchedItemName: String? = nil,
        offerSnapshot: GroceryOffer? = nil
    ) async -> Bool {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return false }

        let previous = shoppingList
        listMutationRevision &+= 1
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
            var metadataSyncError: Error?
            let metadataKey = offerRetailerNameKey(trimmed)

            if let retailer, !retailer.isEmpty {
                let metadata = OfferItemMetadata(
                    retailer: retailer,
                    price: offerPrice,
                    validFrom: offerValidFrom,
                    validUntil: offerValidUntil,
                    offerID: offerID,
                    publicationID: publicationID,
                    matchedItemName: matchedItemName ?? trimmed,
                    offerSnapshot: offerSnapshot
                )
                offerMetadata[metadataKey] = metadata
                saveOfferMetadata()
                objectWillChange.send()
                do {
                    try await api.setOfferMetadata(metadata.dto(itemName: trimmed))
                } catch {
                    metadataSyncError = error
                }
            } else {
                // Items typed in the app use the same plain Samsung Food flow
                // as fridge-created items and must not inherit old offer data.
                offerMetadata.removeValue(forKey: metadataKey)
                saveOfferMetadata()
                objectWillChange.send()
                do {
                    try await api.removeOfferMetadata(itemName: trimmed)
                } catch {
                    metadataSyncError = error
                }
            }

            if let metadataSyncError {
                errorMessage = "Varen er tilføjet, men tilbudsoplysningerne kunne ikke deles endnu: \(metadataSyncError.localizedDescription)"
            } else {
                errorMessage = nil
            }
            // Samsung can be eventually consistent after SyncItems. Do not
            // replace the confirmed optimistic row with a stale response a few
            // seconds later; the next ordinary refresh will reconcile it.
            scheduleReconciliation(for: trimmed)
            return true
        } catch {
            shoppingList = previous
            if let previous { ShoppingListCache.save(previous) }
            errorMessage = error.localizedDescription
            return false
        }
    }

    private func scheduleReconciliation(for name: String) {
        let key = offerRetailerNameKey(name)
        reconciliationTasks[key]?.cancel()
        reconciliationTasks[key] = Task { [weak self] in
            for delay in [2, 4, 8] {
                try? await Task.sleep(for: .seconds(delay))
                guard !Task.isCancelled, let self else { return }
                await self.refresh()
                if self.shoppingList?.items.contains(where: {
                    $0.id != nil && self.offerRetailerNameKey($0.name) == key
                }) == true { break }
            }
            self?.reconciliationTasks[key] = nil
        }
    }

    private func cancelReconciliation(for name: String) {
        let key = offerRetailerNameKey(name)
        reconciliationTasks.removeValue(forKey: key)?.cancel()
    }

    private func scheduleDeletionTombstoneRelease(_ stableID: String) {
        Task { @MainActor [weak self] in
            // Samsung Food can briefly return the deleted row after DELETE has
            // succeeded. Keeping the tombstone outside the mutation queue means
            // subsequent rapid deletes can continue immediately.
            try? await Task.sleep(for: .seconds(8))
            self?.mutatingItemIDs.remove(stableID)
        }
    }

    func setChecked(_ item: ShoppingItem, checked: Bool) async {
        guard item.id != nil else { return }
        let previous = shoppingList
        listMutationRevision &+= 1
        updateLocalItem(item.stableID) { changed in
            changed.checked = checked
        }

        let key = item.stableID
        mutatingItemIDs.insert(key)
        defer { mutatingItemIDs.remove(key) }
        do {
            try await api.setChecked(item: item, checked: checked)
            if let shoppingList { ShoppingListCache.save(shoppingList) }
            errorMessage = nil
        } catch {
            shoppingList = previous
            if let previous { ShoppingListCache.save(previous) }
            errorMessage = error.localizedDescription
        }
    }

    func setQuantity(_ item: ShoppingItem, quantity: Double) async {
        guard item.id != nil, quantity > 0 else { return }
        let previous = shoppingList
        listMutationRevision &+= 1
        updateLocalItem(item.stableID) { changed in
            changed.quantity = quantity
            changed.unit = changed.unit ?? "stk"
        }

        let key = item.stableID
        mutatingItemIDs.insert(key)
        defer { mutatingItemIDs.remove(key) }
        do {
            try await api.setQuantity(item: item, quantity: quantity, unit: item.unit ?? "stk")
            if let shoppingList { ShoppingListCache.save(shoppingList) }
            errorMessage = nil
        } catch {
            shoppingList = previous
            if let previous { ShoppingListCache.save(previous) }
            errorMessage = error.localizedDescription
        }
    }

    func deleteItem(_ item: ShoppingItem) async {
        guard item.id != nil else { return }
        let previous = shoppingList

        // A just-added item may still have a 2/4/8 second reconciliation loop.
        // Stop it before changing the list so it cannot race the DELETE and
        // reinsert stale Samsung state into the SwiftUI List.
        cancelReconciliation(for: item.name)
        listMutationRevision &+= 1

        if let list = shoppingList {
            let updated = replacingItems(
                in: list,
                with: list.items.filter { $0.stableID != item.stableID }
            )
            shoppingList = updated
            ShoppingListCache.save(updated)
        }

        let key = item.stableID
        mutatingItemIDs.insert(key)
        do {
            try await api.deleteItem(item)
            offerMetadata.removeValue(forKey: offerRetailerNameKey(item.name))
            saveOfferMetadata()
            objectWillChange.send()
            do {
                try await api.removeOfferMetadata(itemName: item.name)
                errorMessage = nil
            } catch {
                errorMessage = "Varen er slettet, men tilbudsoplysningerne kunne ikke fjernes fra den fælles metadata endnu: \(error.localizedDescription)"
            }
            scheduleDeletionTombstoneRelease(key)
        } catch {
            mutatingItemIDs.remove(key)
            shoppingList = previous
            if let previous { ShoppingListCache.save(previous) }
            errorMessage = error.localizedDescription
        }
    }

    func clearChecked() async {
        guard let checked = shoppingList?.items.filter(\.checked), !checked.isEmpty else { return }
        let previous = shoppingList
        checked.forEach { cancelReconciliation(for: $0.name) }
        let deletionIDs = Set(checked.map(\.stableID))
        mutatingItemIDs.formUnion(deletionIDs)
        listMutationRevision &+= 1
        if let list = shoppingList {
            let updated = replacingItems(in: list, with: list.items.filter { !$0.checked })
            shoppingList = updated
            ShoppingListCache.save(updated)
        }
        do {
            try await api.deleteAllCheckedItems()
            for item in checked {
                offerMetadata.removeValue(forKey: offerRetailerNameKey(item.name))
                try? await api.removeOfferMetadata(itemName: item.name)
            }
            saveOfferMetadata()
            errorMessage = nil
            deletionIDs.forEach(scheduleDeletionTombstoneRelease)
        } catch {
            mutatingItemIDs.subtract(deletionIDs)
            shoppingList = previous
            if let previous { ShoppingListCache.save(previous) }
            errorMessage = error.localizedDescription
        }
    }

    func category(for item: ShoppingItem) -> ShoppingCategory {
        categories.category(for: item.name)
    }

    func offerRetailer(for item: ShoppingItem) -> String? {
        currentOfferMetadata(for: item)?.retailer
    }

    func assignedRetailer(for item: ShoppingItem) -> String? {
        offerMetadata[offerRetailerNameKey(item.name)]?.retailer
    }

    func hasApprovedOfferMatch(offerID: String, publicationID: String) -> Bool {
        (shoppingList?.items ?? []).contains { item in
            guard let metadata = offerMetadata[offerRetailerNameKey(item.name)] else { return false }
            return metadata.offerID == offerID && metadata.publicationID == publicationID
        }
    }

    func offerPrice(for item: ShoppingItem) -> Double? {
        currentOfferMetadata(for: item)?.price
    }

    func offerMetadataReference(for item: ShoppingItem) -> OfferMetadataDTO? {
        offerMetadata[offerRetailerNameKey(item.name)]?.dto(itemName: item.name)
    }

    func offerState(for item: ShoppingItem) -> OfferItemState? {
        guard let metadata = offerMetadata[offerRetailerNameKey(item.name)] else { return nil }
        let today = Calendar.current.startOfDay(for: Date())
        if let start = parseOfferDate(metadata.validFrom), start > today {
            return .upcoming(start)
        }
        if let end = parseOfferDate(metadata.validUntil), end < today {
            return .expired
        }
        if let end = parseOfferDate(metadata.validUntil) {
            let days = Calendar.current.dateComponents([.day], from: today, to: Calendar.current.startOfDay(for: end)).day ?? 2
            if days <= 1 { return .expiresSoon }
        }
        return .active
    }

    func expiredOfferMetadata(for item: ShoppingItem) -> (retailer: String, price: Double?)? {
        guard let metadata = offerMetadata[offerRetailerNameKey(item.name)],
              let validUntil = metadata.validUntil else { return nil }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "da_DK")
        formatter.dateFormat = "dd.MM.yyyy"
        guard let expiry = formatter.date(from: validUntil),
              Calendar.current.startOfDay(for: expiry) < Calendar.current.startOfDay(for: Date()) else { return nil }
        return (metadata.retailer, metadata.price)
    }

    func offerExpiresToday(for item: ShoppingItem) -> Bool {
        offerState(for: item) == .expiresSoon
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

    func completeOnboarding() {
        onboardingRequired = false
        latestRecoveryCode = nil
    }

    func createHousehold(name: String, memberName: String) async -> Bool {
        do {
            let response = try await api.createHousehold(name: name, memberName: memberName)
            try KeychainStore.saveToken(response.accessToken)
            tokenConfigured = true
            householdProfile = HouseholdProfile(
                householdID: response.householdID, householdName: response.householdName,
                memberName: response.memberName, role: response.role, listBackend: response.listBackend
            )
            latestRecoveryCode = response.recoveryCode
            await refresh()
            return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    func joinHousehold(code: String, memberName: String) async -> Bool {
        do {
            let response = try await api.joinHousehold(code: code, memberName: memberName)
            try KeychainStore.saveToken(response.accessToken)
            tokenConfigured = true
            householdProfile = HouseholdProfile(
                householdID: response.householdID, householdName: response.householdName,
                memberName: response.memberName, role: response.role, listBackend: response.listBackend
            )
            await refresh()
            return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    func recoverHousehold(code: String, memberName: String) async -> Bool {
        do {
            let response = try await api.recoverHousehold(code: code, memberName: memberName)
            try KeychainStore.saveToken(response.accessToken)
            tokenConfigured = true
            householdProfile = HouseholdProfile(
                householdID: response.householdID, householdName: response.householdName,
                memberName: response.memberName, role: response.role, listBackend: response.listBackend
            )
            await refresh()
            return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    func rotateRecoveryCode() async -> String? {
        do {
            let code = try await api.rotateRecoveryCode()
            latestRecoveryCode = code
            return code
        } catch { errorMessage = error.localizedDescription; return nil }
    }

    func createInvite() async -> String? {
        do { return try await api.createHouseholdInvite().inviteCode }
        catch { errorMessage = error.localizedDescription; return nil }
    }

    func householdMembers() async -> [HouseholdMember]? {
        do { return try await api.fetchHouseholdMembers() }
        catch { errorMessage = error.localizedDescription; return nil }
    }

    func updateHouseholdMember(id: String, name: String) async -> Bool {
        do { try await api.updateHouseholdMember(id: id, name: name); return true }
        catch { errorMessage = error.localizedDescription; return false }
    }

    func removeHouseholdMember(id: String) async -> Bool {
        do { try await api.removeHouseholdMember(id: id); return true }
        catch { errorMessage = error.localizedDescription; return false }
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
        return offerState(for: item) == .expired ? nil : metadata
    }

    private func parseOfferDate(_ value: String?) -> Date? {
        guard let value else { return nil }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "da_DK")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.dateFormat = "dd.MM.yyyy"
        return formatter.date(from: value)
    }

    private func saveOfferMetadata() {
        guard let data = try? JSONEncoder().encode(offerMetadata) else { return }
        UserDefaults.standard.set(data, forKey: offerMetadataKey)
    }
}

private struct OfferItemMetadata: Codable {
    let retailer: String
    let price: Double?
    let validFrom: String?
    let validUntil: String?
    let offerID: String?
    let publicationID: String?
    let matchedItemName: String?
    let offerSnapshot: GroceryOffer?

    init(
        retailer: String,
        price: Double?,
        validFrom: String?,
        validUntil: String?,
        offerID: String? = nil,
        publicationID: String? = nil,
        matchedItemName: String? = nil,
        offerSnapshot: GroceryOffer? = nil
    ) {
        self.retailer = retailer
        self.price = price
        self.validFrom = validFrom
        self.validUntil = validUntil
        self.offerID = offerID
        self.publicationID = publicationID
        self.matchedItemName = matchedItemName
        self.offerSnapshot = offerSnapshot
    }

    init(dto: OfferMetadataDTO) {
        retailer = dto.retailer
        price = dto.price
        validFrom = dto.validFrom
        validUntil = dto.validUntil
        offerID = dto.offerID
        publicationID = dto.publicationID
        matchedItemName = dto.matchedItemName
        offerSnapshot = dto.offerSnapshot
    }

    func dto(itemName: String) -> OfferMetadataDTO {
        OfferMetadataDTO(
            itemName: itemName,
            retailer: retailer,
            price: price,
            validFrom: validFrom,
            validUntil: validUntil,
            offerID: offerID,
            publicationID: publicationID,
            matchedItemName: matchedItemName ?? itemName,
            offerSnapshot: offerSnapshot
        )
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        retailer = try values.decode(String.self, forKey: .retailer)
        price = try values.decodeIfPresent(Double.self, forKey: .price)
        validFrom = try values.decodeIfPresent(String.self, forKey: .validFrom)
        validUntil = try values.decodeIfPresent(String.self, forKey: .validUntil)
        offerID = try values.decodeIfPresent(String.self, forKey: .offerID)
        publicationID = try values.decodeIfPresent(String.self, forKey: .publicationID)
        matchedItemName = try values.decodeIfPresent(String.self, forKey: .matchedItemName)
        offerSnapshot = try values.decodeIfPresent(GroceryOffer.self, forKey: .offerSnapshot)
    }
}

enum OfferItemState: Equatable {
    case upcoming(Date)
    case active
    case expiresSoon
    case expired
}
