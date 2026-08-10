import Foundation

enum ShoppingCategory: String, Codable, CaseIterable, Identifiable, Hashable {
    case fruitAndVegetables = "Frugt & Grønt"
    case meat = "Kød"
    case deli = "Pålæg"
    case dairy = "Mejeri"
    case bakery = "Brød & Bager"
    case frozen = "Frost"
    case pantry = "Kolonial"
    case beverages = "Drikkevarer"
    case household = "Husholdning"
    case personalCare = "Personlig pleje"
    case other = "Andet"

    var id: String { rawValue }

    var icon: String {
        switch self {
        case .fruitAndVegetables: return "carrot"
        case .meat: return "fork.knife"
        case .deli: return "takeoutbag.and.cup.and.straw"
        case .dairy: return "waterbottle"
        case .bakery: return "birthday.cake"
        case .frozen: return "snowflake"
        case .pantry: return "cabinet"
        case .beverages: return "cup.and.saucer"
        case .household: return "house"
        case .personalCare: return "cross.case"
        case .other: return "square.grid.2x2"
        }
    }

    var sortOrder: Int {
        switch self {
        case .fruitAndVegetables: return 0
        case .bakery: return 1
        case .meat: return 2
        case .deli: return 3
        case .dairy: return 4
        case .frozen: return 5
        case .pantry: return 6
        case .beverages: return 7
        case .personalCare: return 8
        case .household: return 9
        case .other: return 10
        }
    }
}
