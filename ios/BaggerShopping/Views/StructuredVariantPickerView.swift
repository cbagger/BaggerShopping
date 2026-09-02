import SwiftUI

struct StructuredVariantPickerView: View {
    let offer: GroceryOffer
    var selectionVerb = "Tilføj"
    let select: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var customName = ""

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

                    }
                }
            }
            .navigationTitle(offer.safeToAdd ? "Vælg vare" : "Tilbud")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Annuller") { dismiss() } } }
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

}
