import SwiftUI
import MapKit
import CoreLocation

private enum StoreAddressResolver {
    static func resolve(latitude: Double, longitude: Double) async -> String? {
        let location = CLLocation(latitude: latitude, longitude: longitude)
        let geocoder = CLGeocoder()

        do {
            guard let placemark = try await geocoder.reverseGeocodeLocation(
                location,
                preferredLocale: Locale(identifier: "da_DK")
            ).first else {
                return nil
            }

            let street: String = {
                let streetName = placemark.thoroughfare?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                let number = placemark.subThoroughfare?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""

                if !streetName.isEmpty && !number.isEmpty { return "\(streetName) \(number)" }
                if !streetName.isEmpty { return streetName }
                return placemark.name?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            }()

            let city = [placemark.postalCode, placemark.locality]
                .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }
                .joined(separator: " ")

            let components = [street, city, placemark.country ?? ""]
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }

            let address = components.joined(separator: ", ")
            return address.isEmpty ? nil : address
        } catch {
            return nil
        }
    }
}

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
                        description: Text("Søg efter en butik, eller placér den manuelt på kortet.")
                    )
                } else {
                    Section {
                        ForEach(stores.stores) { store in
                            HStack(spacing: 12) {
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

                                        Spacer(minLength: 8)

                                        Image(systemName: "chevron.right")
                                            .font(.caption.weight(.semibold))
                                            .foregroundStyle(.tertiary)
                                    }
                                    .contentShape(Rectangle())
                                }
                                .buttonStyle(.plain)

                                Toggle(
                                    "Geofence for \(store.name)",
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
            .task {
                await backfillMissingAddresses()
            }
        }
    }

    private func backfillMissingAddresses() async {
        let missing = stores.stores.filter {
            $0.address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }

        for store in missing {
            guard let address = await StoreAddressResolver.resolve(
                latitude: store.latitude,
                longitude: store.longitude
            ) else { continue }

            stores.setAddress(address, for: store.id)
        }
    }
}

