import Foundation

struct OfferPublication: Codable, Identifiable, Hashable {
    let id: String
    let retailer: String
    let title: String
    let validFrom: String?
    let validUntil: String?
    let status: String
    let sourceURL: URL
    let pageCount: Int
    let readerURL: URL?
    let readerKind: String?

    enum CodingKeys: String, CodingKey {
        case id, retailer, title, status
        case validFrom = "valid_from"
        case validUntil = "valid_until"
        case sourceURL = "source_url"
        case pageCount = "page_count"
        case readerURL = "reader_url"
        case readerKind = "reader_kind"
    }
}

struct GroceryOffer: Codable, Identifiable, Hashable {
    let id: String
    let retailer: String
    let publicationID: String
    let publicationTitle: String
    let validFrom: String?
    let validUntil: String?
    let productName: String
    let brand: String?
    let price: Double?
    let normalPrice: Double?
    let quantity: Double?
    let unit: String?
    let unitPrice: String?
    let discountPercent: Int?
    let imageURL: URL?
    let sourceURL: URL
    let pageNumber: Int?
    let rawText: String
    let safeToAdd: Bool
    let variants: [OfferVariant]

    enum CodingKeys: String, CodingKey {
        case id, retailer, brand, price, quantity, unit
        case publicationID = "publication_id"
        case publicationTitle = "publication_title"
        case validFrom = "valid_from"
        case validUntil = "valid_until"
        case productName = "product_name"
        case normalPrice = "normal_price"
        case unitPrice = "unit_price"
        case discountPercent = "discount_percent"
        case imageURL = "image_url"
        case sourceURL = "source_url"
        case pageNumber = "page_number"
        case rawText = "raw_text"
        case safeToAdd = "safe_to_add"
        case variants
    }
}

struct OfferVariant: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let description: String?
    let quantity: Double?
    let unit: String?
}

struct PublicationsResponse: Codable {
    let ok: Bool
    let publications: [OfferPublication]
}

struct OfferSearchResponse: Codable {
    let ok: Bool
    let query: String
    let retailer: String
    let publication: OfferPublication
    let offerCount: Int
    let offers: [GroceryOffer]

    enum CodingKeys: String, CodingKey {
        case ok, query, retailer, publication, offers
        case offerCount = "offer_count"
    }
}
