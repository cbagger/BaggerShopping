import SwiftUI

struct StructuredVariantPickerView: View {
    let offer: GroceryOffer
    var selectionVerb = "Tilføj"
    let select: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var customName = ""
    @State private var rememberForFamily = false
    @State private var requireExactVariant = false
    @State private var saving = false
    @State private var preferenceMessage: String?
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
                        identityChips(offer.productIdentity)
                    }
                }

                Section("Vælg variant") {
                    if options.isEmpty {
                        Text("Varianterne kan ikke opdeles sikkert. Du kan bruge kampagnens navn eller skrive din ønskede variant.")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }
                    ForEach(options) { option in
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
                                identityChips(option.variant?.identity)
                                unitPrice(option.variant?.identity)
                            }
                            .padding(.vertical, 5)
                        }
                    }
                }

                Section("Et andet valg") {
                    Button("\(selectionVerb) uden bestemt variant") {
                        choose(offer.shoppingItemName(variant: nil))
                    }
                    TextField("Skriv din ønskede variant", text: $customName)
                    Button(selectionVerb) {
                        choose(offer.shoppingItemName(customVariant: customName))
                    }
                    .disabled(customName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }

                Section("Familiens valg") {
                    Toggle("Husk denne variant for familien", isOn: $rememberForFamily)
                    if rememberForFamily {
                        Toggle("Kræv præcis denne variant", isOn: $requireExactVariant)
                        Text(requireExactVariant
                             ? "Andre varianter skjules i automatiske forslag."
                             : "Varianten prioriteres, men andre tydelige muligheder kan stadig vises.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Button("Alle varianter er acceptable") {
                        saveAnyVariantAndSelect()
                    }
                    .disabled(saving)
                    if let preferenceMessage {
                        Text(preferenceMessage).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("Vælg vare")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Annuller") { dismiss() } } }
            .task { await loadPreference() }
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
        let name = rawName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        saving = true
        Task {
            if rememberForFamily {
                try? await api.saveFamilyProductPreference(
                    itemName: offer.conciseProductName,
                    preferredName: name,
                    mode: requireExactVariant ? "required" : "preferred"
                )
            }
            await MainActor.run {
                saving = false
                select(name)
            }
        }
    }

    private func saveAnyVariantAndSelect() {
        saving = true
        let base = offer.shoppingItemName(variant: nil)
        Task {
            try? await api.saveFamilyProductPreference(
                itemName: offer.conciseProductName,
                preferredName: base,
                mode: "any_variant"
            )
            await MainActor.run {
                saving = false
                select(base)
            }
        }
    }

    @MainActor private func loadPreference() async {
        guard let preferences = try? await api.fetchFamilyProductPreferences(),
              let preference = preferences.first(where: {
                  $0.itemName.localizedCaseInsensitiveCompare(offer.conciseProductName) == .orderedSame
              }) else { return }
        rememberForFamily = preference.mode != "any_variant"
        requireExactVariant = preference.mode == "required"
        preferenceMessage = preference.mode == "any_variant"
            ? "Familien accepterer allerede alle varianter."
            : "Familien foretrækker: \(preference.preferredName)"
    }
}
