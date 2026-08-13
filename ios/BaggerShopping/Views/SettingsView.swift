import SwiftUI
import CoreLocation

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        SettingsContent(
            model: model,
            geofence: model.geofence,
            categories: model.categories,
            flyerPush: model.flyerPush
        )
    }
}

private struct SettingsContent: View {
    @ObservedObject var model: AppModel
    @ObservedObject var geofence: GeofenceManager
    @ObservedObject var categories: ShoppingCategoryService
    @ObservedObject var flyerPush: FlyerPushManager

    @State private var token = ""
    @State private var saved = false
    @State private var showingTechnical = false

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

                    Text("Kurv bruger kun geofencing til at registrere ankomst til de butikker, du selv har aktiveret.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Section("Kategorier") {
                    LabeledContent("Fælles lærte rettelser", value: "\(categories.learnedCount)")

                    Text("Når en vare flyttes til en anden kategori, gemmes rettelsen på Kurv-serveren og bruges automatisk af alle appens brugere. Fælles læring kan ikke nulstilles fra appen.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Section("Notifikation om ny avis") {
                    Toggle("Send push ved nye aviser", isOn: Binding(
                        get: { flyerPush.enabled },
                        set: { value in Task { await flyerPush.setEnabled(value) } }
                    ))

                    if flyerPush.enabled {
                        ForEach(flyerPush.availableRetailers, id: \.self) { retailer in
                            Toggle(retailer, isOn: Binding(
                                get: { flyerPush.selectedRetailers.contains(retailer) },
                                set: { selected in
                                    Task { await flyerPush.updateRetailer(retailer, selected: selected) }
                                }
                            ))
                        }
                    }

                    LabeledContent("Push-tilladelse", value: flyerPush.authorizationText)
                    Text("Valgene gælder kun denne iPhone. QNAP kontrollerer aviserne og sender push, også når Kurv er lukket.")
                        .font(.caption)
                        .foregroundStyle(.secondary)

                    if let error = flyerPush.errorMessage {
                        Text(error).font(.caption).foregroundStyle(.red)
                    }
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
        }
    }
}
