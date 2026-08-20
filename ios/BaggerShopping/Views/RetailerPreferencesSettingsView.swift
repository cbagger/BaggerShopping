import SwiftUI

struct RetailerPreferencesSettingsSection: View {
    @ObservedObject private var preferences = RetailerPreferences.shared

    var body: some View {
        Section("Aviser og tilbud") {
            NavigationLink {
                RetailerPreferencesSettingsView()
            } label: {
                HStack(spacing: 10) {
                    Label("Butikker", systemImage: "storefront")
                    Spacer()
                    Text("\(preferences.enabledCount) af \(RetailerCatalog.all.count)")
                        .foregroundStyle(.secondary)
                }
            }

            Text("Vælg hvilke butikker denne iPhone skal vise under Aviser og søge i efter nye tilbud. Allerede gemte tilbud på familiens indkøbsliste påvirkes ikke.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}

struct RetailerPreferencesSettingsView: View {
    @ObservedObject private var preferences = RetailerPreferences.shared

    var body: some View {
        List {
            Section {
                ForEach(RetailerCatalog.all, id: \.self) { retailer in
                    Toggle(
                        retailer,
                        isOn: Binding(
                            get: { preferences.isEnabled(retailer) },
                            set: { preferences.setEnabled($0, for: retailer) }
                        )
                    )
                    .disabled(
                        preferences.isEnabled(retailer)
                            && !preferences.canDisable(retailer)
                    )
                }
            } header: {
                Text("Butikker")
            } footer: {
                Text("Valget gælder kun denne iPhone. En butik, der slås fra her, skjules fra Aviser, tilbudssøgning og nye automatiske tilbudsforslag. Tilbud, som allerede er knyttet til familiens fælles indkøbsliste, bevares og vises fortsat på alle familiens telefoner.")
            }

            Section {
                Button("Vælg alle butikker") {
                    preferences.enableAll()
                }
                .disabled(preferences.enabledCount == RetailerCatalog.all.count)
            } footer: {
                Text("Mindst én butik skal være aktiv.")
            }
        }
        .navigationTitle("Aviser og tilbud")
        .navigationBarTitleDisplayMode(.inline)
    }
}
