import SwiftUI

private struct ShoppingItemRenameTarget: Identifiable {
    let id = UUID()
    let item: ShoppingItem
}

private struct NearbyStorePickerView: View {
    let stores: [StoreVisitContext]
    let onSelect: (StoreVisitContext) -> Void

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(stores) { store in
                        Button {
                            onSelect(store)
                        } label: {
                            HStack(spacing: 13) {
                                Image(systemName: "storefront.fill")
                                    .font(.headline)
                                    .foregroundStyle(.white)
                                    .frame(width: 38, height: 38)
                                    .background(Color.accentColor, in: Circle())

                                VStack(alignment: .leading, spacing: 3) {
                                    Text(store.retailer)
                                        .font(.headline)
                                        .foregroundStyle(.primary)
                                    if !store.address.isEmpty {
                                        Text(store.address)
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                            .lineLimit(2)
                                    }
                                }

                                Spacer(minLength: 6)

                                Image(systemName: "arrow.right.circle.fill")
                                    .font(.title3)
                                    .foregroundStyle(Color.accentColor)
                            }
                            .padding(.vertical, 5)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                } header: {
                    Text("Du er inden for flere butikkers område")
                } footer: {
                    Text("Kurv lærer rækkefølgen separat for den butik, du vælger.")
                }
            }
            .navigationTitle("Vælg butik")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Annuller") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }
}

private struct ShoppingItemOfferTarget: Identifiable {
    let id = UUID()
    let item: ShoppingItem
}

private struct ShoppingItemOfferPreviewTarget: Identifiable {
    let id = UUID()
    let metadata: OfferMetadataDTO
}

struct ShoppingListView: View {
    private struct CategoryGroup: Identifiable {
        let category: ShoppingCategory
        let items: [ShoppingItem]
        var id: String { category.id }
    }

    private struct RetailerGroup: Identifiable {
        let retailer: String?
        let items: [ShoppingItem]
        var id: String { retailer ?? "__without-retailer__" }
        var count: Int { items.count }
    }

    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var navigation: AppNavigation
    @StateObject private var smartOffers = SmartOfferMatchService()
    @StateObject private var mutationQueue = ShoppingListMutationQueue()
    @State private var newItem = ""
    @State private var selectedRetailerFilters: Set<String> = []
    @State private var renameTarget: ShoppingItemRenameTarget?
    @State private var offerTarget: ShoppingItemOfferTarget?
    @State private var offerPreviewTarget: ShoppingItemOfferPreviewTarget?
    @State private var showCheckedItems = false
    @State private var activeStoreMode: StoreVisitContext?
    @State private var nearbyStores: [StoreVisitContext] = []
    @State private var showNearbyStorePicker = false
    @State private var storeModePurchasedIDsByStore: [String: [String]] = [:]
    @State private var showStoreModePurchased = true
    @AppStorage("shopping-list-sort-by-retailer") private var sortByRetailer = false

    private let retailerFilterOptions = RetailerCatalog.all

    private var activeItems: [ShoppingItem] {
        model.shoppingList?.items.filter { !$0.checked } ?? []
    }

    private var checkedItems: [ShoppingItem] {
        model.shoppingList?.items.filter(\.checked) ?? []
    }

    private var offerMatchSignature: String {
        activeItems
            .map { item in
                let normalized = ShoppingCategoryService.normalize(item.name)
                return "\(normalized)|\(model.offerRetailer(for: item) ?? "-")"
            }
            .sorted()
            .joined(separator: "||")
    }

    private var upcomingItems: [ShoppingItem] {
        activeItems.filter {
            if case .upcoming = model.offerState(for: $0) { return true }
            return false
        }
    }

    private var currentItems: [ShoppingItem] {
        activeItems.filter {
            if case .upcoming = model.offerState(for: $0) { return false }
            return true
        }
    }

    private var groupedActiveItems: [CategoryGroup] {
        categoryGroups(for: currentItems)
    }

    private var storeModeItems: [ShoppingItem] {
        guard let activeStoreMode else { return [] }
        return activeItems.filter {
            StoreModeService.includes(
                assignedRetailer: model.assignedRetailer(for: $0),
                in: activeStoreMode
            )
        }
    }

    private var storeModeGroups: [CategoryGroup] {
        guard let activeStoreMode else { return [] }
        return categoryGroups(for: storeModeItems).sorted { lhs, rhs in
            let lhsRank = model.storeLayouts.rank(for: lhs.category, at: activeStoreMode)
            let rhsRank = model.storeLayouts.rank(for: rhs.category, at: activeStoreMode)
            if lhsRank != rhsRank { return lhsRank < rhsRank }
            return StoreModeService.defaultRank(for: lhs.category) < StoreModeService.defaultRank(for: rhs.category)
        }
    }

    private var storeModePurchasedItems: [ShoppingItem] {
        guard let activeStoreMode else { return [] }
        let purchasedIDs = storeModePurchasedIDsByStore[activeStoreMode.id] ?? []
        let checkedByID = checkedItems.reduce(into: [String: ShoppingItem]()) {
            $0[$1.stableID] = $1
        }
        return purchasedIDs.compactMap { checkedByID[$0] }
    }

