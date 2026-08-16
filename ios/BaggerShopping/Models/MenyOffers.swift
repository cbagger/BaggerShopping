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
    let memberPrice: Double?
    let memberPriceLabel: String?
    let memberPriceApp: String?
    let memberPriceRequiresActivation: Bool
    let memberPriceSource: String?
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
    let identityMatch: ProductIdentityMatch?
    let productIdentity: ProductIdentityAnalysis?
    let publicationStatus: String?
    let hotspotConfidence: Double
    let variantConfidence: Double
    let qualityScore: Double
    let qualitySource: String
    let qualityIssues: [String]
    let qualitySignals: [String]

    enum CodingKeys: String, CodingKey {
        case id, retailer, brand, price, quantity, unit
        case publicationID = "publication_id"
        case publicationTitle = "publication_title"
        case validFrom = "valid_from"
        case validUntil = "valid_until"
        case productName = "product_name"
        case normalPrice = "normal_price"
        case memberPrice = "member_price"
        case memberPriceLabel = "member_price_label"
        case memberPriceApp = "member_price_app"
        case memberPriceRequiresActivation = "member_price_requires_activation"
        case memberPriceSource = "member_price_source"
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
        case identityMatch = "identity_match"
        case productIdentity = "product_identity"
        case publicationStatus = "publication_status"
        case hotspotConfidence = "hotspot_confidence"
        case variantConfidence = "variant_confidence"
        case qualityScore = "quality_score"
        case qualitySource = "quality_source"
        case qualityIssues = "quality_issues"
        case qualitySignals = "quality_signals"
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
        memberPrice = try values.decodeIfPresent(Double.self, forKey: .memberPrice)
        memberPriceLabel = try values.decodeIfPresent(String.self, forKey: .memberPriceLabel)
        memberPriceApp = try values.decodeIfPresent(String.self, forKey: .memberPriceApp)
        memberPriceRequiresActivation = try values.decodeIfPresent(Bool.self, forKey: .memberPriceRequiresActivation) ?? false
        memberPriceSource = try values.decodeIfPresent(String.self, forKey: .memberPriceSource)
        quantity = try values.decodeIfPresent(Double.self, forKey: .quantity)
        unit = try values.decodeIfPresent(String.self, forKey: .unit)
        // Parsed unit-price data is intentionally hidden for now. Source flyers
        // often describe a range that is copied onto every parsed variant, so
        // exposing it would imply a precision Kurv does not currently have.
        unitPrice = nil
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
        identityMatch = try values.decodeIfPresent(ProductIdentityMatch.self, forKey: .identityMatch)
        productIdentity = try values.decodeIfPresent(ProductIdentityAnalysis.self, forKey: .productIdentity)
        publicationStatus = try values.decodeIfPresent(String.self, forKey: .publicationStatus)
        hotspotConfidence = try values.decodeIfPresent(Double.self, forKey: .hotspotConfidence) ?? 0
        variantConfidence = try values.decodeIfPresent(Double.self, forKey: .variantConfidence) ?? 0
        qualityScore = try values.decodeIfPresent(Double.self, forKey: .qualityScore) ?? 0
        qualitySource = try values.decodeIfPresent(String.self, forKey: .qualitySource) ?? "unknown"
        qualityIssues = try values.decodeIfPresent([String].self, forKey: .qualityIssues) ?? []
        qualitySignals = try values.decodeIfPresent([String].self, forKey: .qualitySignals) ?? []
    }

    var memberPriceDisplayLabel: String {
        let label = memberPriceLabel?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return label.isEmpty ? "Medlemspris" : label
    }

    var lowestListedPrice: Double? {
        [price, memberPrice].compactMap { $0 }.min()
    }

    var lowestListedPriceRequiresMembership: Bool {
        guard let memberPrice else { return false }
        guard let price else { return true }
        return memberPrice < price
    }
}

struct ProductIdentityMatch: Codable, Hashable {
    let level: String
    let confidence: Double
    let explanation: String
    let directPriceComparison: Bool
    let evidence: [String]?
    let conflicts: [String]?

    enum CodingKeys: String, CodingKey {
        case level, confidence, explanation, evidence, conflicts
        case directPriceComparison = "direct_price_comparison"
    }
}

struct ProductIdentityCompareResponse: Codable {
    let level: String
    let confidence: Double
    let explanation: String
    let directPriceComparison: Bool
    let left: ProductIdentityAnalysis
    let right: ProductIdentityAnalysis
    let evidence: [String]?
    let conflicts: [String]?

    enum CodingKeys: String, CodingKey {
        case level, confidence, explanation, left, right, evidence, conflicts
        case directPriceComparison = "direct_price_comparison"
    }
}

struct ProductIdentityAnalysis: Codable, Hashable {
    let brand: String?
    let product: String
    let variant: String?
    let flavours: [String]
    let types: [String]
    let packCount: Int
    let amountText: String?
    let unitPrice: Double?
    let unitPriceMin: Double?
    let unitPriceMax: Double?
    let unitPriceUnit: String?
    let canonicalFamily: String?
    let evidence: [String]?

    enum CodingKeys: String, CodingKey {
        case brand, product, variant, flavours, types
        case packCount = "pack_count"
        case amountText = "amount_text"
        case unitPrice = "unit_price"
        case unitPriceMin = "unit_price_min"
        case unitPriceMax = "unit_price_max"
        case unitPriceUnit = "unit_price_unit"
        case canonicalFamily = "canonical_family"
        case evidence
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        brand = try values.decodeIfPresent(String.self, forKey: .brand)
        product = try values.decode(String.self, forKey: .product)
        variant = try values.decodeIfPresent(String.self, forKey: .variant)
        flavours = try values.decodeIfPresent([String].self, forKey: .flavours) ?? []
        types = try values.decodeIfPresent([String].self, forKey: .types) ?? []
        packCount = try values.decodeIfPresent(Int.self, forKey: .packCount) ?? 1

        // Keep uncertain size/unit-price data out of the iOS presentation and
        // matching layer until variant-specific extraction is trustworthy.
        // Backend/source data remains untouched and can be re-enabled later.
        amountText = nil
        unitPrice = nil
        unitPriceMin = nil
        unitPriceMax = nil
        unitPriceUnit = nil

        canonicalFamily = try values.decodeIfPresent(String.self, forKey: .canonicalFamily)
        evidence = try values.decodeIfPresent([String].self, forKey: .evidence)
    }
}

struct OfferVariant: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let description: String?
    let quantity: Double?
    let unit: String?
    let matchesQuery: Bool
    let identity: ProductIdentityAnalysis?

    enum CodingKeys: String, CodingKey {
        case id, name, description, quantity, unit
        case matchesQuery = "matches_query"
        case identity
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        name = try values.decode(String.self, forKey: .name)
        description = try values.decodeIfPresent(String.self, forKey: .description)
        quantity = try values.decodeIfPresent(Double.self, forKey: .quantity)
        unit = try values.decodeIfPresent(String.self, forKey: .unit)
        matchesQuery = try values.decodeIfPresent(Bool.self, forKey: .matchesQuery) ?? false
        identity = try values.decodeIfPresent(ProductIdentityAnalysis.self, forKey: .identity)
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
    let publication: OfferPublication?
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
