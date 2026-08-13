import SwiftUI

private struct ShoppingItemRenameTarget: Identifiable {
    let id = UUID()
    let item: ShoppingItem
}

private struct ShoppingItemOfferTarget: Identifiable {
    let id = UUID()
    let item: ShoppingItem
}

struct ShoppingListView: View {
    private struct CategoryGroup: Identifiable {
        let category: ShoppingCategory
        let items: [ShoppingItem]
        var id: String { category.id }
    }

    private struct RetailerGroup: Identifiable {
        let retailer: String?
        let categories: [CategoryGroup]
        var id: String { retailer ?? "__without-retailer__" }
        var count: Int { categories.reduce(0) { $0 + $1.items.count } }
    }

    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var navigation: AppNavigation
    @StateObject private var smartOffers = SmartOfferMatchService()
    @State private var newItem = ""
    @State private var selectedRetailerFilters: Set<String> = []
    @State private var renameTarget: ShoppingItemRenameTarget?
    @State private var offerTarget: ShoppingItemOfferTarget?
    @AppStorage("shopping-list-sort-by-retailer") private var sortByRetailer = false

    private let retailerFilterOptions = [
        "MENY", "365discount", "REMA 1000", "Bilka",
        "føtex", "Lidl", "Netto", "SPAR"
    ]

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

