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
    @State private var inviteCode: String?
    @State private var showingHouseholdSetup = false

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
                Section("Familie") {
                    if let profile = model.householdProfile {
                        LabeledContent("Familie", value: profile.householdName)
                        LabeledContent("Medlem", value: profile.memberName)
                        Button("Invitér familiemedlem") {
                            Task { inviteCode = await model.createInvite() }
                        }
                        if profile.role == "owner" {
                            NavigationLink("Administrér familiemedlemmer") {
                                HouseholdMembersView(model: model)
                            }
                            NavigationLink("Gendannelse og sikkerhed") {
                                HouseholdRecoveryView(model: model)
                            }
                        }
                        if let inviteCode {
                            VStack(alignment: .leading, spacing: 5) {
                                Text("Invitationskode").font(.caption).foregroundStyle(.secondary)
                                Text(inviteCode).font(.title2.monospaced().bold()).textSelection(.enabled)
                                Text("Koden kan bruges én gang og udløber efter 7 dage.")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    } else {
                        Button("Opret eller tilslut familie") { showingHouseholdSetup = true }
                        Text("Hver familie har sin egen private indkøbsliste og egne tilbudsoplysninger.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }

                Section("Integrationer") {
                    NavigationLink {
                        SamsungFoodIntegrationView(model: model)
                    } label: {
                        Label("Samsung Food", systemImage: "refrigerator")
                    }
                    Text("Integrationer tilhører familien og opbevares sikkert på QNAP – ikke på den enkelte telefon.")
                        .font(.caption).foregroundStyle(.secondary)
                }

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
            .sheet(isPresented: $showingHouseholdSetup) {
                HouseholdSetupView(model: model)
            }
        }
    }
}

private struct HouseholdMembersView: View {
    @ObservedObject var model: AppModel
    @State private var members: [HouseholdMember] = []
    @State private var editing: HouseholdMember?
    @State private var editedName = ""
    @State private var removing: HouseholdMember?

    var body: some View {
        List {
            ForEach(members) { member in
                HStack {
                    VStack(alignment: .leading) {
                        Text(member.name)
                        Text(member.role == "owner" ? "Administrator" : "Familiemedlem")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Menu {
                        Button("Redigér navn", systemImage: "pencil") {
                            editedName = member.name
                            editing = member
                        }
                        if member.id != "legacy-owner" {
                            Button(member.role == "owner" ? "Tilbagekald denne adgang" : "Fjern fra familien", systemImage: "person.badge.minus", role: .destructive) {
                                removing = member
                            }
                        }
                    } label: { Image(systemName: "ellipsis.circle") }
                }
            }
        }
        .navigationTitle("Familiemedlemmer")
        .task { await reload() }
        .alert("Redigér navn", isPresented: Binding(get: { editing != nil }, set: { if !$0 { editing = nil } })) {
            TextField("Navn", text: $editedName)
            Button("Gem") {
                guard let member = editing else { return }
                Task {
                    if await model.updateHouseholdMember(id: member.id, name: editedName) { await reload() }
                    editing = nil
                }
            }
            Button("Annuller", role: .cancel) { editing = nil }
        }
        .confirmationDialog("Fjern \(removing?.name ?? "familiemedlem")?", isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } }), titleVisibility: .visible) {
            Button("Fjern fra familien", role: .destructive) {
                guard let member = removing else { return }
                Task {
                    if await model.removeHouseholdMember(id: member.id) { await reload() }
                    removing = nil
                }
            }
            Button("Annuller", role: .cancel) { removing = nil }
        } message: {
            Text("Medlemmets adgang fjernes med det samme. Familiens indkøbsliste påvirkes ikke.")
        }
    }

    @MainActor private func reload() async {
        if let loaded = await model.householdMembers() { members = loaded }
    }
}

private struct HouseholdRecoveryView: View {
    @ObservedObject var model: AppModel
    @State private var code: String?
    @State private var confirmingRotation = false

