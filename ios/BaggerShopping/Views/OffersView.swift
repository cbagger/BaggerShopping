import SwiftUI

struct OffersView: View {
    @State private var publication: MenyPublication?
    @State private var query = ""
    @State private var matches: [String] = []
    @State private var isLoading = false
    @State private var errorMessage: String?

    private let api = APIClient()

    var body: some View {
        NavigationStack {
            List {
                Section {
                    if let publication {
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Text("MENY")
                                    .font(.title2.bold())
                                Spacer()
                                Image(systemName: "checkmark.circle.fill")
                                    .foregroundStyle(.green)
                            }
                            Text(publication.title)
                                .font(.headline)
                            if let from = publication.validFrom, let until = publication.validUntil {
                                Text("Gyldig \(from) – \(until)")
                                    .foregroundStyle(.secondary)
                            }
                            if let pages = publication.pageCount {
                                Text("\(pages) sider · automatisk hentet fra aktuel avis")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .padding(.vertical, 4)
                    } else if isLoading {
                        ProgressView("Henter aktuel MENY-avis …")
                    }
                } header: {
                    Text("Aktuel tilbudsavis")
                }

                Section {
                    HStack {
                        TextField("Søg fx juice, vandmelon …", text: $query)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .onSubmit { Task { await search() } }

                        Button {
                            Task { await search() }
                        } label: {
                            Image(systemName: "magnifyingglass")
                        }
                        .disabled(query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isLoading)
                    }
                }

                if !matches.isEmpty {
                    Section("Fund i MENY-avisen") {
                        ForEach(Array(matches.enumerated()), id: \.offset) { _, match in
                            Text(match)
                                .font(.body)
                                .textSelection(.enabled)
                                .padding(.vertical, 4)
                        }
                    }
                } else if !query.isEmpty && !isLoading && errorMessage == nil {
                    Section {
                        ContentUnavailableView(
                            "Ingen tilbud fundet",
                            systemImage: "magnifyingglass",
                            description: Text("\"\(query)\" findes ikke i den aktuelle MENY-avis.")
                        )
                    }
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage)
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("Tilbud")
            .refreshable { await loadStatus() }
            .task { await loadStatus() }
        }
    }

    @MainActor
    private func loadStatus() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            publication = try await api.fetchMenyOfferStatus().publication
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    @MainActor
    private func search() async {
        let term = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !term.isEmpty else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let response = try await api.searchMenyOffers(query: term)
            publication = response.publication
            matches = response.matches
        } catch {
            matches = []
            errorMessage = error.localizedDescription
        }
    }
}
