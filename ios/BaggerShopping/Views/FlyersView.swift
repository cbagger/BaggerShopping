import SwiftUI
import WebKit

struct FlyersView: View {
    @State private var publications: [OfferPublication] = []
    @State private var isLoading = false
    @State private var errorMessage: String?
    private let api = APIClient()

    var body: some View {
        NavigationStack {
            Group {
                if isLoading && publications.isEmpty {
                    ProgressView("Henter aktuelle aviser …")
                } else if let errorMessage, publications.isEmpty {
                    ContentUnavailableView("Kunne ikke hente aviser", systemImage: "wifi.exclamationmark", description: Text(errorMessage))
                } else {
                    List(publications) { publication in
                        NavigationLink(value: publication) {
                            VStack(alignment: .leading, spacing: 7) {
                                HStack {
                                    Text(publication.retailer).font(.title3.bold())
                                    Spacer()
                                    Text(publication.status == "upcoming" ? "KOMMER SNART" : "AKTUEL")
                                        .font(.caption2.bold())
                                        .foregroundStyle(publication.status == "upcoming" ? .orange : .green)
                                }
                                Text(publication.title).font(.headline)
                                if let from = publication.validFrom, let until = publication.validUntil {
                                    Text("Gyldig \(from)–\(until)")
                                        .foregroundStyle(.secondary)
                                }
                                Text("\(publication.pageCount) sider · Åbn avis")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            .padding(.vertical, 6)
                        }
                    }
                    .refreshable { await load() }
                }
            }
            .navigationTitle("Aviser")
            .navigationDestination(for: OfferPublication.self) { publication in
                FlyerReaderView(publication: publication)
            }
            .task { await load() }
        }
    }

    @MainActor
    private func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do { publications = try await api.fetchOfferPublications().publications }
        catch { errorMessage = error.localizedDescription }
    }
}

private struct FlyerReaderView: View {
    let publication: OfferPublication

    var body: some View {
        Group {
            if let url = publication.readerURL {
                OfficialFlyerWebView(url: url)
                    .ignoresSafeArea(edges: .bottom)
            } else {
                ContentUnavailableView("Avisen kan ikke åbnes", systemImage: "doc.text.magnifyingglass")
            }
        }
        .navigationTitle(publication.retailer)
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct OfficialFlyerWebView: UIViewRepresentable {
    let url: URL

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = true
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.scrollView.contentInsetAdjustmentBehavior = .never
        view.allowsBackForwardNavigationGestures = false
        return view
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        guard webView.url != url else { return }
        webView.load(URLRequest(url: url, cachePolicy: .reloadRevalidatingCacheData))
    }
}
