import Foundation
import SwiftUI

@MainActor
final class SmartOfferMatchService: ObservableObject {
    @Published private(set) var matchesByItem: [String: [GroceryOffer]] = SmartOfferMatchCache.load()?.matches ?? [:]
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?

    private let api = APIClient()
    private let matchAPI = SmartOfferMatchAPI()
    private var deferredWarning: String?

    private enum ApprovalError: LocalizedError {
        case samsungItemStillSyncing

        var errorDescription: String? {
            switch self {
            case .samsungItemStillSyncing:
                return "Varen er stadig ved at blive synkroniseret med Samsung Food. Prøv igen om et øjeblik."
            }
        }
    }

    func matches(for item: ShoppingItem) -> [GroceryOffer] {
        matchesByItem[key(item.name)] ?? []
    }

    func refresh(items: [ShoppingItem], model: AppModel) async {
        let eligible = items.filter {
            !$0.checked && model.offerRetailer(for: $0) == nil
        }
        let names = Array(
            Dictionary(grouping: eligible, by: { key($0.name) })
                .values
                .compactMap { $0.first?.name }
        )
        .sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }

        guard !names.isEmpty else {
            matchesByItem = [:]
            SmartOfferMatchCache.save([:])
            errorMessage = nil
            return
        }

        isLoading = true
        defer { isLoading = false }

        do {
            // The backend already exposes one read-only batch endpoint for the
            // entire shopping list. The previous implementation performed one
            // sequential HTTP search per item, making startup latency grow with
            // every item on the list and delaying the “x tilbud fundet” badges.
            let response = try await matchAPI.fetch(items: names)
            guard !Task.isCancelled else { return }

            var refreshed: [String: [GroceryOffer]] = [:]
            for group in response.matches {
                let offers = group.offers.filter { $0.identityMatch?.level != "not_same" }
                if !offers.isEmpty {
                    refreshed[key(group.itemName)] = offers
                }
            }
            matchesByItem = refreshed
            SmartOfferMatchCache.save(refreshed)
            errorMessage = nil
        } catch {
            guard !Task.isCancelled else { return }
            // Suggestions are an enhancement. Keep the recent local cache on
            // screen if refresh fails instead of blanking badges during startup.
            if matchesByItem.isEmpty {
                errorMessage = error.localizedDescription
            } else {
                errorMessage = nil
            }
        }
    }

    func approve(
        _ offer: GroceryOffer,
        selectedItemName: String,
        for item: ShoppingItem,
        model: AppModel
    ) async -> Bool {
        let selectedName = normalizedName(selectedItemName)
        guard !selectedName.isEmpty else {
            errorMessage = "Den valgte tilbudsvariant mangler et varenavn."
            return false
        }

        let oldName = normalizedName(item.name)
        let needsRename = ShoppingCategoryService.normalize(oldName)
            != ShoppingCategoryService.normalize(selectedName)

        deferredWarning = nil

        do {
            let persistedItem: ShoppingItem
            if needsRename {
                persistedItem = try await resolvePersistedItem(item, model: model)
            } else {
                persistedItem = item
            }

            if needsRename {
                let duplicateExists = model.shoppingList?.items.contains { candidate in
                    candidate.id != persistedItem.id
                        && ShoppingCategoryService.normalize(candidate.name)
                            == ShoppingCategoryService.normalize(selectedName)
                } ?? false
                if duplicateExists {
                    errorMessage = "Der findes allerede en vare med navnet \"\(selectedName)\" på indkøbslisten."
                    return false
                }
            }

            let metadata = OfferMetadataDTO(
                itemName: persistedItem.name,
                retailer: offer.retailer,
                price: offer.price,
                validFrom: offer.validFrom,
                validUntil: offer.validUntil,
                offerID: offer.id,
                publicationID: offer.publicationID,
                matchedItemName: selectedName,
                offerSnapshot: offer
            )

            try await api.setOfferMetadata(metadata)

            if needsRename {
                let categoryOverride = model.hasCategoryOverride(for: persistedItem)
                    ? model.category(for: persistedItem)
                    : nil
                do {
                    let result = try await ItemRenameService().rename(
                        item: persistedItem,
                        to: selectedName,
                        categoryOverride: categoryOverride
                    )
                    deferredWarning = result.warning
                } catch {
                    try? await api.removeOfferMetadata(itemName: persistedItem.name)
                    await model.syncSharedOfferMetadata()
                    throw error
                }
            }

            let learnedDecision = offer.identityMatch?.level == "same_item"
                ? "same_item"
                : "compatible_variant"
            try? await api.submitProductMatchFeedback(
                left: item.name,
                right: selectedName,
                decision: learnedDecision
            )

            matchesByItem.removeValue(forKey: key(item.name))
            SmartOfferMatchCache.save(matchesByItem)
            errorMessage = nil
            return true
        } catch {
            errorMessage = "Tilbuddet kunne ikke bruges endnu: \(error.localizedDescription)"
            return false
        }
    }

    func takeDeferredWarning() -> String? {
        defer { deferredWarning = nil }
        return deferredWarning
    }

    private func resolvePersistedItem(
        _ snapshot: ShoppingItem,
        model: AppModel
    ) async throws -> ShoppingItem {
        if let snapshotID = snapshot.id {
            return model.shoppingList?.items.first(where: { $0.id == snapshotID }) ?? snapshot
        }

        let wantedKey = key(snapshot.name)

        if let current = model.shoppingList?.items.first(where: {
            $0.id != nil && key($0.name) == wantedKey
        }) {
            return current
        }

        let delays: [Duration] = [.zero, .milliseconds(500), .seconds(1), .seconds(2)]
        for delay in delays {
            if delay != .zero {
                try await Task.sleep(for: delay)
            }
            guard !Task.isCancelled else { throw CancellationError() }

            let list = try await api.fetchList()
            if let persisted = list.items.first(where: {
                $0.id != nil && key($0.name) == wantedKey
            }) {
                return persisted
            }
        }

        throw ApprovalError.samsungItemStillSyncing
    }

    private func normalizedName(_ value: String) -> String {
        value
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
    }

    private func key(_ value: String) -> String {
        normalizedName(value).lowercased()
    }
}

