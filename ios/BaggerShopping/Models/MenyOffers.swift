import Foundation

struct MenyPublication: Codable {
    let title: String
    let validFrom: String?
    let validUntil: String?
    let contentSource: String?
    let pageCount: Int?

    enum CodingKeys: String, CodingKey {
        case title
        case validFrom = "valid_from"
        case validUntil = "valid_until"
        case contentSource = "content_source"
        case pageCount = "page_count"
    }
}

struct MenyOfferStatusResponse: Codable {
    let ok: Bool
    let retailer: String
    let publication: MenyPublication
}

struct MenyOfferSearchResponse: Codable {
    let ok: Bool
    let retailer: String
    let query: String
    let publication: MenyPublication
    let matchCount: Int
    let matches: [String]

    enum CodingKeys: String, CodingKey {
        case ok, retailer, query, publication, matches
        case matchCount = "match_count"
    }
}
