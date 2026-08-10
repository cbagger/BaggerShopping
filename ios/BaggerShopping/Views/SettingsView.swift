import SwiftUI
import CoreLocation

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        SettingsContent(
            model: model,
            geofence: model.geofence
        )
    }
}

private struct SettingsContent: View {
    @ObservedObject var model: AppModel
    @ObservedObject var geofence: GeofenceManager

    @State private var token = ""
    @State private var saved = false

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

    var body: some View {
        NavigationStack {
            Form {
                Section("Server") {
                    LabeledContent("API", value: "shopping.chewbagger.dk")

                    SecureField("Mobil-API token", text: $token)
                        .textContentType(.password)

                    Button("Gem token") {
                        do {
                            try model.saveToken(token)
                            token = ""
                            saved = true
                            Task { await model.refresh() }
                        } catch {
                            model.errorMessage = error.localizedDescription
                        }
                    }
                    .disabled(token.isEmpty)

                    if saved {
                        Text("Token gemt i Keychain")
                            .foregroundStyle(.green)
                    }
                }

                Section("Geofencing") {
                    LabeledContent("Placering", value: authorizationText)
                    LabeledContent("Notifikationer", value: geofence.notificationAuthorizationText)
                    LabeledContent("Aktive geofences", value: "\(geofence.monitoredCount) / 20")

                    Button("Aktivér placering og notifikationer") {
                        Task {
                            await geofence.requestPermissions()
                            model.syncGeofences()
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
        }
    }
}