    private var storeModeProgress: StoreModeProgress {
        StoreModeService.progress(
            remaining: storeModeItems.count,
            purchased: storeModePurchasedItems.count
        )
    }

    private var retailerGroups: [RetailerGroup] {
        let grouped = Dictionary(grouping: activeItems) { model.assignedRetailer(for: $0) }
        return grouped.map { retailer, items in
            RetailerGroup(
                retailer: retailer,
                items: items.sorted { lhs, rhs in
                    let lhsCategory = model.category(for: lhs)
                    let rhsCategory = model.category(for: rhs)
                    if lhsCategory.sortOrder != rhsCategory.sortOrder {
                        return lhsCategory.sortOrder < rhsCategory.sortOrder
                    }
                    return lhs.name.localizedCaseInsensitiveCompare(rhs.name) == .orderedAscending
                }
            )
        }
        .filter { group in
            guard let retailer = group.retailer else { return true }
            return selectedRetailerFilters.isEmpty || selectedRetailerFilters.contains(retailer)
        }
        .sorted { lhs, rhs in
            if lhs.retailer == nil { return true }
            if rhs.retailer == nil { return false }
            return lhs.retailer!.localizedCaseInsensitiveCompare(rhs.retailer!) == .orderedAscending
        }
    }

    private func categoryGroups(for items: [ShoppingItem]) -> [CategoryGroup] {
        let grouped = Dictionary(grouping: items) { model.category(for: $0) }
        return grouped
            .map {
                CategoryGroup(
                    category: $0.key,
                    items: $0.value.sorted {
                        $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
                    }
                )
            }
            .sorted { $0.category.sortOrder < $1.category.sortOrder }
    }

    var body: some View {
        NavigationStack {
            Group {
                if !model.tokenConfigured {
                    ContentUnavailableView(
                        "API-token mangler",
                        systemImage: "key",
                        description: Text("Gem mobil-API tokenet under Indstillinger.")
                    )
                } else if model.isLoading && model.shoppingList == nil {
                    ProgressView("Henter indkøbsliste…")
                } else if model.shoppingList != nil {
                    shoppingListContent
                } else {
                    ContentUnavailableView(
                        "Ingen liste",
                        systemImage: "cart",
                        description: Text(model.errorMessage ?? "Kunne ikke hente indkøbslisten.")
                    )
                }
            }
            .navigationTitle(activeStoreMode == nil ? "Indkøbsliste" : "Indkøbstur")
            .navigationBarTitleDisplayMode(activeStoreMode == nil ? .large : .inline)
            .sheet(item: $renameTarget) { target in
                RenameShoppingItemView(item: target.item)
                    .environmentObject(model)
            }
            .sheet(item: $offerTarget) { target in
                SmartOfferMatchesView(service: smartOffers, item: target.item)
                    .environmentObject(model)
            }
            .sheet(item: $offerPreviewTarget) { target in
                ShoppingItemOfferPreviewSheet(metadata: target.metadata)
            }
            .sheet(isPresented: $showNearbyStorePicker) {
                NearbyStorePickerView(stores: nearbyStores) { store in
                    showNearbyStorePicker = false
                    beginStoreMode(store)
                }
            }
            .task(id: offerMatchSignature) {
                guard model.tokenConfigured,
                      model.shoppingList != nil,
                      !activeItems.isEmpty else { return }
                await smartOffers.refresh(items: activeItems, model: model)
            }
            .onChange(of: navigation.shoppingListRoute?.id) { _, _ in
                guard let route = navigation.shoppingListRoute else { return }
                beginStoreMode(route.store)
            }
            .onChange(of: navigation.storeSelectionRequest?.id) { _, _ in
                guard let request = navigation.storeSelectionRequest else { return }
                handleStoreSelectionRequest(request)
            }
            .onReceive(model.geofence.$nearbyStores) { stores in
                nearbyStores = stores
                if stores.isEmpty { showNearbyStorePicker = false }
            }
            .onAppear {
                nearbyStores = model.geofence.nearbyStores
                if let store = navigation.shoppingListRoute?.store {
                    beginStoreMode(store)
                } else if let request = navigation.storeSelectionRequest {
                    handleStoreSelectionRequest(request)
                }
            }
            .alert(
                "Fejl",
                isPresented: Binding(
                    get: { model.errorMessage != nil },
                    set: { if !$0 { model.errorMessage = nil } }
                )
            ) {
                Button("OK", role: .cancel) { model.errorMessage = nil }
            } message: {
                Text(AppModel.userFacingMutationMessage(model.errorMessage ?? ""))
            }
        }
    }

