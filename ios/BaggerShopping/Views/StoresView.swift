import SwiftUI

struct StoresView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        StoresListContent(
            stores: model.stores,
            syncGeofences: model.syncGeofences
        )
    }
}

private struct StoresListContent: View {
    @ObservedObject var stores: StoreRepository
    let syncGeofences: () -> Void
    @State private var showingAdd = false
    @State private var editingStore: StoreLocation?

    var body: some View {
        NavigationStack {
            List {
                if stores.stores.isEmpty {
                    ContentUnavailableView(
                        "Ingen butikker endnu",
                        systemImage: "mappin.slash",
                        description: Text("Søg efter fx ‘Rema 1000 Skørping’ og tilføj butikken.")
                    )
                } else {
                    Section {
                        ForEach(stores.stores) { store in
                            Button {
                                editingStore = store
                            } label: {
                                HStack(spacing: 12) {
                                    Image(systemName: store.enabled ? "location.circle.fill" : "location.slash.circle")
                                        .font(.title2)
                                        .foregroundStyle(store.enabled ? .blue : .secondary)

                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(store.name)
                                            .foregroundStyle(.primary)
                                            .font(.headline)

                                        if !store.address.isEmpty {
                                            Text(store.address)
                                                .font(.caption)
                                                .foregroundStyle(.secondary)
                                                .lineLimit(2)
                                        }

                                        Text("Geofence · \(Int(store.radius)) m")
                                            .font(.caption2)
                                            .foregroundStyle(.secondary)
                                    }

                                    Spacer()

                                    Toggle(
                                        "",
                                        isOn: Binding(
                                            get: { store.enabled },
                                            set: { enabled in
                                                stores.setEnabled(enabled, for: store.id)
                                                syncGeofences()
                                            }
                                        )
                                    )
                                    .labelsHidden()
                                    .buttonStyle(.plain)
                                }
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                        }
                        .onDelete { offsets in
                            stores.delete(at: offsets)
                            syncGeofences()
                        }
                    } footer: {
                        Text("Aktive butikker kan give en indkøbsnotifikation, når du ankommer inden for den valgte radius.")
                    }
                }
            }
            .navigationTitle("Butikker")
            .toolbar {
                Button {
                    showingAdd = true
                } label: {
                    Image(systemName: "plus")
                }
            }
            .sheet(isPresented: $showingAdd) {
                AddStoreSearchView()
            }
            .sheet(item: $editingStore) { store in
                EditStoreView(store: store, stores: stores) {
                    syncGeofences()
                }
            }
        }
    }
}

private struct AddStoreSearchView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @StateObject private var search = StoreSearchService()
    @State private var query = ""
    @State private var selected: StoreSearchResult?
    @State private var radius = 180.0

    var body: some View {
        NavigationStack {
            List {
                Section {
                    HStack {
                        TextField("Fx Rema 1000 Skørping", text: $query)
                            .textInputAutocapitalization(.words)
                            .submitLabel(.search)
                            .onSubmit { runSearch() }

                        if search.isSearching {
                            ProgressView().controlSize(.small)
                        }
                    }

                    Button("Søg") { runSearch() }
                        .disabled(query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                } header: {
                    Text("Søg efter butik")
                }

                if let error = search.errorMessage {
                    Section {
                        Text(error).foregroundStyle(.secondary)
                    }
                }

                if !search.results.isEmpty {
                    Section("Resultater") {
                        ForEach(search.results) { result in
                            Button {
                                selected = result
                            } label: {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(result.name).foregroundStyle(.primary)
                                    Text(result.address)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Tilføj butik")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Luk") { dismiss() }
                }
            }
            .sheet(item: $selected) { result in
                StoreConfirmView(result: result, radius: $radius) {
                    model.stores.add(
                        StoreLocation(
                            name: result.name,
                            address: result.address,
                            latitude: result.latitude,
                            longitude: result.longitude,
                            radius: radius
                        )
                    )
                    model.syncGeofences()
                    selected = nil
                    dismiss()
                }
            }
        }
    }

    private func runSearch() {
        Task { await search.search(query) }
    }
}

private struct StoreConfirmView: View {
    let result: StoreSearchResult
    @Binding var radius: Double
    let onAdd: () -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("Valgt sted") {
                    Text(result.name)
                    Text(result.address)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Section("Geofence") {
                    Slider(value: $radius, in: 100...500, step: 25)
                    LabeledContent("Radius", value: "\(Int(radius)) m")
                    Text("150–250 m er typisk et godt udgangspunkt. En større radius giver tidligere besked.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Bekræft butik")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Tilbage") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Tilføj") { onAdd() }
                }
            }
        }
    }
}

private struct EditStoreView: View {
    let store: StoreLocation
    @ObservedObject var stores: StoreRepository
    let onChanged: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var radius: Double
    @State private var enabled: Bool
    @State private var showingDeleteConfirmation = false

    init(store: StoreLocation, stores: StoreRepository, onChanged: @escaping () -> Void) {
        self.store = store
        self.stores = stores
        self.onChanged = onChanged
        _radius = State(initialValue: store.radius)
        _enabled = State(initialValue: store.enabled)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Butik") {
                    Text(store.name)
                        .font(.headline)
                    if !store.address.isEmpty {
                        Text(store.address)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                Section("Geofence") {
                    Toggle("Aktiv", isOn: $enabled)
                    Slider(value: $radius, in: 100...500, step: 25)
                    LabeledContent("Radius", value: "\(Int(radius)) m")
                    Text("Notifikationen udløses, når iOS registrerer ankomst til området omkring butikken.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Section {
                    Button("Slet butik", role: .destructive) {
                        showingDeleteConfirmation = true
                    }
                }
            }
            .navigationTitle("Rediger butik")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Annuller") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Gem") {
                        stores.setEnabled(enabled, for: store.id)
                        stores.setRadius(radius, for: store.id)
                        onChanged()
                        dismiss()
                    }
                }
            }
            .confirmationDialog(
                "Slet \(store.name)?",
                isPresented: $showingDeleteConfirmation,
                titleVisibility: .visible
            ) {
                Button("Slet butik", role: .destructive) {
                    stores.delete(id: store.id)
                    onChanged()
                    dismiss()
                }
            }
        }
    }
}