    var body: some View {
        Form {
            Section("Gendannelseskode") {
                if let code {
                    Text(code).font(.title3.monospaced().bold()).textSelection(.enabled)
                    ShareLink(item: code) { Label("Gem eller del sikkert", systemImage: "square.and.arrow.up") }
                    Text("Den tidligere kode er nu ugyldig.").font(.caption).foregroundStyle(.secondary)
                } else {
                    Text("Af sikkerhedsgrunde kan den eksisterende kode ikke vises igen. Opret en ny, hvis du ikke længere har den gemt.")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                Button(code == nil ? "Opret ny gendannelseskode" : "Generér en anden kode") { confirmingRotation = true }
            }
            Section {
                Text("En gendannelseskode kan udstede en ny administratoradgang på en ny telefon uden at oprette en tom familie eller ændre listen og integrationerne.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .navigationTitle("Gendannelse")
        .confirmationDialog("Generér ny kode?", isPresented: $confirmingRotation, titleVisibility: .visible) {
            Button("Generér ny kode") { Task { code = await model.rotateRecoveryCode() } }
            Button("Annuller", role: .cancel) {}
        } message: { Text("En tidligere gendannelseskode stopper med at virke med det samme.") }
    }
}

private struct SamsungFoodIntegrationView: View {
    @ObservedObject var model: AppModel
    @Environment(\.openURL) private var openURL
    @State private var integration: SamsungIntegrationStatus?
    @State private var loading = true
    @State private var disconnecting = false
    @State private var confirmingDisconnect = false
    @State private var resultMessage: String?
    @State private var loginSession: SamsungLoginStartResponse?
    @State private var availableLists: [SamsungListChoice] = []
    @State private var selectedListID: String?
    @State private var loginWorking = false
    private let api = APIClient()

    var body: some View {
        Form {
            if loading {
                Section { ProgressView("Henter integrationsstatus …") }
            } else if let integration {
                Section("Status") {
                    LabeledContent("Samsung Food") {
                        Label(statusText(integration.status), systemImage: statusIcon(integration.status))
                            .foregroundStyle(statusColor(integration.status))
                    }
                    if let listName = integration.listName {
                        LabeledContent("Tilknyttet liste", value: listName)
                    }
                    if let timestamp = integration.lastSuccessfulSync {
                        LabeledContent("Seneste synkronisering", value: Date(timeIntervalSince1970: TimeInterval(timestamp)).formatted(date: .abbreviated, time: .shortened))
                    }
                    if let error = integration.errorMessage {
                        Label(error, systemImage: "exclamationmark.triangle.fill")
                            .font(.subheadline).foregroundStyle(.orange)
                    }
                }

                Section("Handlinger") {
                    if integration.status == "connected" {
                        Button("Forbind igen") { Task { await startLogin() } }
                            .disabled(!integration.canManage || !integration.selfServiceLoginAvailable || loginWorking)
                        Button("Afbryd forbindelse", role: .destructive) { confirmingDisconnect = true }
                            .disabled(!integration.canManage || disconnecting)
                    } else {
                        Button(integration.status == "requires_reconnect" ? "Forbind igen" : "Forbind Samsung Food") {
                            Task { await startLogin() }
                        }
                        .disabled(!integration.canManage || !integration.selfServiceLoginAvailable || loginWorking)
                    }
                    if integration.canManage && !integration.selfServiceLoginAvailable {
                        Text("Det sikre login skal først aktiveres på familiens QNAP.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Text("Samsung-login foregår direkte i en midlertidig QNAP-browser. Kurv ser eller modtager aldrig din Samsung-adgangskode.")
                        .font(.caption).foregroundStyle(.secondary)
                    if !integration.canManage {
                        Text("Kun familiens administrator kan ændre integrationen.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }

                if let loginSession {
                    Section("Midlertidigt login") {
                        LabeledContent("Udløber", value: Date(timeIntervalSince1970: TimeInterval(loginSession.expiresAt)).formatted(date: .omitted, time: .shortened))
                        Button("Åbn Samsung-login igen") { openURL(loginSession.loginURL) }
                        Button("Jeg er færdig – kontrollér login") { Task { await checkLogin() } }
                            .disabled(loginWorking)
                    }
                }

                if !availableLists.isEmpty {
                    Section("Vælg Samsung-liste") {
                        Picker("Indkøbsliste", selection: Binding(
                            get: { selectedListID ?? availableLists.first?.id ?? "" },
                            set: { selectedListID = $0 }
                        )) {
                            ForEach(availableLists) { list in Text(list.name).tag(list.id) }
                        }
                        Button("Brug den valgte liste") { Task { await activateSelectedList() } }
                            .buttonStyle(.borderedProminent)
                            .disabled(selectedListID == nil && availableLists.isEmpty)
                    }
                }

                Section {
                    Text("Ved afbrydelse kopierer QNAP først alle aktuelle varer til familiens Kurv-liste. Tilbudsmetadata og familiens adgang bevares.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            } else {
                ContentUnavailableView("Status kunne ikke hentes", systemImage: "wifi.exclamationmark", description: Text(model.errorMessage ?? "Prøv igen senere."))
            }

            if let resultMessage {
                Section { Label(resultMessage, systemImage: "checkmark.circle.fill").foregroundStyle(.green) }
            }
        }
        .navigationTitle("Samsung Food")
        .task { await reload() }
        .refreshable { await reload() }
        .confirmationDialog("Afbryd Samsung Food?", isPresented: $confirmingDisconnect, titleVisibility: .visible) {
            Button("Afbryd og bevar listen", role: .destructive) { Task { await disconnect() } }
            Button("Annuller", role: .cancel) {}
        } message: {
            Text("QNAP kopierer først den aktuelle Samsung-liste. Der slettes ikke varer fra Samsung Food eller Kurv.")
        }
    }

    @MainActor private func reload() async {
        loading = true
        do { integration = try await api.fetchSamsungIntegration() }
        catch { model.errorMessage = error.localizedDescription; integration = nil }
        loading = false
    }

    @MainActor private func disconnect() async {
        disconnecting = true
        do {
            let response = try await api.disconnectSamsungIntegration()
            resultMessage = "\(response.preservedItemCount) varer er bevaret i \(response.preservedListName)."
            await model.refresh()
            await reload()
        } catch { model.errorMessage = error.localizedDescription }
        disconnecting = false
    }

    @MainActor private func startLogin() async {
        loginWorking = true
        do {
            let session = try await api.startSamsungLogin()
            loginSession = session
            availableLists = []
            selectedListID = nil
            openURL(session.loginURL)
        } catch { model.errorMessage = error.localizedDescription }
        loginWorking = false
    }

    @MainActor private func checkLogin() async {
        guard let loginSession else { return }
        loginWorking = true
        do {
            let status = try await api.fetchSamsungLoginStatus(sessionID: loginSession.sessionID)
            if status.status == "choose_list" {
                availableLists = status.lists
                selectedListID = status.lists.first?.id
            } else {
                resultMessage = "Samsung-login er endnu ikke afsluttet i QNAP-browseren."
            }
        } catch { model.errorMessage = error.localizedDescription }
        loginWorking = false
    }

    @MainActor private func activateSelectedList() async {
        guard let sessionID = loginSession?.sessionID,
              let listID = selectedListID else { return }
        loginWorking = true
        do {
            try await api.selectSamsungList(sessionID: sessionID, listID: listID)
            resultMessage = "Samsung Food-listen er forbundet med familien."
            loginSession = nil
            availableLists = []
            selectedListID = nil
            await model.refresh()
            await reload()
        } catch { model.errorMessage = error.localizedDescription }
        loginWorking = false
    }

    private func statusText(_ status: String) -> String {
        switch status {
        case "connected": "Forbundet"
        case "requires_reconnect": "Kræver genforbindelse"
        default: "Ikke forbundet"
        }
    }

    private func statusIcon(_ status: String) -> String {
        switch status {
        case "connected": "checkmark.circle.fill"
        case "requires_reconnect": "exclamationmark.triangle.fill"
        default: "minus.circle"
        }
    }

    private func statusColor(_ status: String) -> Color {
        switch status {
        case "connected": .green
        case "requires_reconnect": .orange
        default: .secondary
        }
    }
}

private struct HouseholdSetupView: View {
    @ObservedObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var mode = 0
    @State private var memberName = ""
    @State private var householdName = ""
    @State private var inviteCode = ""
    @State private var working = false

    var body: some View {
        NavigationStack {
            Form {
                Picker("Handling", selection: $mode) {
                    Text("Tilslut familie").tag(0)
                    Text("Opret familie").tag(1)
                }.pickerStyle(.segmented)
                TextField("Dit navn", text: $memberName)
                if mode == 0 {
                    TextField("Invitationskode", text: $inviteCode)
                        .textInputAutocapitalization(.characters).autocorrectionDisabled()
                } else {
                    TextField("Familiens navn", text: $householdName)
                }
                Button(working ? "Arbejder …" : mode == 0 ? "Tilslut familie" : "Opret familie") {
                    working = true
                    Task {
                        let ok = mode == 0
                            ? await model.joinHousehold(code: inviteCode, memberName: memberName)
                            : await model.createHousehold(name: householdName, memberName: memberName)
                        working = false
                        if ok { dismiss() }
                    }
                }
                .disabled(working || memberName.trimmingCharacters(in: .whitespaces).isEmpty || (mode == 0 ? inviteCode.isEmpty : householdName.isEmpty))
            }
            .navigationTitle("Familie")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Annuller") { dismiss() } } }
        }
    }
}
