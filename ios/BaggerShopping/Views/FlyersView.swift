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
    @State private var pendingProduct: FlyerProductSelection?

    var body: some View {
        NavigationStack {
            Group {
                if let url = publication.readerURL {
                    OfficialFlyerWebView(url: url) { product in pendingProduct = product }
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
            .sheet(item: $pendingProduct) { product in
                NavigationStack {
                    List(product.variants, id: \.self) { name in
                        Button {
                            pendingProduct = nil
                            Task {
                                if await model.addItem(name) { addedItemName = name }
                            }
                        } label: {
                            Label(name, systemImage: "plus.circle.fill")
                                .foregroundStyle(.primary)
                        }
                    }
                    .navigationTitle(product.title)
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) {
                            Button("Annuller") { pendingProduct = nil }
                        }
                    }
                }
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
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

private struct FlyerProductSelection: Codable, Identifiable {
    let title: String
    let variants: [String]
    var id: String { title + variants.joined(separator: "|") }
}

private struct OfficialFlyerWebView: UIViewRepresentable {
    let url: URL
    let onSelectProduct: (FlyerProductSelection) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onSelectProduct: onSelectProduct) }

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
        let onSelectProduct: (FlyerProductSelection) -> Void

        init(onSelectProduct: @escaping (FlyerProductSelection) -> Void) { self.onSelectProduct = onSelectProduct }

        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.name == "baggerShoppingAdd",
                  JSONSerialization.isValidJSONObject(message.body),
                  let data = try? JSONSerialization.data(withJSONObject: message.body),
                  let product = try? JSONDecoder().decode(FlyerProductSelection.self, from: data),
                  !product.variants.isEmpty else { return }
            DispatchQueue.main.async { self.onSelectProduct(product) }
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
      const basketPattern = /læg\s+i\s+kurv|vis\s+i\s+kurv|gå\s+til\s+kurv/i;
      const clean = value => value.replace(basketPattern, '').replace(/\s+/g, ' ').trim();
      const emitOverlay = root => {
        if (!root || root.dataset.baggerHandled === '1') return false;
        const actions = Array.from(root.querySelectorAll('button,a,[role="button"]')).filter(el => /læg\s+i\s+kurv/i.test(text(el)));
        if (!actions.length) return false;
        root.dataset.baggerHandled = '1';
        const headings = Array.from(root.querySelectorAll('h1,h2,h3,h4,[class*="title"],[class*="name"]')).map(el => clean(text(el))).filter(Boolean);
        const variants = actions.map(action => {
          const row = action.closest('li,[class*="variant"],[class*="product"],[class*="item"]') || action.parentElement;
          const names = row ? Array.from(row.querySelectorAll('h1,h2,h3,h4,[class*="title"],[class*="name"]')).map(el => clean(text(el))).filter(Boolean) : [];
          return names[0] || clean(text(row));
        }).filter(value => value && !/vælg|variant|pris/i.test(value));
        const unique = Array.from(new Set(variants));
        const title = headings.find(value => !unique.includes(value) && !/vælg|variant/i.test(value)) || unique[0] || 'Vælg vare';
        root.style.setProperty('display','none','important');
        window.webkit.messageHandlers.baggerShoppingAdd.postMessage({title, variants: unique.length ? unique : [title]});
        return true;
      };
      const inspect = () => {
        document.querySelectorAll('a,button,[role="button"]').forEach(el => {
          if (/vis\s+i\s+kurv|gå\s+til\s+kurv/i.test(text(el))) el.style.setProperty('display','none','important');
        });
        document.querySelectorAll('[role="dialog"],dialog,[class*="modal"],[class*="overlay"]').forEach(emitOverlay);
      };
      inspect();
      new MutationObserver(inspect).observe(document.documentElement, {subtree:true, childList:true});

      document.addEventListener('click', event => {
        const action = event.target && event.target.closest('button,a,[role="button"]');
        if (!action || !/læg\s+i\s+kurv/i.test(text(action))) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        const container = action.closest('[role="dialog"],dialog,[class*="modal"],[class*="product"],[class*="enrichment"]') || action.parentElement;
        if (!emitOverlay(container)) {
          const product = clean(text(container)) || 'Vælg vare';
          window.webkit.messageHandlers.baggerShoppingAdd.postMessage({title: product, variants: [product]});
        }
      }, true);
    })();
    """#
}
