import Foundation
import CoreLocation

struct ShoppingItem: Codable, Identifiable, Hashable {
    let id: String?
    let name: String
    let checked: Bool

    var stableID: String { id ?? name }
}

struct ShoppingListResponse: Codable {
    let ok: Bool
    let name: String
    let count: Int
    let hasItems: Bool
    let items: [ShoppingItem]

    enum CodingKeys: String, CodingKey {
        case ok, name, count, items
        case hasItems = "has_items"
    }
}

struct AddItemResponse: Codable {
    let ok: Bool
    let name: String
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
