import SwiftUI

struct OnboardingView: View {
    private enum Mode: String, CaseIterable, Identifiable {
        case create = "Opret ny familie"
        case join = "Bliv medlem"
        case recover = "Gendan familie"
        var id: String { rawValue }
        var icon: String {
            switch self { case .create: "person.2.badge.plus"; case .join: "person.badge.key"; case .recover: "arrow.counterclockwise.circle" }
        }
    }

    @EnvironmentObject private var model: AppModel
    @State private var mode: Mode?
    @State private var memberName = ""
    @State private var householdName = ""
    @State private var code = ""
    @State private var working = false
    @State private var permissionsStep = false
    @State private var recoveryCode: String?

    var body: some View {
        ZStack {
            LinearGradient(colors: [Color.accentColor.opacity(0.95), Color.accentColor.opacity(0.62)], startPoint: .topLeading, endPoint: .bottomTrailing).ignoresSafeArea()
            ScrollView {
                VStack(spacing: 22) {
                    Image("AppIcon").resizable().scaledToFit().frame(width: 104, height: 104)
                        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous)).shadow(radius: 14)
                    Text("Kurv").font(.largeTitle.bold()).foregroundStyle(.white)
                    Text("Familiens fælles indkøbsliste og tilbud – samlet ét sted.")
                        .multilineTextAlignment(.center).foregroundStyle(.white.opacity(0.9))
                    Group {
                        if let recoveryCode { recoveryCard(recoveryCode) }
                        else if permissionsStep { permissionsCard }
                        else if let mode { setupCard(mode) }
                        else { choiceCard }
                    }
                    .padding(20).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 28, style: .continuous))
                }.padding(24)
            }
        }
    }

    private var choiceCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Kom i gang").font(.title2.bold())
            ForEach(Mode.allCases) { option in
                Button { mode = option } label: {
                    Label(option.rawValue, systemImage: option.icon).frame(maxWidth: .infinity, alignment: .leading).padding(14)
                }.buttonStyle(.borderedProminent)
            }
        }
    }

    private func setupCard(_ selected: Mode) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Button("Tilbage", systemImage: "chevron.left") { mode = nil }.buttonStyle(.plain)
            Text(selected.rawValue).font(.title2.bold())
            TextField("Dit navn", text: $memberName).textFieldStyle(.roundedBorder)
            if selected == .create { TextField("Familiens navn", text: $householdName).textFieldStyle(.roundedBorder) }
            else {
                TextField(selected == .join ? "Invitationskode" : "Gendannelseskode", text: $code)
                    .textInputAutocapitalization(.characters).autocorrectionDisabled().textFieldStyle(.roundedBorder)
            }
            Button(working ? "Arbejder …" : "Fortsæt") { submit(selected) }.buttonStyle(.borderedProminent)
                .disabled(working || memberName.trimmingCharacters(in: .whitespaces).isEmpty || (selected == .create ? householdName.isEmpty : code.isEmpty))
            if let error = model.errorMessage { Text(error).font(.caption).foregroundStyle(.red) }
        }
    }

    private func recoveryCard(_ code: String) -> some View {
        VStack(spacing: 14) {
            Image(systemName: "key.viewfinder").font(.largeTitle).foregroundStyle(Color.accentColor)
            Text("Gem din gendannelseskode").font(.title2.bold())
            Text(code).font(.title3.monospaced().bold()).textSelection(.enabled)
            Text("Koden er vejen tilbage til familien, hvis alle telefoner bliver mistet eller nulstillet.")
                .font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center)
            ShareLink(item: code) { Label("Gem eller del sikkert", systemImage: "square.and.arrow.up") }
            Button("Jeg har gemt koden") { recoveryCode = nil; permissionsStep = true }.buttonStyle(.borderedProminent)
        }
    }

    private var permissionsCard: some View {
        VStack(spacing: 14) {
            Image(systemName: "location.circle.fill").font(.largeTitle).foregroundStyle(Color.accentColor)
            Text("Påmindelser ved butikker").font(.title2.bold())
            Text("Kurv kan vise indkøbslisten, når du ankommer til en butik, du selv har aktiveret.")
                .multilineTextAlignment(.center).foregroundStyle(.secondary)
            Button("Aktivér placering og notifikationer") {
                Task { await model.geofence.requestPermissions(); model.syncGeofences(); model.completeOnboarding() }
            }.buttonStyle(.borderedProminent)
            Button("Ikke nu") { model.completeOnboarding() }.buttonStyle(.plain)
        }
    }

    private func submit(_ selected: Mode) {
        working = true
        Task {
            let ok: Bool
            switch selected {
            case .create: ok = await model.createHousehold(name: householdName, memberName: memberName)
            case .join: ok = await model.joinHousehold(code: code, memberName: memberName)
            case .recover: ok = await model.recoverHousehold(code: code, memberName: memberName)
            }
            working = false
            guard ok else { return }
            if let generated = model.latestRecoveryCode { recoveryCode = generated } else { permissionsStep = true }
        }
    }
}