    private var shoppingListContent: some View {
        List {
            addItemRow
            if let activeStoreMode {
                storeModeBanner(activeStoreMode)
            } else {
                if !nearbyStores.isEmpty {
                    nearbyStoreModeLauncher
                }
                sortModeRow
                if sortByRetailer {
                    retailerFiltersRow
                }
            }

            if activeStoreMode != nil {
                if storeModeItems.isEmpty {
                    storeModeEmptyRow
                } else {
                    ForEach(storeModeGroups) { group in
                        Section {
                            ForEach(group.items, id: \.stableID) { item in
                                itemRow(item, showCategory: false)
                            }
                        } header: {
                            storeModeSectionHeader(
                                group.category,
                                count: group.items.count,
                                isNext: group.id == storeModeGroups.first?.id
                            )
                        }
                    }
                }

                if !storeModePurchasedItems.isEmpty {
                    storeModePurchasedSection
                }
            } else if activeItems.isEmpty {
                emptyActiveRow
            } else if sortByRetailer {
                ForEach(retailerGroups) { group in
                    Section {
                        ForEach(group.items, id: \.stableID) { item in
                            itemRow(item, showCategory: true)
                        }
                    } header: {
                        sectionHeader(
                            group.retailer ?? "Uden butik",
                            count: group.count,
                            icon: group.retailer == nil ? "shippingbox" : "storefront",
                            reminder: memberPriceReminder(for: group)
                        )
                    }
                }
            } else {
                if !upcomingItems.isEmpty {
                    Section {
                        ForEach(upcomingItems, id: \.stableID) { item in
                            itemRow(item, showCategory: false)
                        }
                    } header: {
                        sectionHeader("Kommende tilbud", count: upcomingItems.count, icon: "calendar.badge.clock")
                    }
                }

                ForEach(groupedActiveItems) { group in
                    Section {
                        ForEach(group.items, id: \.stableID) { item in
                            itemRow(item, showCategory: false)
                        }
                    } header: {
                        sectionHeader(group.category.rawValue, count: group.items.count, icon: group.category.icon)
                    }
                }
            }

            if activeStoreMode == nil, !checkedItems.isEmpty {
                checkedItemsSection
            }
        }
        .listStyle(.plain)
        .listSectionSpacing(.custom(6))
        .scrollContentBackground(.hidden)
        .background(Color(uiColor: .systemGroupedBackground))
        .safeAreaInset(edge: .bottom, spacing: 0) {
            Color.clear.frame(height: 76)
        }
        .refreshable {
            await model.refresh()
            await model.syncSharedCategories()
            await smartOffers.refresh(items: activeItems, model: model)
        }
    }

    private func beginStoreMode(_ store: StoreVisitContext) {
        showNearbyStorePicker = false
        activeStoreMode = store
        selectedRetailerFilters.removeAll()
        navigation.resolveStoreSelection()
        model.storeLayouts.beginSession(for: store)
    }

    private func endStoreMode() {
        withAnimation(.snappy(duration: 0.22)) {
            activeStoreMode = nil
        }
        navigation.endStoreMode()
    }

