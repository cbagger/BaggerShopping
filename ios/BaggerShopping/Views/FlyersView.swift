import SwiftUI
import UIKit

struct FlyersView: View {
    @EnvironmentObject private var navigation: AppNavigation
    @State private var publications: [OfferPublication] = FlyerPublicationCache.load()?.publications ?? []
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
                    GeometryReader { geometry in
                        let horizontalPadding: CGFloat = 18
                        let columnSpacing: CGFloat = 14
                        let cardWidth = max(
                            0,
                            floor((geometry.size.width - (horizontalPadding * 2) - columnSpacing) / 2)
                        )

                        ScrollView {
                            LazyVGrid(
                                columns: [
                                    GridItem(.fixed(cardWidth), spacing: columnSpacing, alignment: .top),
                                    GridItem(.fixed(cardWidth), spacing: columnSpacing, alignment: .top)
                                ],
                                alignment: .center,
                                spacing: 24
                            ) {
                                ForEach(Array(publications.enumerated()), id: \.element.id) { _, publication in
                                    Button { selectedPublication = publication } label: {
                                        FlyerCoverCard(publication: publication, width: cardWidth)
                                    }
                                    .buttonStyle(.plain)
                                    .frame(width: cardWidth)
                                }
                            }
                            .frame(maxWidth: .infinity)
                            .padding(.horizontal, horizontalPadding)
                            .padding(.vertical, 12)
                            .padding(.bottom, 88)
                        }
                        .refreshable { await load() }
                    }
                }
            }
            .navigationTitle("Aviser")
            .task { await load() }
            .onChange(of: navigation.flyerRoute?.id) { _, _ in
                openRequestedFlyerIfAvailable()
            }
            .fullScreenCover(item: $selectedPublication) { NativeFlyerReader(publication: $0) }
        }
    }

    @MainActor private func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let fetched = try await api.fetchOfferPublications().publications
            publications = fetched
            FlyerPublicationCache.save(fetched)
            openRequestedFlyerIfAvailable()
        } catch {
            if publications.isEmpty { errorMessage = error.localizedDescription }
        }
    }

    @MainActor private func openRequestedFlyerIfAvailable() {
        guard let route = navigation.flyerRoute else { return }
        if let exact = publications.first(where: { $0.id == route.publicationID }) {
            selectedPublication = exact
            return
        }
        if let retailer = route.retailer,
           let latest = publications.first(where: {
               $0.retailer.caseInsensitiveCompare(retailer) == .orderedSame && $0.status != "expired"
           }) {
            selectedPublication = latest
        }
    }
}

private struct FlyerCoverCard: View {
    let publication: OfferPublication
    let width: CGFloat

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            ZStack {
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(Color(uiColor: .secondarySystemBackground))

                if let coverURL = publication.pageImageURLs.first {
                    AsyncImage(url: coverURL) { phase in
                        if let image = phase.image {
                            image
                                .resizable()
                                .scaledToFill()
                                .frame(width: width, height: 240)
                                .clipped()
                        } else if phase.error != nil {
                            Image(systemName: "newspaper")
                                .font(.largeTitle)
                                .foregroundStyle(.secondary)
                        } else {
                            ProgressView()
                        }
                    }
                } else {
                    Image(systemName: "newspaper")
                        .font(.largeTitle)
                        .foregroundStyle(.secondary)
                }
            }
            .frame(width: width, height: 240)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(.black.opacity(0.08), lineWidth: 1)
            }
            .shadow(color: .black.opacity(0.12), radius: 8, y: 3)

            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(publication.retailer)
                    .font(.headline)
                    .foregroundStyle(.primary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.82)

                Spacer(minLength: 4)

                if let weekLabel {
                    Text(weekLabel)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            .frame(width: width)

            Label(expiryLabel, systemImage: "clock")
                .font(.subheadline)
                .foregroundStyle(expiryColor)
                .lineLimit(1)
                .minimumScaleFactor(0.82)
                .frame(width: width, alignment: .leading)
        }
        .frame(width: width, alignment: .leading)
        .clipped()
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityHint("Åbner tilbudsavisen")
    }

    private var weekLabel: String? {
        let lowercased = publication.title.lowercased()
        guard let range = lowercased.range(of: "uge") else { return nil }
        let suffix = lowercased[range.upperBound...]
            .drop(while: { !$0.isNumber })
        let digits = suffix.prefix(while: { $0.isNumber })
        guard !digits.isEmpty else { return nil }
        return "Uge \(digits.prefix(2))"
    }

    private var expiryLabel: String {
        if publication.status == "upcoming" { return "Kommer snart" }
        guard let expiryDate else { return "Aktuel avis" }

        let days = Calendar.current.dateComponents(
            [.day],
            from: Calendar.current.startOfDay(for: Date()),
            to: Calendar.current.startOfDay(for: expiryDate)
        ).day ?? 0

        switch days {
        case ..<0: return "Udløbet"
        case 0: return "Slutter i dag"
        case 1: return "Slutter i morgen"
        default: return "\(days) dage tilbage"
        }
    }

    private var expiryColor: Color {
        guard let expiryDate else { return .secondary }
        let days = Calendar.current.dateComponents(
            [.day],
            from: Calendar.current.startOfDay(for: Date()),
            to: Calendar.current.startOfDay(for: expiryDate)
        ).day ?? 0
        return days <= 1 ? .orange : .secondary
    }

    private var expiryDate: Date? {
        guard let value = publication.validUntil else { return nil }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "da_DK")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.dateFormat = "dd.MM.yyyy"
        return formatter.date(from: value)
    }
}

