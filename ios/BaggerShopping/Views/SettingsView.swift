import SwiftUI
import CoreLocation

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        SettingsContent(
            model: model,
            geofence: model.geofence,
            categories: model.categories
        )
    }
}

private struct SettingsContent: View {
    @ObservedObject var model: AppModel
    @ObservedObject var geofence: GeofenceManager
    @ObservedObject var categories: ShoppingCategoryService

    @State private var token = ""
    @State private var saved = false
    @State private var showingTechnical = false
    @State private var showingCategoryReset = false

    private var authorizationText: String {
        switch geofence.authorizationStatus {
        case .authorizedAlways: return "Altid"
        case .authorizedWhenInUse: return "Når appen bruges"
        case .denied: return "Afvist"
        case .restricted: return "Begrænset"
        case .notDetermined: return "Ikke valgt"
        @unknown default: return "Ukendt"
        }
    }

    private var versionText: String {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "–"
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "–"
        return "\(version) (\(build))"
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Geofencing") {
                    LabeledContent("Placering", value: authorizationText)
                    LabeledContent("Notifikationer", value: geofence.notificationAuthorizationText)
                    LabeledContent("Aktive butikker", value: "\(geofence.monitoredCount)")

                    if geofence.authorizationStatus != .authorizedAlways {
                        Button("Aktivér placering og notifikationer") {
                            Task {
                                await geofence.requestPermissions()
                                model.syncGeofences()
                            }
                        }
                    }

                    Text("Bagger Shopping bruger kun geofencing til at registrere ankomst til de butikker, du selv har aktiveret.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Section("Kategorier") {
                    LabeledContent("Fælles lærte rettelser", value: "\(categories.learnedCount)")

                    if categories.learnedCount > 0 {
                        Button("Nulstil lærte kategorier", role: .destructive) {
                            showingCategoryReset = true
                        }
                    }

                    Text("Når en vare flyttes til en anden kategori, deles rettelsen via Bagger Shopping-serveren og bruges på familiens iPhones.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Section("Om") {
                    LabeledContent("Version", value: versionText)
                }

                Section("Avanceret") {
                    DisclosureGroup("Teknisk opsætning", isExpanded: $showingTechnical) {
                        LabeledContent("API", value: "shopping.chewbagger.dk")

                        SecureField("Mobil-API token", text: $token)
                            .textContentType(.password)

                        Button("Gem token") {
                            do {
                                try model.saveToken(token)
                                token = ""
                                saved = true
                                Task {
                                    await model.refresh()
                                    await model.syncSharedCategories()
                                }
                            } catch {
                                model.errorMessage = error.localizedDescription
                            }
                        }
                        .disabled(token.isEmpty)

                        if saved {
                            Label("Token gemt i Keychain", systemImage: "checkmark.circle.fill")
                                .foregroundStyle(.green)
                        }
                    }

                    NavigationLink {
                        GeofenceDiagnosticsView(
                            geofence: geofence,
                            resync: model.syncGeofences
                        )
                    } label: {
                        Label("Geofence-diagnose", systemImage: "stethoscope")
                    }
                }

                if let error = model.errorMessage {
                    Section("Seneste fejl") {
                        Text(error)
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("Indstillinger")
            .task {
                await geofence.refreshNotificationAuthorization()
            }
            .confirmationDialog(
                "Nulstil alle lærte kategorier?",
                isPresented: $showingCategoryReset,
                titleVisibility: .visible
            ) {
                Button("Nulstil", role: .destructive) {
                    model.clearLearnedCategories()
                }
                Button("Annuller", role: .cancel) {}
            } message: {
                Text("Varerne vil igen bruge den automatiske kategorisering på familiens enheder.")
            }
        }
    }
}
