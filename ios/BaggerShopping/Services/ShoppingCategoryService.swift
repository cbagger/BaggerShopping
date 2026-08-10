import Foundation

@MainActor
final class ShoppingCategoryService: ObservableObject {
    @Published private(set) var overrides: [String: ShoppingCategory] = [:]

    private let key = "bagger-shopping-category-overrides-v1"

    init() {
        load()
    }

    func category(for itemName: String) -> ShoppingCategory {
        let normalized = Self.normalize(itemName)
        if let override = overrides[normalized] {
            return override
        }
        return Self.classify(normalized)
    }

    func setCategory(_ category: ShoppingCategory, for itemName: String) {
        overrides[Self.normalize(itemName)] = category
        save()
    }

    func removeOverride(for itemName: String) {
        overrides.removeValue(forKey: Self.normalize(itemName))
        save()
    }

    func hasOverride(for itemName: String) -> Bool {
        overrides[Self.normalize(itemName)] != nil
    }

    nonisolated static func normalize(_ input: String) -> String {
        input
            .folding(options: [.diacriticInsensitive, .caseInsensitive], locale: Locale(identifier: "da_DK"))
            .lowercased()
            .replacingOccurrences(of: "[^a-z0-9æøå ]", with: " ", options: .regularExpression)
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
    }

    nonisolated static func classify(_ normalized: String) -> ShoppingCategory {
        let rules: [(ShoppingCategory, [String])] = [
            (.fruitAndVegetables, [
                "æble", "aeble", "banan", "appelsin", "citron", "lime", "pære", "paere", "vindrue", "melon", "ananas",
                "jordbær", "jordbaer", "hindbær", "hindbaer", "blåbær", "blaabaer", "avocado", "tomat", "agurk", "peberfrugt",
                "gulerod", "kartoffel", "løg", "loeg", "hvidløg", "hvidloeg", "salat", "spinat", "broccoli", "blomkål", "blomkaal",
                "champignon", "svampe", "porre", "selleri", "squash", "majs", "kål", "kaal", "frugt", "grønt", "groent"
            ]),
            (.meat, [
                "kylling", "oksekød", "oksekoed", "hakket okse", "svinekød", "svinekoed", "flæsk", "flaesk", "bøf", "boef",
                "kotelet", "mørbrad", "moerbrad", "medister", "fars", "kød", "koed", "laks", "torsk", "fisk", "rejer", "tun"
            ]),
            (.deli, [
                "pålæg", "paalaeg", "skinke", "hamburgerryg", "spegepølse", "spegepoelse", "leverpostej", "salami", "rullepølse",
                "rullepoelse", "bacon", "kalkunpålæg", "kalkunpaalaeg"
            ]),
            (.dairy, [
                "mælk", "maelk", "yoghurt", "skyr", "ost", "smør", "smoer", "fløde", "floede", "creme fraiche", "kærnemælk",
                "kaernemaelk", "hytteost", "mozzarella", "parmesan", "æg", "aeg"
            ]),
            (.bakery, [
                "brød", "broed", "rugbrød", "rugbroed", "toast", "boller", "bolle", "baguette", "pitabrød", "pitabroed", "tortilla",
                "croissant", "knækbrød", "knaekbroed"
            ]),
            (.frozen, [
                "frost", "frossen", "is", "ispind", "pizza", "pommes", "frosne", "isterninger"
            ]),
            (.beverages, [
                "cola", "sodavand", "danskvand", "juice", "saft", "kaffe", "te", "øl", "oel", "vin", "energidrik", "vand"
            ]),
            (.household, [
                "toiletpapir", "køkkenrulle", "koekkenrulle", "opvasketabs", "opvask", "vaskemiddel", "skyllemiddel", "affaldsposer",
                "skraldeposer", "stanniol", "sølvpapir", "soelvpapir", "bagepapir", "madpapir", "rengøring", "rengoering", "svampe",
                "klude", "servietter", "lys", "batterier"
            ]),
            (.personalCare, [
                "shampoo", "balsam", "sæbe", "saebe", "tandpasta", "tandbørste", "tandboerste", "deodorant", "bleer", "vådservietter",
                "vaadservietter", "barber", "creme", "håndsæbe", "haandsaebe"
            ]),
            (.pantry, [
                "pasta", "ris", "mel", "sukker", "salt", "peber", "olie", "eddike", "ketchup", "sennep", "mayonnaise", "remoulade",
                "pesto", "tomatsauce", "hakkede tomater", "bouillon", "havregryn", "morgenmad", "cornflakes", "mysli", "müsli", "nutella",
                "syltetøj", "syltetoej", "honning", "kiks", "chips", "slik", "chokolade", "nødder", "noedder", "dåse", "daase"
            ])
        ]

        for (category, terms) in rules {
            if terms.contains(where: { normalized == $0 || normalized.contains("\($0) ") || normalized.contains(" \($0)") }) {
                return category
            }
        }

        return .other
    }

    private func save() {
        let raw = overrides.mapValues(\.rawValue)
        UserDefaults.standard.set(raw, forKey: key)
    }

    private func load() {
        guard let raw = UserDefaults.standard.dictionary(forKey: key) as? [String: String] else { return }
        overrides = raw.reduce(into: [:]) { result, entry in
            if let category = ShoppingCategory(rawValue: entry.value) {
                result[entry.key] = category
            }
        }
    }
}
