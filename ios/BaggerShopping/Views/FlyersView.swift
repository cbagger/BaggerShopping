import SwiftUI
import WebKit

struct FlyersView: View {
    @State private var publications: [OfferPublication] = []
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var selectedPublication: OfferPublication?
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
                        Button { selectedPublication = publication } label: {
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
                        .buttonStyle(.plain)
                    }
                    .refreshable { await load() }
                }
            }
            .navigationTitle("Aviser")
            .task { await load() }
            .fullScreenCover(item: $selectedPublication) { publication in
                FlyerReaderView(publication: publication)
            }
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
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var model: AppModel
    @State private var addedItemName: String?

    var body: some View {
        NavigationStack {
            Group {
                if let url = publication.readerURL {
                    OfficialFlyerWebView(url: url) { itemName in
                        Task {
                            if await model.addItem(itemName) {
                                addedItemName = itemName
                            }
                        }
                    }
                        .ignoresSafeArea(edges: .bottom)
                } else {
                    ContentUnavailableView("Avisen kan ikke åbnes", systemImage: "doc.text.magnifyingglass")
                }
            }
            .alert("Tilføjet til indkøbslisten", isPresented: Binding(
                get: { addedItemName != nil },
                set: { if !$0 { addedItemName = nil } }
            )) {
                Button("OK") { addedItemName = nil }
            } message: {
                Text(addedItemName ?? "")
            }
            .navigationTitle(publication.retailer)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Luk", systemImage: "xmark") { dismiss() }
                }
            }
        }
        .preferredColorScheme(.light)
    }
}

private struct OfficialFlyerWebView: UIViewRepresentable {
    let url: URL
    let onAddProduct: (String) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onAddProduct: onAddProduct) }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = true
        configuration.userContentController.add(context.coordinator, name: "baggerShoppingAdd")
        configuration.userContentController.addUserScript(WKUserScript(
            source: Self.bridgeScript,
            injectionTime: .atDocumentEnd,
            forMainFrameOnly: false
        ))
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.overrideUserInterfaceStyle = .light
        view.scrollView.contentInsetAdjustmentBehavior = .never
        view.allowsBackForwardNavigationGestures = false
        view.navigationDelegate = context.coordinator
        return view
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        guard webView.url != url else { return }
        webView.load(URLRequest(url: url, cachePolicy: .reloadRevalidatingCacheData))
    }

    static func dismantleUIView(_ uiView: WKWebView, coordinator: Coordinator) {
        uiView.configuration.userContentController.removeScriptMessageHandler(forName: "baggerShoppingAdd")
        uiView.navigationDelegate = nil
    }

    final class Coordinator: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
        let onAddProduct: (String) -> Void

        init(onAddProduct: @escaping (String) -> Void) { self.onAddProduct = onAddProduct }

        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.name == "baggerShoppingAdd",
                  let raw = message.body as? String else { return }
            let name = raw
                .replacingOccurrences(of: "Læg i kurv", with: "", options: .caseInsensitive)
                .replacingOccurrences(of: "Vis i kurv", with: "", options: .caseInsensitive)
                .split(whereSeparator: { $0.isNewline })
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .first(where: { !$0.isEmpty && !$0.allSatisfy { $0.isNumber } })
            guard let name, !name.isEmpty else { return }
            DispatchQueue.main.async { self.onAddProduct(name) }
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            webView.evaluateJavaScript(OfficialFlyerWebView.bridgeScript)
        }
    }

    private static let bridgeScript = #"""
    (() => {
      if (window.__baggerShoppingInstalled) return;
      window.__baggerShoppingInstalled = true;

      const text = el => (el && (el.innerText || el.textContent) || '').replace(/\s+/g, ' ').trim();
      const isBasketAction = el => /læg\s+i\s+kurv/i.test(text(el));
      const hideMenyBasket = () => {
        document.querySelectorAll('a,button,[role="button"]').forEach(el => {
          if (/vis\s+i\s+kurv|gå\s+til\s+kurv/i.test(text(el))) el.style.setProperty('display','none','important');
        });
      };
      hideMenyBasket();
      new MutationObserver(hideMenyBasket).observe(document.documentElement, {subtree:true, childList:true});

      document.addEventListener('click', event => {
        const action = event.target && event.target.closest('button,a,[role="button"]');
        if (!action || !isBasketAction(action)) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        const container = action.closest('[role="dialog"],dialog,[class*="modal"],[class*="product"],[class*="enrichment"]') || action.parentElement;
        const candidates = container ? Array.from(container.querySelectorAll('h1,h2,h3,h4,[class*="title"],[class*="name"]')).map(text).filter(Boolean) : [];
        const product = candidates.find(value => !/kurv|vælg|variant/i.test(value)) || text(container) || text(action);
        window.webkit.messageHandlers.baggerShoppingAdd.postMessage(product);
      }, true);
    })();
    """#
}
