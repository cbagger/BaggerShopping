import SwiftUI

struct ShoppingListView: View {
    private struct CategoryGroup: Identifiable {
        let category: ShoppingCategory
        let items: [ShoppingItem]
        var id: String { category.id }
    }

    @EnvironmentObject private var model: AppModel
    @State private var newItem = ""

    private var activeItems: [ShoppingItem] {
        model.shoppingList?.items.filter { !$0.checked } ?? []
    }

    private var checkedItems: [ShoppingItem] {
        model.shoppingList?.items.filter(\.checked) ?? []
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
        let grouped = Dictionary(grouping: currentItems) { model.category(for: $0) }
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
                            if !upcomingItems.isEmpty {
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
                        }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .disabled(!model.tokenConfigured || model.isLoading)
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
            return ("Tilbud fra \(date.formatted(.dateTime.weekday(.wide).day().month(.abbreviated)))", "calendar.badge.clock", .indigo)
        case .expiresSoon:
            return ("Udløber snart", "clock.badge.exclamationmark", .orange)
        case .expired:
            return ("Tilbud udløbet", "calendar.badge.exclamationmark", .red)
        case .active, .none:
            return nil
        }
    }
}
