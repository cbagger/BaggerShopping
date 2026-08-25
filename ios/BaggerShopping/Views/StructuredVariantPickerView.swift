import SwiftUI

struct StructuredVariantPickerView: View {
    let offer: GroceryOffer
    var selectionVerb = "Tilføj"
    var favoriteItemName: String? = nil
    var onFavoriteChanged: () -> Void = {}
    let select: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var customName = ""
    @State private var familyFavorite: FamilyProductPreference?
    @State private var favoriteWorking = false
    @State private var favoriteError: String?
    private let api = APIClient()

    private struct Option: Identifiable {
        let name: String
        let variant: OfferVariant?
        var id: String { name }
    }

    private var options: [Option] {
        let available: [String]
        if case .variants(let values) = offer.choiceState {
            available = values
        } else {
            available = offer.variants.map(\.name)
        }
        return available
            .map { name in
                Option(
                    name: name,
                    variant: offer.variants.first { $0.name == name }
                )
            }
            .sorted {
                if ($0.variant?.matchesQuery == true) != ($1.variant?.matchesQuery == true) {
                    return $0.variant?.matchesQuery == true
                }
                return $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
            }
    }

    var body: some View {
        NavigationStack {
            Group {
                if let title = offer.addAvailabilityTitle,
                   let message = offer.addAvailabilityMessage {
                    ContentUnavailableView(
                        title,
                        systemImage: offer.publicationStatus == "upcoming"
                            ? "calendar.badge.clock"
                            : "exclamationmark.shield",
                        description: Text(message)
                    )
                    .padding()
                } else {
                    List {
                        if offer.imageURL != nil {
                            OfferCropView(offer: offer)
                                .frame(maxWidth: .infinity, minHeight: 130, maxHeight: 190)
                                .listRowInsets(EdgeInsets())
                        }

                        Section {
                            VStack(alignment: .leading, spacing: 6) {
                                Text(offer.conciseProductName).font(.headline)
                                HStack(spacing: 6) {
                                    Text(offer.retailer).fontWeight(.semibold)
                                    if let price = offer.price {
                                        Text("·")
                                        Text(price, format: .currency(code: "DKK").precision(.fractionLength(price.rounded() == price ? 0 : 2)))
                                    }
                                }
                                .font(.subheadline).foregroundStyle(.secondary)
                                if offer.memberPrice != nil {
                                    MemberPriceBadge(offer: offer, compact: true)
                                }
                                identityChips(offer.productIdentity)
                            }
                        }

                        Section("Vælg variant") {
                            if options.isEmpty {
                                Text("Varianterne kan ikke opdeles sikkert. Du kan bruge kampagnens navn eller skrive din ønskede variant.")
                                    .font(.subheadline).foregroundStyle(.secondary)
                            }
                            ForEach(options) { option in
                                HStack(spacing: 12) {
                                    Button { choose(option.name) } label: {
                                        VStack(alignment: .leading, spacing: 7) {
                                            HStack(alignment: .firstTextBaseline) {
                                                Text(option.name).font(.headline).foregroundStyle(.primary)
                                                Spacer()
                                                if option.variant?.matchesQuery == true {
                                                    Label("Bedste match", systemImage: "checkmark.circle.fill")
                                                        .font(.caption2.weight(.semibold)).foregroundStyle(.green)
                                                }
                                            }
                                            if let detail = variantPackDetail(option.variant),
                                               option.name.range(of: detail, options: [.caseInsensitive, .diacriticInsensitive]) == nil {
                                                Label(detail, systemImage: "shippingbox")
                                                    .font(.caption.weight(.medium))
                                                    .foregroundStyle(.secondary)
                                            }
                                            identityChips(option.variant?.identity)
                                            unitPrice(option.variant?.identity)
                                        }
                                        .padding(.vertical, 5)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                    }
                                    .buttonStyle(.plain)

                                    Button {
                                        toggleFavorite(variant: option.variant)
                                    } label: {
                                        Image(systemName: isFavorite(variant: option.variant) ? "heart.fill" : "heart")
                                            .font(.title3)
                                            .foregroundStyle(isFavorite(variant: option.variant) ? .pink : .secondary)
                                            .frame(width: 36, height: 36)
                                            .contentShape(Rectangle())
                                    }
                                    .buttonStyle(.plain)
                                    .disabled(favoriteWorking)
                                    .accessibilityLabel(
                                        isFavorite(variant: option.variant)
                                            ? "Fjern fra familiens foretrukne varer"
                                            : "Føj til familiens foretrukne varer"
                                    )
                                }
                            }
                        }

                        Section("Et andet valg") {
                            HStack {
                                Button("\(selectionVerb) uden bestemt variant") {
                                    choose(offer.shoppingItemName(variant: nil))
                                }
                                Spacer()
                                Button {
                                    toggleFavorite(variant: nil)
                                } label: {
                                    Image(systemName: isFavorite(variant: nil) ? "heart.fill" : "heart")
                                        .foregroundStyle(isFavorite(variant: nil) ? .pink : .secondary)
                                }
                                .buttonStyle(.plain)
                                .disabled(favoriteWorking)
                            }
                            TextField("Skriv din ønskede variant", text: $customName)
                            Button(selectionVerb) {
                                choose(offer.shoppingItemName(customVariant: customName))
                            }
                            .disabled(customName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        }

                        Section("Familiens foretrukne") {
                            Text("Tryk på hjertet ved familiens favorit. Favoritten vises først i relevante søgninger, men Kurv skjuler aldrig andre tilbud. Størrelse og pakkemængde er kun en ekstra sortering.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            if let familyFavorite {
                                Label(familyFavorite.preferredName, systemImage: "heart.fill")
                                    .font(.subheadline.weight(.semibold))
                                    .foregroundStyle(.pink)
                            }
                            if let favoriteError {
                                Text(favoriteError)
                                    .font(.caption)
                                    .foregroundStyle(.red)
                            }
                        }
                    }
                }
            }
            .navigationTitle(offer.safeToAdd ? "Vælg vare" : "Tilbud")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Annuller") { dismiss() } } }
            .task {
                if offer.safeToAdd { await loadPreference() }
            }
        }
    }

    @ViewBuilder private func identityChips(_ identity: ProductIdentityAnalysis?) -> some View {
        if let identity {
            FlowLayout(spacing: 6) {
                if let brand = identity.brand { chip(brand.capitalized, color: .blue) }
                if let family = identity.canonicalFamily { chip(familyLabel(family), color: .teal) }
                ForEach(identity.types, id: \.self) { chip(typeLabel($0), color: .orange) }
                ForEach(identity.flavours, id: \.self) { chip($0.capitalized, color: .purple) }
                if let amount = identity.amountText { chip(amount, color: .secondary) }
            }
        }
    }

    private func chip(_ text: String, color: Color) -> some View {
        Text(text).font(.caption2.weight(.semibold)).foregroundStyle(color)
            .padding(.horizontal, 8).padding(.vertical, 4)
            .background(color.opacity(0.12), in: Capsule())
    }

    @ViewBuilder private func unitPrice(_ identity: ProductIdentityAnalysis?) -> some View {
        if let value = identity?.unitPrice, let unit = identity?.unitPriceUnit {
            Text("\(value.formatted(.currency(code: "DKK").precision(.fractionLength(2)))) pr. \(unit)")
                .font(.caption).foregroundStyle(.secondary)
        } else if let minimum = identity?.unitPriceMin,
                  let maximum = identity?.unitPriceMax,
                  let unit = identity?.unitPriceUnit {
            Text("\(minimum.formatted(.currency(code: "DKK").precision(.fractionLength(2))))–\(maximum.formatted(.currency(code: "DKK").precision(.fractionLength(2)))) pr. \(unit)")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private func variantPackDetail(_ variant: OfferVariant?) -> String? {
        guard let variant,
              let quantity = variant.quantity,
              quantity > 0,
              let unit = variant.unit?.trimmingCharacters(in: .whitespacesAndNewlines),
              !unit.isEmpty else { return nil }
        let amount = quantity.rounded() == quantity
            ? String(Int(quantity))
            : quantity.formatted(.number.precision(.fractionLength(0...1)))
        return "\(amount) \(unit)"
    }

    private func typeLabel(_ type: String) -> String {
        switch type {
        case "organic": "Økologisk"
        case "lactose_free": "Laktosefri"
        case "gluten_free": "Glutenfri"
        case "alcohol_free": "Alkoholfri"
        case "whole_milk": "Sødmælk"
        case "low_fat_milk": "Letmælk"
        case "mini_milk": "Minimælk"
        case "skimmed_milk": "Skummetmælk"
        default: type.capitalized
        }
    }

    private func familyLabel(_ family: String) -> String {
        switch family {
        case "bread": "Brød"
        case "cola": "Cola"
        case "soft_drink": "Sodavand"
        case "fermented_dairy": "Yoghurt/skyr"
        case "butter_spread": "Smør/smørbar"
        case "household_paper": "Husholdningspapir"
        case "milk": "Mælk"
        default: family.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private func choose(_ rawName: String) {
        guard offer.safeToAdd else { return }
        let name = rawName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        let shoppingName = offer.samsungSafeShoppingItemName(name)
        select(shoppingName)
    }

    private var preferenceContext: String {
        let value = favoriteItemName?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return value.isEmpty ? offer.conciseProductName : value
    }

    private func favoriteName(variant: OfferVariant?) -> String {
        offer.familyFavoriteName(variant: variant)
    }

    private func isFavorite(variant: OfferVariant?) -> Bool {
        guard let familyFavorite else { return false }
        return familyFavorite.preferredName.compare(
            favoriteName(variant: variant),
            options: [.caseInsensitive, .diacriticInsensitive]
        ) == .orderedSame
    }

    private func toggleFavorite(variant: OfferVariant?) {
        guard !favoriteWorking else { return }
        favoriteWorking = true
        favoriteError = nil
        let name = favoriteName(variant: variant)
        Task {
            do {
                if isFavorite(variant: variant), let familyFavorite {
                    try await api.removeFamilyProductPreference(itemName: familyFavorite.itemName)
                    await MainActor.run { self.familyFavorite = nil }
                } else {
                    try await api.saveFamilyProductPreference(
                        itemName: preferenceContext,
                        preferredName: name,
                        mode: "favorite"
                    )
                    await MainActor.run {
                        familyFavorite = FamilyProductPreference(
                            itemName: preferenceContext,
                            preferredName: name,
                            mode: "favorite"
                        )
                    }
                }
                await MainActor.run { onFavoriteChanged() }
            } catch {
                await MainActor.run { favoriteError = error.localizedDescription }
            }
            await MainActor.run { favoriteWorking = false }
        }
    }

    @MainActor private func loadPreference() async {
        guard let preferences = try? await api.fetchFamilyProductPreferences() else { return }
        familyFavorite = preferences.first(where: {
            $0.itemName.localizedCaseInsensitiveCompare(preferenceContext) == .orderedSame
        })
    }
}
