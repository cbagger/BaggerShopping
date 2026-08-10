import SwiftUI
import CoreLocation

struct GeofenceDiagnosticsView: View {
    @ObservedObject var geofence: GeofenceManager
    let resync: () -> Void

    @State private var message: String?
    @State private var isTesting = false

    private var locationAuthorizationText: String {
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
        List {
            Section("Tilladelser") {
                LabeledContent("Placering", value: locationAuthorizationText)
                LabeledContent("Notifikationer", value: geofence.notificationAuthorizationText)
                LabeledContent("Aktive geofences", value: "\(geofence.monitoredCount) / 20")

                if let currentLocationText = geofence.currentLocationText {
                    LabeledContent("Aktuel testplacering") {
                        Text(currentLocationText)
                            .multilineTextAlignment(.trailing)
                            .font(.caption)
                    }
                }
            }

            Section("Kontrol") {
                Button {
                    resync()
                    Task {
                        await geofence.runDiagnostics()
                    }
                } label: {
                    Label("Kontrollér geofence nu", systemImage: "location.magnifyingglass")
                }

                Button {
                    Task {
                        isTesting = true
                        defer { isTesting = false }

                        do {
                            try await geofence.sendSimpleTestNotification()
                            message = "Testnotifikation sendt."
                        } catch {
                            message = error.localizedDescription
                        }
                    }
                } label: {
                    Label("Test notifikation", systemImage: "bell.badge")
                }
                .disabled(isTesting)

                Button {
                    Task {
                        isTesting = true
                        defer { isTesting = false }

                        do {
                            try await geofence.sendShoppingListTestNotification()
                            message = "Indkøbsliste-notifikation sendt."
                        } catch {
                            message = error.localizedDescription
                        }
                    }
                } label: {
                    Label("Test indkøbsliste-notifikation", systemImage: "cart.badge.plus")
                }
                .disabled(isTesting)

                if let message {
                    Text(message)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                if let lastStateCheck = geofence.lastStateCheck {
                    LabeledContent("State check", value: lastStateCheck)
                }

                Button {
                    geofence.resetCooldowns()
                    message = "Geofence-cooldown nulstillet."
                } label: {
                    Label("Nulstil geofence-cooldown", systemImage: "arrow.counterclockwise")
                }
            }

            Section("Overvågede butikker") {
                if geofence.regionDiagnostics.isEmpty {
                    Text("Ingen aktive geofences er registreret af iOS.")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(geofence.regionDiagnostics) { region in
                        VStack(alignment: .leading, spacing: 5) {
                            HStack {
                                Text(region.name)
                                    .font(.headline)

                                Spacer()

                                Text(region.state)
                                    .font(.caption.bold())
                                    .foregroundStyle(stateColor(region.state))
                            }

                            Text(
                                String(
                                    format: "%.6f, %.6f • radius %.0f m",
                                    region.latitude,
                                    region.longitude,
                                    region.radius
                                )
                            )
                            .font(.caption)
                            .foregroundStyle(.secondary)

                            if let distance = region.distanceMeters {
                                Text("Afstand fra simuleret/aktuel placering: \(Int(distance.rounded())) m")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .padding(.vertical, 3)
                    }
                }
            }

            Section("Notifikations-pipeline") {
                LabeledContent(
                    "Forsøg",
                    value: geofence.lastNotificationAttempt ?? "Ingen registreret"
                )

                LabeledContent(
                    "Liste-fetch",
                    value: geofence.lastListFetchResult ?? "Ingen registreret"
                )

                LabeledContent(
                    "Resultat",
                    value: geofence.lastNotificationResult ?? "Ingen registreret"
                )
            }

            Section("Seneste hændelser") {
                LabeledContent(
                    "Enter",
                    value: geofence.lastEnterEvent ?? "Ingen registreret"
                )
                LabeledContent(
                    "Exit",
                    value: geofence.lastExitEvent ?? "Ingen registreret"
                )

                if let error = geofence.lastMonitoringError {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Seneste fejl")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Text(error)
                            .foregroundStyle(.red)
                    }
                }
            }

            Section {
                Text("Til en Xcode-test: vælg først en GPX-position uden for butikszonen, derefter positionen inde i zonen. Tryk herefter på “Kontrollér geofence nu”. Hvis regionen viser INSIDE, men der ikke er registreret et Enter-event, er det Xcodes simulerede region-entry, der ikke er blevet leveret – ikke koordinaterne.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .navigationTitle("Geofence-diagnose")
        .task {
            await geofence.refreshNotificationAuthorization()
            await geofence.runDiagnostics()
        }
    }

    private func stateColor(_ state: String) -> Color {
        switch state {
        case "INSIDE": return .green
        case "OUTSIDE": return .secondary
        case "UNKNOWN": return .orange
        default: return .secondary
        }
    }
}
