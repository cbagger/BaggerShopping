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

    private var groupedActiveItems: [CategoryGroup] {
        let grouped = Dictionary(grouping: activeItems) { model.category(for: $0) }
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

                if showCategory {
                    let category = model.category(for: item)
                    Label(category.rawValue, systemImage: category.icon)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Spacer(minLength: 6)

            if let displayQuantity = item.displayQuantity {
                Text(displayQuantity)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(.quaternary, in: Capsule())
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
}