private struct AddStoreSearchView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @StateObject private var search = StoreSearchService()
    @State private var query = ""
    @State private var selected: StoreSearchResult?
    @State private var radius = 100.0
    @State private var showingManualMap = false

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
                } footer: {
                    Text("Søgningen bruger Apple Kort. Hvis en butik ikke kan findes, kan du placere den manuelt nedenfor.")
                }

                Section {
                    Button {
                        showingManualMap = true
                    } label: {
                        Label("Tilføj manuelt på kort", systemImage: "mappin.and.ellipse")
                    }
                } header: {
                    Text("Kan du ikke finde butikken?")
                } footer: {
                    Text("Flyt kortet til butikkens placering, giv den et navn og vælg den samme geofence-radius som ved søgte butikker.")
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
            .sheet(isPresented: $showingManualMap) {
                ManualStoreMapView { store in
                    model.stores.add(store)
                    model.syncGeofences()
                    showingManualMap = false
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
                    Slider(value: $radius, in: 50...500, step: 25)
                    LabeledContent("Radius", value: "\(Int(radius)) m")
                    Text("100 m er standard. Du kan vælge helt ned til 50 m, hvis butikkens placering kræver en mere præcis geofence.")
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

private struct ManualStoreMapView: View {
    let onAdd: (StoreLocation) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var radius = 100.0
    @State private var latitude = 56.0
    @State private var longitude = 10.0
    @State private var address = ""
    @State private var isResolvingAddress = false
    @State private var isAdding = false
    @State private var addressLookupID = UUID()
    @State private var mapPosition: MapCameraPosition = .userLocation(
        followsHeading: false,
        fallback: .region(
            MKCoordinateRegion(
                center: CLLocationCoordinate2D(latitude: 56.0, longitude: 10.0),
                span: MKCoordinateSpan(latitudeDelta: 3.2, longitudeDelta: 4.2)
            )
        )
    )

    private var trimmedName: String {
        name.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Navn") {
                    TextField("Fx Coop 365discount", text: $name)
                        .textInputAutocapitalization(.words)
                }

                Section {
                    Map(position: $mapPosition) {
                        UserAnnotation()
                    }
                    .mapControls {
                        MapUserLocationButton()
                        MapCompass()
                        MapScaleView()
                    }
                    .onMapCameraChange(frequency: .continuous) { context in
                        latitude = context.region.center.latitude
                        longitude = context.region.center.longitude
                    }
                    .onMapCameraChange(frequency: .onEnd) { context in
                        let selectedLatitude = context.region.center.latitude
                        let selectedLongitude = context.region.center.longitude
                        latitude = selectedLatitude
                        longitude = selectedLongitude
                        address = ""
                        Task {
                            await resolveAddress(
                                latitude: selectedLatitude,
                                longitude: selectedLongitude
                            )
                        }
                    }
                    .overlay {
                        Image(systemName: "mappin.circle.fill")
                            .font(.system(size: 38, weight: .semibold))
                            .foregroundStyle(.red)
                            .offset(y: -18)
                            .allowsHitTesting(false)
                    }
                    .frame(height: 330)
                    .clipShape(RoundedRectangle(cornerRadius: 12))

                    Text("Flyt kortet, så den røde nål står på butikkens indgang eller parkeringsområde. Appen finder automatisk adressen ud fra nålens placering.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } header: {
                    Text("Placering")
                }

                Section("Adresse") {
                    if isResolvingAddress {
                        HStack(spacing: 10) {
                            ProgressView().controlSize(.small)
                            Text("Finder adresse…")
                                .foregroundStyle(.secondary)
                        }
                    } else if !address.isEmpty {
                        Label(address, systemImage: "mappin.and.ellipse")
                            .font(.subheadline)
                    } else {
                        Text("Adressen vises automatisk, når kortet står på butikkens placering.")
                            .foregroundStyle(.secondary)
                    }
                }

                Section("Geofence") {
                    Slider(value: $radius, in: 50...500, step: 25)
                    LabeledContent("Radius", value: "\(Int(radius)) m")
                    Text("100 m er standard. Manuelt tilføjede butikker bruger præcis de samme geofence-indstillinger og notifikationer som søgte butikker.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Manuel butik")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Annuller") { dismiss() }
                        .disabled(isAdding)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task { await addStore() }
                    } label: {
                        if isAdding {
                            ProgressView().controlSize(.small)
                        } else {
                            Text("Tilføj")
                        }
                    }
                    .disabled(trimmedName.isEmpty || isAdding)
                }
            }
        }
    }

    @MainActor
    private func resolveAddress(latitude selectedLatitude: Double, longitude selectedLongitude: Double) async {
        let lookupID = UUID()
        addressLookupID = lookupID
        isResolvingAddress = true

        let resolved = await StoreAddressResolver.resolve(
            latitude: selectedLatitude,
            longitude: selectedLongitude
        )

        guard addressLookupID == lookupID else { return }
        isResolvingAddress = false

        guard abs(latitude - selectedLatitude) < 0.000001,
              abs(longitude - selectedLongitude) < 0.000001 else { return }

        address = resolved ?? ""
    }

    @MainActor
    private func addStore() async {
        guard !trimmedName.isEmpty else { return }

        isAdding = true
        addressLookupID = UUID()
        let selectedLatitude = latitude
        let selectedLongitude = longitude

        let resolvedAddress = await StoreAddressResolver.resolve(
            latitude: selectedLatitude,
            longitude: selectedLongitude
        )

        let finalAddress = resolvedAddress ?? address

        onAdd(
            StoreLocation(
                name: trimmedName,
                address: finalAddress,
                latitude: selectedLatitude,
                longitude: selectedLongitude,
                radius: radius
            )
        )

        isAdding = false
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
                    Slider(value: $radius, in: 50...500, step: 25)
                    LabeledContent("Radius", value: "\(Int(radius)) m")
                    Text("Notifikationen udløses, når iOS registrerer ankomst til området omkring butikken. Radius kan vælges fra 50 til 500 m.")
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