    private var retailerGroups: [RetailerGroup] {
        let grouped = Dictionary(grouping: activeItems) { model.assignedRetailer(for: $0) }
        return grouped.map { retailer, items in
            RetailerGroup(retailer: retailer, categories: categoryGroups(for: items))
        }
        .filter { group in
            guard let retailer = group.retailer else { return true }
            return selectedRetailerFilters.isEmpty
                || selectedRetailerFilters.contains(retailer)
        }
        .sorted { lhs, rhs in
            if lhs.retailer == nil { return rhs.retailer != nil }
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
                    List {
                        Section {
                            HStack(spacing: 10) {
                                TextField("Tilføj vare", text: $newItem)
                                    .textInputAutocapitalization(.sentences)
                                    .submitLabel(.done)
                                    .onSubmit { addNewItem() }

                                Button(action: addNewItem) {
                                    Image(systemName: "plus.circle.fill")
                                        .font(.title3)
                                }
                                .disabled(newItem.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                            }
                            Button {
                                withAnimation {
                                    sortByRetailer.toggle()
                                    if !sortByRetailer { selectedRetailerFilters.removeAll() }
                                }
                            } label: {
                                Label(
                                    "Sorter efter butik",
                                    systemImage: sortByRetailer ? "checkmark.circle.fill" : "storefront"
                                )
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(sortByRetailer ? Color.white : Color.primary)
                                .padding(.horizontal, 13)
                                .padding(.vertical, 8)
                                .background(
                                    sortByRetailer ? Color.accentColor : Color(uiColor: .secondarySystemGroupedBackground),
                                    in: Capsule()
                                )
                            }
                            .buttonStyle(.plain)
                            .listRowBackground(Color.clear)
                            .listRowInsets(EdgeInsets(top: 2, leading: 0, bottom: 2, trailing: 0))

                            if sortByRetailer {
                                ScrollView(.horizontal, showsIndicators: false) {
                                    HStack(spacing: 8) {
                                        retailerFilterButton(
                                            title: "Alle",
                                            selected: selectedRetailerFilters.isEmpty
                                        ) {
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
                                    .padding(.horizontal, 2)
                                }
                                .listRowBackground(Color.clear)
                                .listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 4, trailing: 0))
                            }
                        }

                        if activeItems.isEmpty {
                            Section {
                                ContentUnavailableView(
                                    "Listen er tom",
                                    systemImage: "cart.badge.checkmark",
                                    description: Text("Tilføj varer her eller direkte fra Samsung Food på køleskabet.")
                                )
                            }
                        } else {
                            if sortByRetailer {
                                ForEach(retailerGroups) { retailerGroup in
                                    Section {
                                        ForEach(retailerGroup.categories) { categoryGroup in
                                            Label(
                                                categoryGroup.category.rawValue,
                                                systemImage: categoryGroup.category.icon
                                            )
                                            .font(.caption2.weight(.semibold))
                                            .foregroundStyle(.secondary)
                                            .listRowInsets(EdgeInsets(top: 5, leading: 20, bottom: 0, trailing: 16))
                                            .listRowSeparator(.hidden)

                                            ForEach(categoryGroup.items, id: \.stableID) { item in
                                                itemRow(item, showCategory: false)
                                                    .listRowInsets(EdgeInsets(top: 2, leading: 20, bottom: 2, trailing: 16))
                                            }
                                        }
                                    } header: {
                                        if let retailer = retailerGroup.retailer {
                                            Label("\(retailer) · \(retailerGroup.count)", systemImage: "storefront")
                                        } else {
                                            Label("Uden butik · \(retailerGroup.count)", systemImage: "tray")
                                        }
                                    } footer: {
                                        if retailerGroup.retailer != nil {
                                            Text("Husk varer øverst der ikke er dedikeret til én butik")
                                        }
                                    }
                                }
                            } else if !upcomingItems.isEmpty {
                                Section {
                                    ForEach(upcomingItems, id: \.stableID) { item in
                                        itemRow(item, showCategory: false)
                                    }
                                } header: {
                                    Label("Kommende tilbud · \(upcomingItems.count)", systemImage: "calendar.badge.clock")
                                } footer: {
                                    Text("Disse priser gælder ikke endnu.")
                                }
                            }

                            if !sortByRetailer {
                                ForEach(groupedActiveItems) { group in
                                    Section {
                                        ForEach(group.items, id: \.stableID) { item in
                                            itemRow(item, showCategory: false)
                                        }
                                    } header: {
                                        Label("\(group.category.rawValue) · \(group.items.count)", systemImage: group.category.icon)
                                    }
                                }
                            }
                        }

                        if !checkedItems.isEmpty {
                            Section {
                                ForEach(checkedItems, id: \.stableID) { item in
                                    itemRow(item, showCategory: true)
                                }

                                Button(role: .destructive) {
                                    Task { await model.clearChecked() }
                                } label: {
                                    Label("Ryd købte varer", systemImage: "trash")
                                }
                            } header: {
                                Text("Købt · \(checkedItems.count)")
                            }
                        }
                    }
                    .listStyle(.insetGrouped)
                    .refreshable {
                        await model.refresh()
                        await model.syncSharedCategories()
                        await smartOffers.refresh(items: activeItems, model: model)
                    }
                } else {
                    ContentUnavailableView(
                        "Ingen liste",
                        systemImage: "cart",
                        description: Text(model.errorMessage ?? "Kunne ikke hente indkøbslisten.")
                    )
                }
            }
            .navigationTitle("Indkøbsliste")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task {
                            await model.refresh()
                            await model.syncSharedCategories()
                            await smartOffers.refresh(items: activeItems, model: model)
                        }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .disabled(!model.tokenConfigured || model.isLoading)
                }
            }
            .sheet(item: $renameTarget) { target in
                RenameShoppingItemView(item: target.item)
                    .environmentObject(model)
            }
            .sheet(item: $offerTarget) { target in
                SmartOfferMatchesView(service: smartOffers, item: target.item)
                    .environmentObject(model)
            }
            .task(id: offerMatchSignature) {
                guard model.tokenConfigured else { return }
                await smartOffers.refresh(items: activeItems, model: model)
            }
            .onChange(of: navigation.shoppingListRoute?.id) { _, _ in
                guard let route = navigation.shoppingListRoute else { return }
                sortByRetailer = true
                selectedRetailerFilters = [route.retailer]
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
                Text(model.errorMessage ?? "")
            }
        }
    }

    private func addNewItem() {
        let value = newItem
        guard !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        newItem = ""
        Task {
            if !(await model.addItem(value)) {
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
                .font(.caption.weight(selected ? .semibold : .regular))
                .foregroundStyle(selected ? Color.white : Color.primary)
                .padding(.horizontal, 12)
                .padding(.vertical, 7)
                .background(
                    selected ? Color.accentColor : Color(uiColor: .secondarySystemGroupedBackground),
                    in: Capsule()
                )
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private func itemRow(_ item: ShoppingItem, showCategory: Bool) -> some View {
        HStack(spacing: 12) {
            Button {
                Task { await model.setChecked(item, checked: !item.checked) }
            } label: {
                Image(systemName: item.checked ? "checkmark.circle.fill" : "circle")
                    .font(.title3)
                    .contentTransition(.symbolEffect(.replace))
            }
            .buttonStyle(.plain)
            .disabled(item.id == nil)
            .accessibilityLabel(item.checked ? "Markér som ikke købt" : "Markér som købt")

            VStack(alignment: .leading, spacing: 3) {
                Text(item.name)
                    .strikethrough(item.checked)
                    .foregroundStyle(item.checked ? .secondary : .primary)

                if let retailer = model.offerRetailer(for: item) {
                    HStack(spacing: 4) {
                        Text(retailer).lineLimit(1)
                        if let price = model.offerPrice(for: item) {
                            Text("·")
                            Text(price, format: .currency(code: "DKK").precision(.fractionLength(price.rounded() == price ? 0 : 2)))
                        }
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                    if !item.checked, let status = offerStatus(for: item) {
                        Label(status.label, systemImage: status.icon)
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(status.color)
                    }
                } else if !item.checked, let expired = model.expiredOfferMetadata(for: item) {
                    HStack(spacing: 4) {
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

                if !item.checked, model.offerRetailer(for: item) == nil {
                    let matches = smartOffers.matches(for: item)
                    if !matches.isEmpty {
                        Button {
                            offerTarget = ShoppingItemOfferTarget(item: item)
                        } label: {
                            Label(
                                matches.count == 1 ? "1 tilbud fundet" : "\(matches.count) tilbud fundet",
                                systemImage: "tag"
                            )
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(Color.accentColor)
                        }
                        .buttonStyle(.plain)
                    }
                }

                if showCategory {
                    let category = model.category(for: item)
                    Label(category.rawValue, systemImage: category.icon)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Spacer(minLength: 6)

            if let quantity = item.quantity, quantity > 1, let displayQuantity = item.displayQuantity {
                Text(displayQuantity)
                    .font(.caption.weight(.bold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(.blue, in: Capsule())
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
                    Image(systemName: "ellipsis.circle")
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Indstillinger for \(item.name)")
            }

            if model.mutatingItemIDs.contains(item.stableID) {
                ProgressView().controlSize(.small)
            }
        }
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
                    Task { await model.deleteItem(item) }
                } label: {
                    Label("Slet", systemImage: "trash")
                }
            }
        }
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
