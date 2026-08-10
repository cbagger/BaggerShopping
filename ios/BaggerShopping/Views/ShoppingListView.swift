import SwiftUI

struct ShoppingListView: View {
    @EnvironmentObject private var model: AppModel
    @State private var newItem = ""

    private var activeItems: [ShoppingItem] { model.shoppingList?.items.filter { !$0.checked } ?? [] }
    private var checkedItems: [ShoppingItem] { model.shoppingList?.items.filter(\.checked) ?? [] }

    var body: some View {
        NavigationStack {
            Group {
                if !model.tokenConfigured {
                    ContentUnavailableView("API-token mangler", systemImage: "key", description: Text("Gem mobil-API tokenet under Indstillinger."))
                } else if model.isLoading && model.shoppingList == nil {
                    ProgressView("Henter indkøbsliste…")
                } else if model.shoppingList != nil {
                    List {
                        Section {
                            HStack {
                                TextField("Tilføj vare", text: $newItem).textInputAutocapitalization(.sentences)
                                Button {
                                    Task { if await model.addItem(newItem) { newItem = "" } }
                                } label: { Image(systemName: "plus.circle.fill").font(.title3) }
                                .disabled(newItem.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                            }
                        }

                        Section("\(activeItems.count) varer") {
                            ForEach(activeItems, id: \.stableID) { item in itemRow(item) }
                        }

                        if !checkedItems.isEmpty {
                            Section {
                                ForEach(checkedItems, id: \.stableID) { item in itemRow(item) }
                                Button(role: .destructive) {
                                    Task { await model.clearChecked() }
                                } label: { Label("Ryd købte varer", systemImage: "trash") }
                            } header: { Text("Købt") }
                        }
                    }
                    .refreshable { await model.refresh() }
                } else {
                    ContentUnavailableView("Ingen liste", systemImage: "cart", description: Text(model.errorMessage ?? "Kunne ikke hente indkøbslisten."))
                }
            }
            .navigationTitle("Indkøbsliste")
            .toolbar {
                Button { Task { await model.refresh() } } label: { Image(systemName: "arrow.clockwise") }
                    .disabled(!model.tokenConfigured || model.isLoading)
            }
            .alert("Fejl", isPresented: Binding(get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })) {
                Button("OK", role: .cancel) { model.errorMessage = nil }
            } message: { Text(model.errorMessage ?? "") }
        }
    }

    @ViewBuilder
    private func itemRow(_ item: ShoppingItem) -> some View {
        HStack {
            Button {
                Task { await model.setChecked(item, checked: !item.checked) }
            } label: {
                Image(systemName: item.checked ? "checkmark.circle.fill" : "circle")
                    .font(.title3)
            }
            .buttonStyle(.plain)

            Text(item.name)
                .strikethrough(item.checked)
                .foregroundStyle(item.checked ? .secondary : .primary)
            Spacer()
            if model.mutatingItemIDs.contains(item.stableID) { ProgressView().controlSize(.small) }
        }
        .swipeActions(edge: .trailing, allowsFullSwipe: true) {
            Button(role: .destructive) { Task { await model.deleteItem(item) } } label: { Label("Slet", systemImage: "trash") }
        }
    }
}