private struct NativeFlyerReader: View {
    let publication: OfferPublication
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var model: AppModel
    @StateObject private var offerAddActivity = OfferAddActivity.shared
    @State private var offers: [GroceryOffer]
    @State private var page = 1
    @State private var pendingOffer: GroceryOffer?
    @State private var errorMessage: String?
    @State private var pendingCheaperAddition: PendingOfferAddition?
    private let api = APIClient()

    init(publication: OfferPublication) {
        self.publication = publication
        _offers = State(initialValue: FlyerOfferCache.load(publicationID: publication.id)?.offers ?? [])
    }

    var body: some View {
        NavigationStack {
            Group {
                if publication.pageImageURLs.isEmpty {
                    ContentUnavailableView("Avisen mangler sidebilleder", systemImage: "doc.text.magnifyingglass")
                } else {
                    TabView(selection: $page) {
                        ForEach(Array(publication.pageImageURLs.enumerated()), id: \.offset) { index, url in
                            FlyerPage(
                                url: url,
                                offers: offers.filter { $0.pageNumber == index + 1 },
                                select: choose,
                                report: report
                            )
                            .tag(index + 1)
                        }
                    }
                    .tabViewStyle(.page(indexDisplayMode: .never))
                    .background(Color.black)
                    .overlay(alignment: .topTrailing) {
                        Text("\(page) / \(publication.pageImageURLs.count)")
                            .font(.caption.bold())
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .background(.ultraThinMaterial, in: Capsule())
                            .padding(12)
                    }
                }
            }
            .navigationTitle(publication.retailer)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Luk", systemImage: "xmark") { dismiss() }
                }
            }
            .task { await loadOffers() }
            .sheet(item: $pendingOffer) { offer in
                StructuredVariantPickerView(offer: offer, selectionVerb: "Tilføj") { name in
                    add(name, from: offer)
                    pendingOffer = nil
                }
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
            }
            .alert(
                "Kunne ikke hente avisens varer",
                isPresented: Binding(
                    get: { errorMessage != nil },
                    set: { if !$0 { errorMessage = nil } }
                )
            ) {
                Button("OK") { errorMessage = nil }
            } message: {
                Text(errorMessage ?? "")
            }
            .sheet(item: $pendingCheaperAddition) { pending in
                CheaperOffersSheet(pending: pending) { offer in
                    addWithoutPriceCheck(pending.itemName, from: offer)
                    pendingCheaperAddition = nil
                } ignore: {
                    addWithoutPriceCheck(pending.itemName, from: pending.selectedOffer)
                    pendingCheaperAddition = nil
                }
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
            }
        }
        .overlay {
            if let message = offerAddActivity.phase.message {
                VStack(spacing: 12) {
                    if offerAddActivity.phase.showsProgress {
                        ProgressView()
                    } else {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.title2)
                            .foregroundStyle(.green)
                    }
                    Text(message)
                        .font(.subheadline.weight(.semibold))
                        .multilineTextAlignment(.center)
                }
                .padding(.horizontal, 22)
                .padding(.vertical, 18)
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                .shadow(radius: 12, y: 4)
                .allowsHitTesting(false)
            }
        }
        .preferredColorScheme(.light)
    }

    private func choose(_ offer: GroceryOffer) {
        switch offer.choiceState {
        case .direct(let variant):
            add(offer.shoppingItemName(variant: variant), from: offer)
        case .variants, .unspecified:
            pendingOffer = offer
        }
    }

    private func add(_ name: String, from selectedOffer: GroceryOffer? = nil) {
        let offer = selectedOffer
            ?? pendingOffer
            ?? offers.first { $0.variants.contains(where: { $0.name == name }) }
        guard let offer else { return }

        Task {
            let cheaper = await OfferPriceGuard().cheaperOffers(for: name, than: offer)
            if !cheaper.isEmpty {
                pendingCheaperAddition = PendingOfferAddition(
                    itemName: name,
                    selectedOffer: offer,
                    cheaperOffers: cheaper
                )
                return
            }
            addWithoutPriceCheck(name, from: offer)
        }
    }

    private func addWithoutPriceCheck(_ name: String, from offer: GroceryOffer) {
        Task {
            _ = await model.addItem(
                name,
                retailer: offer.retailer,
                offerPrice: offer.price,
                offerValidFrom: offer.validFrom,
                offerValidUntil: offer.validUntil,
                offerID: offer.id,
                publicationID: offer.publicationID,
                matchedItemName: name,
                offerSnapshot: offer
            )
        }
    }

    @MainActor private func loadOffers() async {
        do {
            let fetched = try await api.fetchOffers(publicationID: publication.id).offers
            offers = fetched
            FlyerOfferCache.save(fetched, publicationID: publication.id)
            errorMessage = nil
        } catch {
            if offers.isEmpty { errorMessage = error.localizedDescription }
        }
    }

    private func report(_ offer: GroceryOffer, decision: String) {
        Task { try? await api.submitFlyerQualityFeedback(offer: offer, decision: decision) }
    }
}

