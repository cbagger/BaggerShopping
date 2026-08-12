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
    let pageImageURLs: [URL]
    let readerURL: URL?
    let readerKind: String?
    let searchable: Bool

    enum CodingKeys: String, CodingKey {
        case id, retailer, title, status
        case validFrom = "valid_from"
        case validUntil = "valid_until"
        case sourceURL = "source_url"
        case pageCount = "page_count"
        case pageImageURLs = "page_image_urls"
        case readerURL = "reader_url"
        case readerKind = "reader_kind"
        case searchable
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        retailer = try values.decode(String.self, forKey: .retailer)
        title = try values.decode(String.self, forKey: .title)
        validFrom = try values.decodeIfPresent(String.self, forKey: .validFrom)
        validUntil = try values.decodeIfPresent(String.self, forKey: .validUntil)
        status = try values.decode(String.self, forKey: .status)
        sourceURL = try values.decode(URL.self, forKey: .sourceURL)
        pageCount = try values.decode(Int.self, forKey: .pageCount)
        pageImageURLs = try values.decodeIfPresent([URL].self, forKey: .pageImageURLs) ?? []
        readerURL = try values.decodeIfPresent(URL.self, forKey: .readerURL)
        readerKind = try values.decodeIfPresent(String.self, forKey: .readerKind)
        searchable = try values.decodeIfPresent(Bool.self, forKey: .searchable) ?? false
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
    let hotspotX: Double?
    let hotspotY: Double?
    let hotspotWidth: Double?
    let hotspotHeight: Double?
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
        case hotspotX = "hotspot_x"
        case hotspotY = "hotspot_y"
        case hotspotWidth = "hotspot_width"
        case hotspotHeight = "hotspot_height"
        case rawText = "raw_text"
        case safeToAdd = "safe_to_add"
        case variants
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        retailer = try values.decode(String.self, forKey: .retailer)
        publicationID = try values.decode(String.self, forKey: .publicationID)
        publicationTitle = try values.decode(String.self, forKey: .publicationTitle)
        validFrom = try values.decodeIfPresent(String.self, forKey: .validFrom)
        validUntil = try values.decodeIfPresent(String.self, forKey: .validUntil)
        productName = try values.decode(String.self, forKey: .productName)
        brand = try values.decodeIfPresent(String.self, forKey: .brand)
        price = try values.decodeIfPresent(Double.self, forKey: .price)
        normalPrice = try values.decodeIfPresent(Double.self, forKey: .normalPrice)
        quantity = try values.decodeIfPresent(Double.self, forKey: .quantity)
        unit = try values.decodeIfPresent(String.self, forKey: .unit)
        unitPrice = try values.decodeIfPresent(String.self, forKey: .unitPrice)
        discountPercent = try values.decodeIfPresent(Int.self, forKey: .discountPercent)
        imageURL = try values.decodeIfPresent(URL.self, forKey: .imageURL)
        sourceURL = try values.decode(URL.self, forKey: .sourceURL)
        pageNumber = try values.decodeIfPresent(Int.self, forKey: .pageNumber)
        hotspotX = try values.decodeIfPresent(Double.self, forKey: .hotspotX)
        hotspotY = try values.decodeIfPresent(Double.self, forKey: .hotspotY)
        hotspotWidth = try values.decodeIfPresent(Double.self, forKey: .hotspotWidth)
        hotspotHeight = try values.decodeIfPresent(Double.self, forKey: .hotspotHeight)
        rawText = try values.decodeIfPresent(String.self, forKey: .rawText) ?? ""
        safeToAdd = try values.decodeIfPresent(Bool.self, forKey: .safeToAdd) ?? false
        variants = try values.decodeIfPresent([OfferVariant].self, forKey: .variants) ?? []
    }
}

struct OfferVariant: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let description: String?
    let quantity: Double?
    let unit: String?
    let matchesQuery: Bool

    enum CodingKeys: String, CodingKey {
        case id, name, description, quantity, unit
        case matchesQuery = "matches_query"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        name = try values.decode(String.self, forKey: .name)
        description = try values.decodeIfPresent(String.self, forKey: .description)
        quantity = try values.decodeIfPresent(Double.self, forKey: .quantity)
        unit = try values.decodeIfPresent(String.self, forKey: .unit)
        matchesQuery = try values.decodeIfPresent(Bool.self, forKey: .matchesQuery) ?? false
    }
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

struct PublicationOffersResponse: Codable {
    let ok: Bool
    let publication: OfferPublication
    let offers: [GroceryOffer]
}
