import Foundation

@MainActor
final class ShoppingCategoryService: ObservableObject {
    @Published private(set) var overrides: [String: ShoppingCategory] = [:]

    private let key = "bagger-shopping-category-overrides-v1"

    var learnedCount: Int { overrides.count }

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

    func removeAllOverrides() {
        overrides.removeAll()
        save()
    }

    func replaceWithSharedOverrides(_ shared: [CategoryOverrideDTO]) {
        var imported: [String: ShoppingCategory] = [:]
        for entry in shared {
            guard let category = ShoppingCategory(rawValue: entry.category) else { continue }
            imported[Self.normalize(entry.itemName)] = category
        }
        overrides = imported
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
                "æble", "æbler", "aeble", "banan", "appelsin", "citron", "lime", "pære", "paere", "vindrue", "melon", "ananas",
                "jordbær", "hindbær", "blåbær", "avocado", "tomat", "agurk", "peberfrugt",
                "gulerod", "gulerødder", "kartoffel", "kartofler", "løg", "hvidløg", "salat", "spinat", "broccoli", "blomkål",
                "champignon", "svamp", "porre", "selleri", "squash", "majs", "kål", "frugt", "grønt"
            ]),
            (.meat, [
                "kylling", "oksekød", "kødkvæg", "hakket okse", "svinekød", "flæsk", "bøf", "kotelet", "mørbrad", "medister", "fars", "kød",
                "laks", "torsk", "fisk", "rejer", "tun"
            ]),
            (.deli, [
                "pålæg", "skinke", "hamburgerryg", "spegepølse", "leverpostej", "salami", "rullepølse", "bacon", "kalkunpålæg"
            ]),
            (.dairy, [
                "mælk", "yoghurt", "skyr", "ost", "smør", "fløde", "creme fraiche", "kærnemælk", "hytteost", "mozzarella",
                "parmesan", "æg", "ricotta"
            ]),
            (.bakery, [
                "brød", "rugbrød", "sandwich", "toast", "boller", "bolle", "baguette", "pitabrød", "tortilla", "croissant", "knækbrød"
            ]),
            (.frozen, [
                "frost", "frossen", "is", "ispind", "pizza", "pommes", "frosne", "isterninger"
            ]),
            (.beverages, [
                "cola", "sodavand", "danskvand", "juice", "saft", "kaffe", "te", "øl", "vin", "energidrik", "vand"
            ]),
            (.snacks, [
                "chips", "slik", "chokolade", "nødder", "popcorn", "snack", "dip", "vingummi", "lakrids", "kiks"
            ]),
            (.household, [
                "toiletpapir", "køkkenrulle", "opvasketabs", "opvask", "vaskemiddel", "skyllemiddel", "affaldsposer", "skraldeposer",
                "stanniol", "sølvpapir", "bagepapir", "madpapir", "rengøring", "rengøringssvamp", "opvaskesvamp", "klude",
                "servietter", "lys", "batterier"
            ]),
            (.personalCare, [
                "shampoo", "balsam", "sæbe", "tandpasta", "tandbørste", "deodorant", "bleer", "vådservietter", "barber", "creme",
                "håndsæbe"
            ]),
            (.pantry, [
                "pasta", "ris", "mel", "sukker", "salt", "peber", "olie", "eddike", "ketchup", "sennep", "mayonnaise", "remoulade",
                "pesto", "tomatsauce", "hakkede tomater", "bouillon", "havregryn", "morgenmad", "cornflakes", "mysli", "müsli",
                "nutella", "syltetøj", "honning", "dåse"
            ])
        ]

        for (category, terms) in rules {
            if terms.contains(where: { matches(normalized, term: Self.normalize($0)) }) {
                return category
            }
        }
        return .other
    }

    nonisolated private static func matches(_ normalized: String, term: String) -> Bool {
        if normalized == term { return true }
        if term.contains(" ") { return normalized.contains(term) }

        let tokens = normalized.split(separator: " ").map(String.init)
        return tokens.contains { token in
            if token == term { return true }
            guard term.count >= 4 else { return false }
            return token.hasPrefix(term) || token.hasSuffix(term)
        }
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