private struct FlyerPage: View {
    let url: URL
    let offers: [GroceryOffer]
    let select: (GroceryOffer) -> Void
    let report: (GroceryOffer, String) -> Void

    var body: some View {
        GeometryReader { proxy in
            ZoomableFlyerPage(
                url: url,
                offers: offers,
                select: select,
                report: report,
                size: proxy.size
            )
            .frame(width: proxy.size.width, height: proxy.size.height)
            .background(Color.black)
        }
    }
}

private struct FlyerPageCanvas: View {
    let url: URL
    let offers: [GroceryOffer]
    let select: (GroceryOffer) -> Void
    let report: (GroceryOffer, String) -> Void
    let size: CGSize
    let hotspotsEnabled: Bool

    var body: some View {
        ZStack {
            Color.black

            AsyncImage(url: url) { phase in
                if let image = phase.image {
                    image
                        .resizable()
                        .scaledToFit()
                        .frame(width: size.width, height: size.height)
                        .overlay {
                            hotspots(in: size)
                                .allowsHitTesting(hotspotsEnabled)
                        }
                } else if phase.error != nil {
                    ContentUnavailableView(
                        "Siden kunne ikke hentes",
                        systemImage: "photo.badge.exclamationmark"
                    )
                    .foregroundStyle(.white)
                } else {
                    ProgressView()
                        .tint(.white)
                }
            }
            .frame(width: size.width, height: size.height)
        }
        .frame(width: size.width, height: size.height)
    }

    func withHotspotsEnabled(_ enabled: Bool) -> FlyerPageCanvas {
        FlyerPageCanvas(
            url: url,
            offers: offers,
            select: select,
            report: report,
            size: size,
            hotspotsEnabled: enabled
        )
    }

    func hotspotHitRects() -> [CGRect] {
        let layout = hotspotLayout(in: size)
        let buttonSize: CGFloat = 44

        return offers.compactMap { offer in
            guard let x = offer.hotspotX,
                  let y = offer.hotspotY,
                  let w = offer.hotspotWidth,
                  let h = offer.hotspotHeight else { return nil }

            let centerX = layout.offsetX + layout.width * (x + w / 2)
            let centerY = layout.offsetY + layout.height * (y + h / 2)
            return CGRect(
                x: centerX - buttonSize / 2,
                y: centerY - buttonSize / 2,
                width: buttonSize,
                height: buttonSize
            )
        }
    }

