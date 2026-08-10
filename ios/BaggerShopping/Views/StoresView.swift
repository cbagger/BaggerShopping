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
                    ForEach(stores.stores) { store in
                        HStack {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(store.name)
                                if !store.address.isEmpty {
                                    Text(store.address)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(2)
                                }
                                Text("\(Int(store.radius)) m radius")
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
                        }
                    }
                    .onDelete { offsets in
                        stores.delete(at: offsets)
                        syncGeofences()
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
                        TextField("Fx Rema 1000 Skørping", text: $query).textInputAutocapitalization(.words).onSubmit { runSearch() }
                        if search.isSearching { ProgressView().controlSize(.small) }
                    }
                    Button("Søg") { runSearch() }.disabled(query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                } header: { Text("Søg efter butik") }

                if let error = search.errorMessage { Section { Text(error).foregroundStyle(.secondary) } }

                Section("Resultater") {
                    ForEach(search.results) { result in
                        Button {
                            selected = result
                        } label: {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(result.name).foregroundStyle(.primary)
                                Text(result.address).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            .navigationTitle("Tilføj butik")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Luk") { dismiss() } } }
            .sheet(item: $selected) { result in StoreConfirmView(result: result, radius: $radius) {
                model.stores.add(StoreLocation(name: result.name, address: result.address, latitude: result.latitude, longitude: result.longitude, radius: radius))
                model.syncGeofences()
                selected = nil
                dismiss()
            }}
        }
    }

    private func runSearch() { Task { await search.search(query) } }
}

private struct StoreConfirmView: View {
    let result: StoreSearchResult
    @Binding var radius: Double
    let onAdd: () -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("Valgt sted") { Text(result.name); Text(result.address).font(.caption).foregroundStyle(.secondary) }
                Section("Geofence") {
                    Slider(value: $radius, in: 100...500, step: 25)
                    LabeledContent("Radius", value: "\(Int(radius)) m")
                }
            }
            .navigationTitle("Bekræft butik")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Tilbage") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) { Button("Tilføj") { onAdd() } }
            }
        }
    }
}
