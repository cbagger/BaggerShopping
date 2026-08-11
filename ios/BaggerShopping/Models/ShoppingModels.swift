import Foundation
import CoreLocation

struct ShoppingItem: Codable, Identifiable, Hashable {
    let id: String?
    let name: String
    var checked: Bool
    var quantity: Double?
    var unit: String?

    init(
        id: String?,
        name: String,
        checked: Bool,
        quantity: Double? = nil,
        unit: String? = nil
    ) {
        self.id = id
        self.name = name
        self.checked = checked
        self.quantity = quantity
        self.unit = unit
    }

    var stableID: String { id ?? name }

    var displayQuantity: String? {
        guard let quantity, quantity > 1 else { return nil }
        if quantity.rounded() == quantity {
            return "×\(Int(quantity))"
        }
        return "×\(quantity.formatted(.number.precision(.fractionLength(0...1))))"
    }
}

struct ShoppingListResponse: Codable {
    let ok: Bool
    let name: String
    let count: Int
    let hasItems: Bool
    var items: [ShoppingItem]

    enum CodingKeys: String, CodingKey {
        case ok, name, count, items
        case hasItems = "has_items"
    }
}

struct AddItemResponse: Codable {
    let ok: Bool
    let name: String
}

struct CategoryOverrideDTO: Codable {
    let itemName: String
    let category: String

    enum CodingKeys: String, CodingKey {
        case category
        case itemName = "item_name"
    }
}

struct CategoryOverridesResponse: Codable {
    let ok: Bool
    let overrides: [CategoryOverrideDTO]
}

struct StoreLocation: Codable, Identifiable, Hashable {
    let id: UUID
    var name: String
    var address: String
    var latitude: Double
    var longitude: Double
    var radius: Double
    var enabled: Bool

    init(
        id: UUID = UUID(),
        name: String,
        address: String = "",
        latitude: Double,
        longitude: Double,
        radius: Double = 180,
        enabled: Bool = true
    ) {
        self.id = id
        self.name = name
        self.address = address
        self.latitude = latitude
        self.longitude = longitude
        self.radius = radius
        self.enabled = enabled
    }

    var coordinate: CLLocationCoordinate2D {
        CLLocationCoordinate2D(latitude: latitude, longitude: longitude)
    }
}

struct StoreSearchResult: Identifiable, Hashable {
    let id = UUID()
    let name: String
    let address: String
    let latitude: Double
    let longitude: Double
}