    @ViewBuilder private func hotspots(in container: CGSize) -> some View {
        let layout = hotspotLayout(in: container)
        let buttonSize: CGFloat = 44

        ZStack(alignment: .topLeading) {
            ForEach(offers) { offer in
                if let x = offer.hotspotX,
                   let y = offer.hotspotY,
                   let w = offer.hotspotWidth,
                   let h = offer.hotspotHeight {
                    let centerX = layout.offsetX + layout.width * (x + w / 2)
                    let centerY = layout.offsetY + layout.height * (y + h / 2)

                    Button { select(offer) } label: {
                        Image(systemName: "plus")
                            .font(.caption.bold())
                            .foregroundStyle(.white)
                            .frame(width: 30, height: 30)
                            .background(
                                offer.hotspotConfidence >= 0.75
                                    ? Color.black.opacity(0.82)
                                    : Color.orange.opacity(0.90),
                                in: Circle()
                            )
                            .frame(width: buttonSize, height: buttonSize)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .frame(width: buttonSize, height: buttonSize)
                    .offset(
                        x: centerX - buttonSize / 2,
                        y: centerY - buttonSize / 2
                    )
                    .accessibilityLabel("Tilføj \(offer.productName)")
                    .accessibilityValue(
                        offer.hotspotConfidence >= 0.75
                            ? "Sikker placering"
                            : "Usikker placering"
                    )
                    .contextMenu {
                        Button("Rapportér forkert placering", systemImage: "scope") {
                            report(offer, "wrong_position")
                        }
                        Button(
                            "Rapportér forkerte varianter",
                            systemImage: "square.stack.3d.up.slash"
                        ) {
                            report(offer, "wrong_variants")
                        }
                    }
                }
            }
        }
        .frame(width: container.width, height: container.height, alignment: .topLeading)
    }

    private func hotspotLayout(in container: CGSize) -> (
        width: CGFloat,
        height: CGFloat,
        offsetX: CGFloat,
        offsetY: CGFloat
    ) {
        let ratio = 694.0 / 1007.0
        let width = min(container.width, container.height * ratio)
        let height = width / ratio
        return (
            width,
            height,
            (container.width - width) / 2,
            (container.height - height) / 2
        )
    }
}

private struct ZoomableFlyerPage: UIViewRepresentable {
    let url: URL
    let offers: [GroceryOffer]
    let select: (GroceryOffer) -> Void
    let report: (GroceryOffer, String) -> Void
    let size: CGSize

    func makeCoordinator() -> Coordinator {
        Coordinator(
            canvas: FlyerPageCanvas(
                url: url,
                offers: offers,
                select: select,
                report: report,
                size: size,
                hotspotsEnabled: true
            )
        )
    }

    func makeUIView(context: Context) -> UIScrollView {
        let scrollView = UIScrollView(frame: .zero)
        scrollView.delegate = context.coordinator
        scrollView.minimumZoomScale = 1
        scrollView.maximumZoomScale = 4
        scrollView.bouncesZoom = true
        scrollView.showsHorizontalScrollIndicator = false
        scrollView.showsVerticalScrollIndicator = false
        scrollView.backgroundColor = .black
        scrollView.contentInsetAdjustmentBehavior = .never
        scrollView.clipsToBounds = true
        scrollView.isMultipleTouchEnabled = true
        scrollView.delaysContentTouches = true
        scrollView.canCancelContentTouches = true
        scrollView.panGestureRecognizer.isEnabled = false
        scrollView.pinchGestureRecognizer?.cancelsTouchesInView = true

        let hostedView = context.coordinator.hostingController.view!
        hostedView.backgroundColor = .clear
        hostedView.frame = CGRect(origin: .zero, size: size)
        scrollView.addSubview(hostedView)
        scrollView.contentSize = size

        let doubleTap = UITapGestureRecognizer(
            target: context.coordinator,
            action: #selector(Coordinator.handleDoubleTap(_:))
        )
        doubleTap.numberOfTapsRequired = 2
        doubleTap.cancelsTouchesInView = true
        doubleTap.delegate = context.coordinator
        scrollView.addGestureRecognizer(doubleTap)
        context.coordinator.doubleTapGesture = doubleTap

        let twoFingerGuard = UILongPressGestureRecognizer(
            target: context.coordinator,
            action: #selector(Coordinator.handleTwoFingerGuard(_:))
        )
        twoFingerGuard.minimumPressDuration = 0
        twoFingerGuard.minimumNumberOfTouches = 2
        twoFingerGuard.maximumNumberOfTouches = 2
        twoFingerGuard.cancelsTouchesInView = true
        twoFingerGuard.delegate = context.coordinator
        scrollView.addGestureRecognizer(twoFingerGuard)
        context.coordinator.twoFingerGuard = twoFingerGuard
        context.coordinator.scrollView = scrollView

        return scrollView
    }

    func updateUIView(_ scrollView: UIScrollView, context: Context) {
        let sizeChanged = context.coordinator.canvasSize != size
        if sizeChanged {
            scrollView.setZoomScale(1, animated: false)
        }

        context.coordinator.update(
            canvas: FlyerPageCanvas(
                url: url,
                offers: offers,
                select: select,
                report: report,
                size: size,
                hotspotsEnabled: true
            )
        )

        context.coordinator.hostingController.view.frame = CGRect(origin: .zero, size: size)
        scrollView.contentSize = size
        scrollView.panGestureRecognizer.isEnabled = scrollView.zoomScale > 1.01
    }