    private func handleStoreSelectionRequest(_ request: StoreSelectionRequest) {
        model.geofence.refreshPresence()
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(450))
            nearbyStores = model.geofence.nearbyStores
            if nearbyStores.count > 1 {
                showNearbyStorePicker = true
            } else if let store = nearbyStores.first ?? request.fallbackStore {
                beginStoreMode(store)
            }
        }
    }

    private func startNearbyStoreMode() {
        if nearbyStores.count == 1, let store = nearbyStores.first {
            beginStoreMode(store)
        } else if !nearbyStores.isEmpty {
            showNearbyStorePicker = true
        }
    }

    private var nearbyStoreModeLauncher: some View {
        Button(action: startNearbyStoreMode) {
            HStack(spacing: 13) {
                Image(systemName: nearbyStores.count == 1 ? "location.fill" : "signpost.right.and.left.fill")
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(.white)
                    .frame(width: 40, height: 40)
                    .background(.white.opacity(0.18), in: Circle())

                VStack(alignment: .leading, spacing: 3) {
                    Text(nearbyStores.count == 1
                         ? "Du er ved \(nearbyStores[0].retailer)"
                         : "\(nearbyStores.count) butikker i nærheden")
                        .font(.headline)
                    Text(nearbyStores.count == 1
                         ? "Start indkøbstur"
                         : "Vælg butik og start indkøbstur")
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.82))
                }

                Spacer(minLength: 4)

                Image(systemName: "chevron.right")
                    .font(.subheadline.weight(.bold))
                    .foregroundStyle(.white.opacity(0.86))
            }
            .foregroundStyle(.white)
            .padding(14)
            .background(
                LinearGradient(
                    colors: [Color.blue.opacity(0.94), Color.teal.opacity(0.90)],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                ),
                in: RoundedRectangle(cornerRadius: 18, style: .continuous)
            )
        }
        .buttonStyle(.plain)
        .listRowInsets(EdgeInsets(top: 4, leading: 16, bottom: 5, trailing: 16))
        .listRowSeparator(.hidden)
        .listRowBackground(Color.clear)
    }

    private func storeModeBanner(_ store: StoreVisitContext) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(alignment: .center, spacing: 11) {
                Image(systemName: "figure.walk.motion")
                    .font(.headline.weight(.bold))
                    .foregroundStyle(.white)
                    .frame(width: 36, height: 36)
                    .background(.white.opacity(0.18), in: Circle())

                VStack(alignment: .leading, spacing: 1) {
                    Text(store.retailer)
                        .font(.headline)
                    Text(store.address.isEmpty ? "Denne butik" : store.address)
                        .font(.caption2)
                        .foregroundStyle(.white.opacity(0.78))
                        .lineLimit(1)
                }

                Spacer(minLength: 4)

                if nearbyStores.count > 1 {
                    Button("Skift") { showNearbyStorePicker = true }
                        .font(.caption2.weight(.bold))
                        .buttonStyle(.bordered)
                        .tint(.white)
                }

                Button("Afslut") { endStoreMode() }
                    .font(.caption2.weight(.bold))
                    .buttonStyle(.bordered)
                    .tint(.white)
            }

            if storeModeProgress.total > 0 {
                ProgressView(value: storeModeProgress.completedFraction)
                    .tint(.white)
                    .background(.white.opacity(0.20), in: Capsule())

                HStack {
                    Text(storeModeProgress.isComplete
                         ? "Turen er færdig"
                         : "\(storeModeProgress.remaining) varer tilbage")
                        .font(.caption.weight(.bold))
                    Spacer()
                    Text("\(storeModeProgress.purchased) købt")
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.80))
                }
            }
        }
        .foregroundStyle(.white)
        .padding(14)
        .background(
            LinearGradient(
                colors: [Color.green.opacity(0.92), Color.teal.opacity(0.92)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            ),
            in: RoundedRectangle(cornerRadius: 20, style: .continuous)
        )
        .listRowInsets(EdgeInsets(top: 5, leading: 16, bottom: 6, trailing: 16))
        .listRowSeparator(.hidden)
        .listRowBackground(Color.clear)
    }

    private var storeModeEmptyRow: some View {
        HStack(spacing: 12) {
            Image(systemName: "checkmark.circle.fill")
                .font(.title)
                .foregroundStyle(.green)
            VStack(alignment: .leading, spacing: 2) {
                Text(storeModePurchasedItems.isEmpty ? "Ingen varer til denne tur" : "Alt er i kurven")
                    .font(.headline)
                Text(storeModePurchasedItems.isEmpty
                     ? "Varer til andre butikker er skjult. Varer uden butik vises altid her."
                     : "Du kan fortryde en afkrydsning under Købt nedenfor.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(16)
        .listRowSeparator(.hidden)
    }

    private func storeModeSectionHeader(
        _ category: ShoppingCategory,
        count: Int,
        isNext: Bool
    ) -> some View {
        HStack(spacing: 8) {
            if isNext {
                Text("NÆSTE")
                    .font(.caption2.weight(.heavy))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 4)
                    .background(Color.accentColor, in: Capsule())
            }

            Image(systemName: category.icon)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(Color.accentColor)
            Text(category.rawValue)
                .font(.headline)
                .foregroundStyle(.primary)
                .textCase(nil)
            Text("\(count)")
                .font(.caption.weight(.bold))
                .foregroundStyle(.secondary)
                .padding(.horizontal, 7)
                .padding(.vertical, 3)
                .background(Color.primary.opacity(0.065), in: Capsule())
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, isNext ? 7 : 3)
        .padding(.bottom, 5)
        .background(Color(uiColor: .systemGroupedBackground))
    }

    private var storeModePurchasedSection: some View {
        Section {
            Button {
                withAnimation(.easeInOut(duration: 0.2)) {
                    showStoreModePurchased.toggle()
                }
            } label: {
                HStack(spacing: 11) {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.title3)
                        .foregroundStyle(.green)
                    Text("Købt")
                        .font(.headline)
                        .foregroundStyle(.primary)
                    Text("\(storeModePurchasedItems.count)")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(Color.primary.opacity(0.055), in: Capsule())
                    Spacer()
                    Text("Tryk igen ved fejl")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Image(systemName: showStoreModePurchased ? "chevron.up" : "chevron.down")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(.secondary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .background(
                Color(uiColor: .secondarySystemGroupedBackground),
                in: RoundedRectangle(cornerRadius: 17, style: .continuous)
            )
            .listRowInsets(EdgeInsets(top: 12, leading: 16, bottom: showStoreModePurchased ? 4 : 14, trailing: 16))
            .listRowSeparator(.hidden)
            .listRowBackground(Color.clear)

            if showStoreModePurchased {
                ForEach(storeModePurchasedItems, id: \.stableID) { item in
                    itemRow(item, showCategory: true)
                        .opacity(0.72)
                }
            }
        }
    }

    private var addItemRow: some View {
        HStack(spacing: 10) {
            Image(systemName: "plus")
                .font(.subheadline.weight(.bold))
                .foregroundStyle(Color.accentColor)
                .frame(width: 28, height: 28)
                .background(Color.accentColor.opacity(0.11), in: Circle())

            TextField("Tilføj vare", text: $newItem)
                .font(.body.weight(.medium))
                .textInputAutocapitalization(.sentences)
                .submitLabel(.done)
                .onSubmit { addNewItem() }

            if !newItem.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                Button(action: addNewItem) {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.title2)
                        .symbolRenderingMode(.hierarchical)
                }
                .buttonStyle(.plain)
                .transition(.scale.combined(with: .opacity))
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(
            Color(uiColor: .secondarySystemGroupedBackground),
            in: RoundedRectangle(cornerRadius: 16, style: .continuous)
        )
        .listRowInsets(EdgeInsets(top: 6, leading: 16, bottom: 3, trailing: 16))
        .listRowSeparator(.hidden)
        .listRowBackground(Color.clear)
    }

    private var sortModeRow: some View {
        HStack(spacing: 4) {
            sortModeButton(
                title: "Kategori",
                systemImage: "square.grid.2x2",
                selected: !sortByRetailer
            ) {
                setSortMode(byRetailer: false)
            }

            sortModeButton(
                title: "Butik",
                systemImage: "storefront",
                selected: sortByRetailer
            ) {
                setSortMode(byRetailer: true)
            }
        }
        .padding(3)
        .background(
            Color(uiColor: .secondarySystemGroupedBackground),
            in: RoundedRectangle(cornerRadius: 12, style: .continuous)
        )
        .listRowInsets(EdgeInsets(top: 1, leading: 16, bottom: 0, trailing: 16))
        .listRowSeparator(.hidden)
        .listRowBackground(Color.clear)
    }

    private func sortModeButton(
        title: String,
        systemImage: String,
        selected: Bool,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Image(systemName: systemImage)
                    .font(.caption.weight(.semibold))
                Text(title)
                    .font(.subheadline.weight(selected ? .semibold : .medium))
            }
            .foregroundStyle(selected ? Color.primary : Color.secondary)
            .frame(maxWidth: .infinity)
            .frame(height: 30)
            .background(
                selected ? Color.primary.opacity(0.10) : Color.clear,
                in: RoundedRectangle(cornerRadius: 9, style: .continuous)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func setSortMode(byRetailer: Bool) {
        guard sortByRetailer != byRetailer else { return }
        withAnimation(.easeInOut(duration: 0.18)) {
            sortByRetailer = byRetailer
            if !byRetailer {
                selectedRetailerFilters.removeAll()
            }
        }
    }

    private var retailerFiltersRow: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                retailerFilterButton(title: "Alle", selected: selectedRetailerFilters.isEmpty) {
                    withAnimation { selectedRetailerFilters.removeAll() }
                }

                ForEach(retailerFilterOptions, id: \.self) { retailer in
                    retailerFilterButton(
                        title: retailer,
                        selected: selectedRetailerFilters.contains(retailer)
                    ) {
                        withAnimation {
                            if selectedRetailerFilters.contains(retailer) {
                                selectedRetailerFilters.remove(retailer)
                            } else {
                                selectedRetailerFilters.insert(retailer)
                            }
                        }
                    }
                }
            }
            .padding(.horizontal, 1)
        }
        .listRowInsets(EdgeInsets(top: 0, leading: 16, bottom: 2, trailing: 0))
        .listRowSeparator(.hidden)
        .listRowBackground(Color.clear)
    }

    private var emptyActiveRow: some View {
        HStack(spacing: 12) {
            Image(systemName: "checkmark.circle.fill")
                .font(.title2)
                .foregroundStyle(.green)
            VStack(alignment: .leading, spacing: 2) {
                Text("Alt er købt")
                    .font(.headline)
                Text("Tilføj en vare ovenfor, når der mangler noget igen.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(
            Color(uiColor: .secondarySystemGroupedBackground),
            in: RoundedRectangle(cornerRadius: 16, style: .continuous)
        )
        .listRowInsets(EdgeInsets(top: 2, leading: 16, bottom: 5, trailing: 16))
        .listRowSeparator(.hidden)
        .listRowBackground(Color.clear)
    }

    private var checkedItemsSection: some View {
        Section {
            Button {
                withAnimation(.easeInOut(duration: 0.2)) {
                    showCheckedItems.toggle()
                }
            } label: {
                HStack(spacing: 12) {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.title3)
                        .foregroundStyle(.secondary)
                    Text("Købt")
                        .font(.headline)
                        .foregroundStyle(.primary)
                    Text("\(checkedItems.count)")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(Color.primary.opacity(0.055), in: Capsule())
                    Spacer()
                    Image(systemName: showCheckedItems ? "chevron.up" : "chevron.down")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(.secondary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .padding(.horizontal, 14)
            .padding(.vertical, 11)
            .background(
                Color(uiColor: .secondarySystemGroupedBackground),
                in: RoundedRectangle(cornerRadius: 16, style: .continuous)
            )
            .listRowInsets(EdgeInsets(top: 5, leading: 16, bottom: showCheckedItems ? 3 : 12, trailing: 16))
            .listRowSeparator(.hidden)
            .listRowBackground(Color.clear)

            if showCheckedItems {
                ForEach(checkedItems, id: \.stableID) { item in
                    itemRow(item, showCategory: true)
                }

                Button(role: .destructive) {
                    mutationQueue.enqueue {
                        await model.clearChecked()
                    }
                } label: {
                    HStack {
                        Spacer()
                        Label("Ryd købte varer", systemImage: "trash")
                            .font(.subheadline.weight(.semibold))
                        Spacer()
                    }
                    .padding(.vertical, 10)
                }
                .buttonStyle(.plain)
                .background(Color.red.opacity(0.07), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .listRowInsets(EdgeInsets(top: 2, leading: 16, bottom: 12, trailing: 16))
                .listRowSeparator(.hidden)
                .listRowBackground(Color.clear)
            }
        }
    }

    private func memberPriceReminder(for group: RetailerGroup) -> String? {
        guard let retailer = group.retailer else { return nil }
        let metadata = group.items.compactMap { model.offerMetadataReference(for: $0) }
        return MemberPriceReminder.message(
            retailer: retailer,
            storeItems: group.items,
            metadata: metadata
        )
    }

    private func sectionHeader(
        _ title: String,
        count: Int,
        icon: String,
        reminder: String? = nil
    ) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 7) {
                Image(systemName: icon)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.accentColor)
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Color.primary.opacity(0.88))
                    .textCase(nil)
                Text("\(count)")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(Color.secondary.opacity(0.92))
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(Color.primary.opacity(0.065), in: Capsule())
            }

            if let reminder {
                Label(reminder, systemImage: "tag.fill")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.red)
                    .textCase(nil)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 5)
        .background(Color(uiColor: .systemGroupedBackground))
    }

    private func addNewItem() {
        let value = newItem
        guard !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        newItem = ""
        Task {
            if !(await model.addManualItemResponsive(value)) {
                newItem = value
            }
        }
    }

    private func retailerFilterButton(
        title: String,
        selected: Bool,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Text(title)
                .font(.caption.weight(selected ? .semibold : .medium))
                .foregroundStyle(selected ? Color.white : Color.primary)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(
                    selected ? Color.accentColor : Color(uiColor: .secondarySystemGroupedBackground),
                    in: Capsule()
                )
        }
        .buttonStyle(.plain)
    }

    private func togglePurchasedState(for item: ShoppingItem) {
        guard item.id != nil else { return }

        Task { @MainActor in
            let targetChecked = !item.checked
            let category = model.category(for: item)
            let store = activeStoreMode

            if let store {
                withAnimation(.snappy(duration: 0.22)) {
                    updatePurchasedTracking(
                        itemID: item.stableID,
                        storeID: store.id,
                        purchased: targetChecked
                    )
                }
            }

            await model.setChecked(item, checked: targetChecked)
            let didApply = model.shoppingList?.items
                .first(where: { $0.stableID == item.stableID })?
                .checked == targetChecked

            guard didApply else {
                if let store {
                    withAnimation(.snappy(duration: 0.22)) {
                        updatePurchasedTracking(
                            itemID: item.stableID,
                            storeID: store.id,
                            purchased: !targetChecked
                        )
                    }
                }
                return
            }

            if targetChecked, let store {
                model.storeLayouts.recordPurchased(category: category, at: store)
            }
        }
    }

    private func updatePurchasedTracking(
        itemID: String,
        storeID: String,
        purchased: Bool
    ) {
        var itemIDs = storeModePurchasedIDsByStore[storeID] ?? []
        itemIDs.removeAll { $0 == itemID }
        if purchased { itemIDs.append(itemID) }
        storeModePurchasedIDsByStore[storeID] = itemIDs
        if purchased { showStoreModePurchased = true }
    }

    @ViewBuilder
    private func itemRow(_ item: ShoppingItem, showCategory: Bool) -> some View {
        HStack(spacing: activeStoreMode == nil ? 12 : 15) {
            Button {
                togglePurchasedState(for: item)
            } label: {
                Image(systemName: item.checked ? "checkmark.circle.fill" : "circle")
                    .font(.system(size: activeStoreMode == nil ? 24 : 36, weight: .semibold))
                    .foregroundStyle(
                        item.checked
                            ? (activeStoreMode == nil ? Color.secondary : Color.green)
                            : Color.accentColor
                    )
                    .frame(
                        width: activeStoreMode == nil ? 28 : 50,
                        height: activeStoreMode == nil ? 34 : 50
                    )
                    .contentShape(Circle())
                    .contentTransition(.symbolEffect(.replace))
            }
            .buttonStyle(.plain)
            .disabled(item.id == nil || model.mutatingItemIDs.contains(item.stableID))
            .accessibilityLabel(item.checked ? "Markér som ikke købt" : "Markér som købt")

            VStack(alignment: .leading, spacing: 5) {
                Text(item.name)
                    .font(activeStoreMode == nil
                          ? .body.weight(item.checked ? .regular : .semibold)
                          : .title3.weight(item.checked ? .medium : .semibold))
                    .strikethrough(item.checked)
                    .foregroundStyle(item.checked ? .secondary : .primary)
                    .lineLimit(2)

                if let retailer = model.offerRetailer(for: item) {
                    Button {
                        showOfferPreview(for: item)
                    } label: {
                        HStack(spacing: 5) {
                            Image(systemName: "tag.fill")
                            Text(retailer).lineLimit(1)
                            if let price = model.offerPrice(for: item) {
                                Text("·")
                                Text(price, format: .currency(code: "DKK").precision(.fractionLength(price.rounded() == price ? 0 : 2)))
                                    .monospacedDigit()
                            }
                            if model.offerMetadataReference(for: item)?.offerID != nil {
                                Image(systemName: "photo")
                            }
                        }
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Color.accentColor)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 5)
                        .background(Color.accentColor.opacity(0.10), in: Capsule())
                    }
                    .buttonStyle(.plain)
                    .disabled(model.offerMetadataReference(for: item)?.offerID == nil)

                    if let offer = model.offerMetadataReference(for: item)?.offerSnapshot,
                       offer.memberPrice != nil {
                        MemberPriceBadge(offer: offer, compact: true)
                    }

                    if !item.checked, let status = offerStatus(for: item) {
                        Label(status.label, systemImage: status.icon)
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(status.color)
                    }
                } else if !item.checked, let expired = model.expiredOfferMetadata(for: item) {
                    Button {
                        showOfferPreview(for: item)
                    } label: {
                        HStack(spacing: 5) {
                            Image(systemName: "calendar.badge.exclamationmark")
                            Text(expired.retailer).lineLimit(1)
                            if let price = expired.price {
                                Text("·")
                                Text(price, format: .currency(code: "DKK").precision(.fractionLength(price.rounded() == price ? 0 : 2)))
                            }
                            Text("· Udløbet")
                        }
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.red)
                    }
                    .buttonStyle(.plain)
                    .disabled(model.offerMetadataReference(for: item)?.offerID == nil)
                }

                if !item.checked, model.offerRetailer(for: item) == nil {
                    let matches = smartOffers.matches(for: item)
                    if !matches.isEmpty {
                        Button {
                            offerTarget = ShoppingItemOfferTarget(item: item)
                        } label: {
                            Label(
                                matches.count == 1 ? "1 tilbud" : "\(matches.count) tilbud",
                                systemImage: "tag"
                            )
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(Color.accentColor)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .background(Color.accentColor.opacity(0.045), in: Capsule())
                        }
                        .buttonStyle(.plain)
                    }
                }

                if showCategory {
                    let category = model.category(for: item)
                    Label(category.rawValue, systemImage: category.icon)
                        .font(.caption2.weight(.medium))
                        .foregroundStyle(.secondary)
                }
            }

            Spacer(minLength: 6)

            if let quantity = item.quantity, quantity > 1, let displayQuantity = item.displayQuantity {
                Text(displayQuantity)
                    .font(.caption.weight(.bold))
                    .monospacedDigit()
                    .foregroundStyle(Color.accentColor)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 5)
                    .background(Color.accentColor.opacity(0.10), in: Capsule())
                    .accessibilityLabel("Antal \(displayQuantity)")
            }

            if item.id == nil {
                ProgressView()
                    .controlSize(.small)
            } else {
                Menu {
                    Section("Antal") {
                        ForEach(1...10, id: \.self) { quantity in
                            Button {
                                Task { await model.setQuantity(item, quantity: Double(quantity)) }
                            } label: {
                                if Int(item.quantity ?? 1) == quantity {
                                    Label("\(quantity) stk", systemImage: "checkmark")
                                } else {
                                    Text("\(quantity) stk")
                                }
                            }
                        }
                    }

                    if model.hasCategoryOverride(for: item) {
                        Button {
                            model.resetCategory(for: item)
                        } label: {
                            Label("Brug automatisk kategori", systemImage: "wand.and.stars")
                        }
                    }

                    Section("Flyt til kategori") {
                        ForEach(ShoppingCategory.allCases) { category in
                            Button {
                                model.setCategory(category, for: item)
                            } label: {
                                if model.category(for: item) == category {
                                    Label(category.rawValue, systemImage: "checkmark")
                                } else {
                                    Text(category.rawValue)
                                }
                            }
                        }
                    }
                } label: {
                    Image(systemName: "ellipsis")
                        .font(.body.weight(.semibold))
                        .foregroundStyle(.secondary)
                        .frame(width: 28, height: 32)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Indstillinger for \(item.name)")
            }

            if model.mutatingItemIDs.contains(item.stableID) {
                ProgressView().controlSize(.small)
            }
        }
        .padding(.horizontal, activeStoreMode == nil ? 13 : 15)
        .padding(.vertical, activeStoreMode == nil ? 9 : 15)
        .background(
            Color(uiColor: .secondarySystemGroupedBackground),
            in: RoundedRectangle(cornerRadius: activeStoreMode == nil ? 16 : 20, style: .continuous)
        )
        .contentShape(Rectangle())
        .simultaneousGesture(
            LongPressGesture(minimumDuration: 0.55)
                .onEnded { _ in
                    guard item.id != nil,
                          !model.mutatingItemIDs.contains(item.stableID) else { return }
                    renameTarget = ShoppingItemRenameTarget(item: item)
                }
        )
        .accessibilityAction(named: "Rediger navn") {
            guard item.id != nil else { return }
            renameTarget = ShoppingItemRenameTarget(item: item)
        }
        .swipeActions(edge: .trailing, allowsFullSwipe: true) {
            if item.id != nil {
                Button(role: .destructive) {
                    mutationQueue.enqueue {
                        await model.deleteItem(item)
                    }
                } label: {
                    Label("Slet", systemImage: "trash")
                }
            }
        }
        .listRowInsets(EdgeInsets(
            top: activeStoreMode == nil ? 2 : 4,
            leading: 16,
            bottom: activeStoreMode == nil ? 2 : 4,
            trailing: 16
        ))
        .listRowSeparator(.hidden)
        .listRowBackground(Color.clear)
    }

    private func showOfferPreview(for item: ShoppingItem) {
        guard let metadata = model.offerMetadataReference(for: item),
              metadata.offerID != nil,
              metadata.publicationID != nil else { return }
        offerPreviewTarget = ShoppingItemOfferPreviewTarget(metadata: metadata)
    }

    private func offerStatus(for item: ShoppingItem) -> (label: String, icon: String, color: Color)? {
        switch model.offerState(for: item) {
        case .upcoming(let date):
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "da_DK")
            formatter.dateFormat = "'Tilbud fra' EEEE d. MMMM"
            return (formatter.string(from: date), "calendar.badge.clock", .indigo)
        case .expiresSoon:
            return ("Udløber snart", "clock.badge.exclamationmark", .orange)
        case .expired:
            return ("Tilbud udløbet", "calendar.badge.exclamationmark", .red)
        case .active, .none:
            return nil
        }
    }
}