struct SmartOfferMatchesView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var service: SmartOfferMatchService
    let item: ShoppingItem

    @State private var applyingOfferID: String?
    @State private var pendingVariantOffer: GroceryOffer?
    @State private var previewOffer: GroceryOffer?

    private var offers: [GroceryOffer] {
        service.matches(for: item)
    }

    var body: some View {
        NavigationStack {
            Group {
                if offers.isEmpty {
                    ContentUnavailableView(
                        "Ingen tilbudsforslag",
                        systemImage: "tag.slash",
                        description: Text("Der er ikke længere aktuelle tilbud til \"\(item.name)\".")
                    )
                } else {
                    List {
                        if let errorMessage = service.errorMessage {
                            Section {
                                Label {
                                    Text(errorMessage)
                                } icon: {
                                    Image(systemName: "exclamationmark.triangle.fill")
                                }
                                .font(.caption)
                                .foregroundStyle(.orange)
                            }
                        }

                        Section {
                            Text("Vælg et tilbud til “\(item.name)”. Hvis tilbuddet har flere varianter, vælger du den konkrete vare først. Når du bruger tilbuddet, omdøbes varen på indkøbslisten til den valgte tilbudsvariant, mens antal og købt-status bevares.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }

                        Section {
                            ForEach(offers) { offer in
                                offerRow(offer)
                            }
                        }
                    }
                    .listStyle(.insetGrouped)
                }
            }
            .navigationTitle("Tilbudsforslag")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Luk") { dismiss() }
                }
            }
            .sheet(item: $pendingVariantOffer) { offer in
                StructuredVariantPickerView(offer: offer, selectionVerb: "Brug") { selectedName in
                    selectVariant(
                        offer,
                        selectedItemName: selectedName
                    )
                }
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
            }
            .sheet(item: $previewOffer) { offer in
                OfferPreviewSheet(offer: offer)
                    .presentationDetents([.medium, .large])
                    .presentationDragIndicator(.visible)
            }
        }
    }

    @ViewBuilder
    private func offerRow(_ offer: GroceryOffer) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Label(offer.retailer, systemImage: "storefront")
                    .font(.subheadline.weight(.semibold))

                Spacer(minLength: 8)

                if let price = offer.price {
                    Text(
                        price,
                        format: .currency(code: "DKK")
                            .precision(.fractionLength(price.rounded() == price ? 0 : 2))
                    )
                    .font(.subheadline.bold())
                }
            }

            Text(offer.conciseProductName)
                .font(.subheadline)
                .foregroundStyle(.primary)

            if offer.imageURL != nil {
                Button {
                    previewOffer = offer
                } label: {
                    Label("Se udsnit fra avisen", systemImage: "photo.on.rectangle.angled")
                        .font(.caption.weight(.semibold))
                }
                .buttonStyle(.borderless)
            }

            let variants = offer.variants.filter(\.matchesQuery).map(\.name)
            if !variants.isEmpty {
                Text(variants.prefix(3).joined(separator: " · "))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }

            HStack(spacing: 8) {
                if let validUntil = offer.validUntil {
                    Label("Til \(validUntil)", systemImage: "calendar")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }

                Spacer(minLength: 8)

                Button {
                    choose(offer)
                } label: {
                    HStack(spacing: 5) {
                        if applyingOfferID == offer.id {
                            ProgressView().controlSize(.mini)
                        }
                        Text("Brug tilbud")
                    }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .disabled(applyingOfferID != nil)
            }
        }
        .padding(.vertical, 3)
    }

    private func choose(_ offer: GroceryOffer) {
        guard applyingOfferID == nil else { return }
        service.errorMessage = nil

        switch offer.choiceState {
        case .direct(let variant):
            Task {
                await beginApply(
                    offer,
                    selectedItemName: offer.shoppingItemName(variant: variant)
                )
            }
        case .variants, .unspecified:
            pendingVariantOffer = offer
        }
    }

    @MainActor
    private func selectVariant(
        _ offer: GroceryOffer,
        selectedItemName: String
    ) {
        guard applyingOfferID == nil else { return }
        applyingOfferID = offer.id
        pendingVariantOffer = nil
        service.errorMessage = nil

        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(450))
            guard !Task.isCancelled else {
                applyingOfferID = nil
                return
            }
            await performApply(
                offer,
                selectedItemName: selectedItemName,
                offerWasMarkedApplying: true
            )
        }
    }

    @MainActor
    private func beginApply(
        _ offer: GroceryOffer,
        selectedItemName: String
    ) async {
        guard applyingOfferID == nil else { return }
        applyingOfferID = offer.id
        await performApply(
            offer,
            selectedItemName: selectedItemName,
            offerWasMarkedApplying: true
        )
    }

    @MainActor
    private func performApply(
        _ offer: GroceryOffer,
        selectedItemName: String,
        offerWasMarkedApplying: Bool
    ) async {
        if !offerWasMarkedApplying {
            guard applyingOfferID == nil else { return }
            applyingOfferID = offer.id
        }

        let success = await service.approve(
            offer,
            selectedItemName: selectedItemName,
            for: item,
            model: model
        )
        applyingOfferID = nil

        guard success else { return }

        let warning = service.takeDeferredWarning()
        dismiss()

        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(500))
            await model.refresh()
            await model.syncSharedCategories()
            await model.syncSharedOfferMetadata()
            if let warning {
                model.errorMessage = warning
            }
        }
    }
}

/// Smart Matching intentionally mirrors the established offer variant picker
/// used in the Tilbud tab. Search responses may contain several matching
/// variants, so approving the campaign alone is not enough to identify what
/// should be put on the list.