    final class Coordinator: NSObject, UIScrollViewDelegate, UIGestureRecognizerDelegate {
        let hostingController: UIHostingController<FlyerPageCanvas>
        weak var scrollView: UIScrollView?
        weak var doubleTapGesture: UITapGestureRecognizer?
        weak var twoFingerGuard: UILongPressGestureRecognizer?
        var canvasSize: CGSize
        private var canvas: FlyerPageCanvas
        private var hotspotsEnabled = true

        init(canvas: FlyerPageCanvas) {
            self.canvas = canvas
            self.canvasSize = canvas.size
            self.hostingController = UIHostingController(
                rootView: canvas.withHotspotsEnabled(true)
            )
            super.init()
        }

        func update(canvas: FlyerPageCanvas) {
            self.canvas = canvas
            canvasSize = canvas.size
            renderCanvas()
        }

        func viewForZooming(in scrollView: UIScrollView) -> UIView? {
            hostingController.view
        }

        func scrollViewWillBeginZooming(_ scrollView: UIScrollView, with view: UIView?) {
            setHotspotsEnabled(false)
            scrollView.panGestureRecognizer.isEnabled = true
        }

        func scrollViewDidZoom(_ scrollView: UIScrollView) {
            let zoomed = scrollView.zoomScale > 1.01
            if zoomed {
                setHotspotsEnabled(false)
            }
            scrollView.panGestureRecognizer.isEnabled = zoomed
        }

        func scrollViewDidEndZooming(
            _ scrollView: UIScrollView,
            with view: UIView?,
            atScale scale: CGFloat
        ) {
            let zoomed = scale > 1.01
            scrollView.panGestureRecognizer.isEnabled = zoomed
            setHotspotsEnabled(!zoomed)
        }

        func gestureRecognizer(
            _ gestureRecognizer: UIGestureRecognizer,
            shouldReceive touch: UITouch
        ) -> Bool {
            guard gestureRecognizer === doubleTapGesture else { return true }
            let point = touch.location(in: hostingController.view)
            return !canvas.hotspotHitRects().contains(where: { $0.contains(point) })
        }

        func gestureRecognizer(
            _ gestureRecognizer: UIGestureRecognizer,
            shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer
        ) -> Bool {
            guard let twoFingerGuard else { return false }
            return gestureRecognizer === twoFingerGuard
                || otherGestureRecognizer === twoFingerGuard
        }

        @objc func handleTwoFingerGuard(_ gesture: UILongPressGestureRecognizer) {
            switch gesture.state {
            case .began, .changed:
                setHotspotsEnabled(false)
            case .ended, .cancelled, .failed:
                reenableHotspotsIfAtRest(after: 0.08)
            default:
                break
            }
        }

        @objc func handleDoubleTap(_ gesture: UITapGestureRecognizer) {
            guard let scrollView else { return }
            setHotspotsEnabled(false)

            if scrollView.zoomScale > scrollView.minimumZoomScale + 0.01 {
                scrollView.setZoomScale(scrollView.minimumZoomScale, animated: true)
                reenableHotspotsIfAtRest(after: 0.35)
                return
            }

            let targetScale = min(2.25, scrollView.maximumZoomScale)
            let point = gesture.location(in: hostingController.view)
            let width = scrollView.bounds.width / targetScale
            let height = scrollView.bounds.height / targetScale
            let zoomRect = CGRect(
                x: point.x - width / 2,
                y: point.y - height / 2,
                width: width,
                height: height
            )
            scrollView.panGestureRecognizer.isEnabled = true
            scrollView.zoom(to: zoomRect, animated: true)
        }

        private func setHotspotsEnabled(_ enabled: Bool) {
            guard hotspotsEnabled != enabled else { return }
            hotspotsEnabled = enabled
            renderCanvas()
        }

        private func renderCanvas() {
            hostingController.rootView = canvas.withHotspotsEnabled(hotspotsEnabled)
        }

        private func reenableHotspotsIfAtRest(after delay: TimeInterval) {
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
                guard let self,
                      let scrollView = self.scrollView,
                      scrollView.zoomScale <= scrollView.minimumZoomScale + 0.01,
                      scrollView.pinchGestureRecognizer?.state != .began,
                      scrollView.pinchGestureRecognizer?.state != .changed else { return }
                self.setHotspotsEnabled(true)
                scrollView.panGestureRecognizer.isEnabled = false
            }
        }
    }
}