private struct ShoppingItemOfferPreviewSheet: View {
    let metadata: OfferMetadataDTO
    @Environment(\.dismiss) private var dismiss
    @State private var offer: GroceryOffer?
    @State private var errorMessage: String?
    private let api = APIClient()

    var body: some View {
        Group {
            if let offer {
                OfferPreviewSheet(offer: offer)
            } else if let errorMessage {
                NavigationStack {
                    ContentUnavailableView(
                        "Tilbudsbilledet kunne ikke hentes",
                        systemImage: "photo.badge.exclamationmark",
                        description: Text(errorMessage)
                    )
                    .navigationTitle("Se tilbud")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .confirmationAction) {
                            Button("Luk") { dismiss() }
                        }
                    }
                }
            } else {
                ProgressView("Henter tilbud …")
            }
        }
        .task { await load() }
    }

    @MainActor private func load() async {
        if let snapshot = metadata.offerSnapshot {
            offer = snapshot
            return
        }
        guard let publicationID = metadata.publicationID,
              let offerID = metadata.offerID else {
            errorMessage = "Varen har ikke længere en reference til det oprindelige tilbud."
            return
        }
        do {
            let response = try await api.fetchOffers(publicationID: publicationID)
            guard let match = response.offers.first(where: { $0.id == offerID }) else {
                errorMessage = "Det oprindelige tilbud findes ikke længere i avisen."
                return
            }
            offer = match
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

private struct RenameShoppingItemView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    let item: ShoppingItem
    @State private var name: String
    @State private var isSaving = false
    @State private var localError: String?

    init(item: ShoppingItem) {
        self.item = item
        _name = State(initialValue: item.name)
    }

    private var trimmedName: String {
        name
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
    }

    private var duplicateExists: Bool {
        let wanted = ShoppingCategoryService.normalize(trimmedName)
        guard !wanted.isEmpty else { return false }
        return model.shoppingList?.items.contains { candidate in
            candidate.stableID != item.stableID
                && ShoppingCategoryService.normalize(candidate.name) == wanted
        } ?? false
    }

    private var canSave: Bool {
        item.id != nil
            && !trimmedName.isEmpty
            && trimmedName != item.name
            && !duplicateExists
            && !isSaving
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Navn", text: $name)
                        .textInputAutocapitalization(.sentences)
                        .submitLabel(.done)
                        .onSubmit {
                            if canSave { Task { await save() } }
                        }
                } header: {
                    Text("Varenavn")
                } footer: {
                    Text("Navnet ændres på den eksisterende Samsung Food-vare. Antal, købt-status, kategori og eventuelle tilbudsoplysninger bevares.")
                }

                if duplicateExists {
                    Section {
                        Label("Der findes allerede en vare med det navn på listen.", systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.orange)
                    }
                }

                if let localError {
                    Section {
                        Text(localError)
                            .foregroundStyle(.red)
                    }
                }

                if isSaving {
                    Section {
                        HStack {
                            ProgressView()
                            Text("Opdaterer vare…")
                        }
                    }
                }
            }
            .navigationTitle("Rediger vare")
            .navigationBarTitleDisplayMode(.inline)
            .interactiveDismissDisabled(isSaving)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Annuller") { dismiss() }
                        .disabled(isSaving)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Gem") {
                        Task { await save() }
                    }
                    .disabled(!canSave)
                }
            }
        }
    }

    @MainActor
    private func save() async {
        guard canSave else { return }
        isSaving = true
        localError = nil
        defer { isSaving = false }

        let categoryOverride = model.hasCategoryOverride(for: item)
            ? model.category(for: item)
            : nil

        do {
            let result = try await ItemRenameService().rename(
                item: item,
                to: trimmedName,
                categoryOverride: categoryOverride
            )
            await model.refresh()
            await model.syncSharedCategories()
            if let warning = result.warning {
                model.errorMessage = warning
            }
            dismiss()
        } catch {
            localError = error.localizedDescription
        }
    }
}
