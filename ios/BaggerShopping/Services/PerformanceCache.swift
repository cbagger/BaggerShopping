import Foundation

struct CachedFlyerPublications: Codable {
    let savedAt: Date
    let publications: [OfferPublication]
}

enum FlyerPublicationCache {
    private static let key = "kurv-cached-flyer-publications-v1"

    static func save(_ publications: [OfferPublication]) {
        guard !publications.isEmpty else { return }
        let cached = CachedFlyerPublications(savedAt: Date(), publications: publications)
        guard let data = try? JSONEncoder().encode(cached) else { return }
        UserDefaults.standard.set(data, forKey: key)
    }

    static func load(maxAge: TimeInterval = 6 * 60 * 60) -> CachedFlyerPublications? {
        guard
            let data = UserDefaults.standard.data(forKey: key),
            let cached = try? JSONDecoder().decode(CachedFlyerPublications.self, from: data),
            Date().timeIntervalSince(cached.savedAt) <= maxAge
        else { return nil }
        let visible = cached.publications.filter {
            RetailerPreferences.shared.isEnabled($0.retailer)
        }
        return CachedFlyerPublications(savedAt: cached.savedAt, publications: visible)
    }
}

enum OfferRetailerShelf {
    static func retailers(from publications: [OfferPublication]) -> [String] {
        publications
            .filter { ["current", "upcoming"].contains($0.status) && $0.searchable }
            .map(\.retailer)
            .reduce(into: []) { result, retailer in
                guard RetailerPreferences.shared.isEnabled(retailer),
                      !result.contains(retailer) else { return }
                result.append(retailer)
            }
    }

    static func cachedRetailers(fallback: [String] = ["MENY"]) -> [String] {
        guard let cached = FlyerPublicationCache.load() else { return fallback }
        let retailers = retailers(from: cached.publications)
        return retailers.isEmpty ? fallback : retailers
    }
}

struct CachedFlyerOffers: Codable {
    let savedAt: Date
    let offers: [GroceryOffer]
}

enum FlyerOfferCache {
    // v5 invalidates Build 57 payloads now that Luna can audit every new page
    // semantically and feed verified member/variant facts into the offer layer.
    private static let prefix = "kurv-cached-flyer-offers-v5-"

    static func save(_ offers: [GroceryOffer], publicationID: String) {
        guard !offers.isEmpty else { return }
        let cached = CachedFlyerOffers(savedAt: Date(), offers: offers)
        guard let data = try? JSONEncoder().encode(cached) else { return }
        UserDefaults.standard.set(data, forKey: key(publicationID))
    }

    static func load(publicationID: String, maxAge: TimeInterval = 6 * 60 * 60) -> CachedFlyerOffers? {
        guard
            let data = UserDefaults.standard.data(forKey: key(publicationID)),
            let cached = try? JSONDecoder().decode(CachedFlyerOffers.self, from: data),
            Date().timeIntervalSince(cached.savedAt) <= maxAge
        else { return nil }
        return cached
    }

    private static func key(_ publicationID: String) -> String {
        let encoded = Data(publicationID.utf8).base64EncodedString()
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "=", with: "")
        return prefix + encoded
    }
}

struct CachedOfferSearch: Codable {
    let savedAt: Date
    let offers: [GroceryOffer]
}

enum OfferSearchCache {
    private static let prefix = "kurv-cached-offer-search-v5-"

    static func save(_ offers: [GroceryOffer], query: String, retailers: Set<String>) {
        guard !offers.isEmpty else { return }
        let visible = offers.filter { RetailerPreferences.shared.isEnabled($0.retailer) }
        guard !visible.isEmpty else { return }
        let cached = CachedOfferSearch(savedAt: Date(), offers: visible)
        guard let data = try? JSONEncoder().encode(cached) else { return }
        UserDefaults.standard.set(data, forKey: key(query: query, retailers: retailers))
    }

    static func load(query: String, retailers: Set<String>, maxAge: TimeInterval = 30 * 60) -> CachedOfferSearch? {
        guard
            let data = UserDefaults.standard.data(forKey: key(query: query, retailers: retailers)),
            let cached = try? JSONDecoder().decode(CachedOfferSearch.self, from: data),
            Date().timeIntervalSince(cached.savedAt) <= maxAge
        else { return nil }
        let visible = cached.offers.filter { RetailerPreferences.shared.isEnabled($0.retailer) }
        return CachedOfferSearch(savedAt: cached.savedAt, offers: visible)
    }

    private static func key(query: String, retailers: Set<String>) -> String {
        let normalized = query
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "da_DK"))
        let effective = RetailerPreferences.shared.effectiveRetailers(requested: Array(retailers))
        let retailerPart = effective.isEmpty ? "__none__" : effective.joined(separator: "|")
        let raw = normalized + "||" + retailerPart
        let encoded = Data(raw.utf8).base64EncodedString()
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "=", with: "")
        return prefix + encoded
    }
}

struct CachedSmartOfferMatches: Codable {
    let savedAt: Date
    let matches: [String: [GroceryOffer]]
}

enum SmartOfferMatchCache {
    private static let key = "kurv-cached-smart-offer-matches-v5"

    static func save(_ matches: [String: [GroceryOffer]]) {
        let cached = CachedSmartOfferMatches(savedAt: Date(), matches: matches)
        guard let data = try? JSONEncoder().encode(cached) else { return }
        UserDefaults.standard.set(data, forKey: key)
    }

    // Show the last known badges immediately and validate them in the
    // background. A six-hour display cache is deliberately longer than the
    // network refresh cadence; stale badges are replaced by the next batch
    // response without ever blanking the list during startup.
    static func load(maxAge: TimeInterval = 6 * 60 * 60) -> CachedSmartOfferMatches? {
        guard
            let data = UserDefaults.standard.data(forKey: key),
            let cached = try? JSONDecoder().decode(CachedSmartOfferMatches.self, from: data),
            Date().timeIntervalSince(cached.savedAt) <= maxAge
        else { return nil }
        return cached
    }
}

struct SmartOfferMatchGroup: Codable {
    let itemName: String
    let offers: [GroceryOffer]

    enum CodingKeys: String, CodingKey {
        case offers
        case itemName = "item_name"
    }
}

struct SmartOfferMatchResponse: Codable {
    let ok: Bool
    let itemCount: Int
    let offerCount: Int
    let matches: [SmartOfferMatchGroup]

    enum CodingKeys: String, CodingKey {
        case ok, matches
        case itemCount = "item_count"
        case offerCount = "offer_count"
    }
}

struct SmartOfferMatchAPI {
    private let baseURL = URL(string: "https://shopping.chewbagger.dk")!

    func fetch(items: [String]) async throws -> SmartOfferMatchResponse {
        guard let token = KeychainStore.loadToken(), !token.isEmpty else {
            throw APIClient.APIError.missingToken
        }
        let body = try JSONEncoder().encode(["items": items])
        var request = URLRequest(url: baseURL.appending(path: "/api/mobile/v1/offers/matches"))
        request.httpMethod = "POST"
        request.timeoutInterval = 20
        request.httpBody = body
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIClient.APIError.invalidResponse
        }
        guard 200..<300 ~= http.statusCode else {
            let message = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
                ?? String(data: data, encoding: .utf8)
                ?? "Ukendt fejl"
            throw APIClient.APIError.server(http.statusCode, message)
        }
        return try JSONDecoder().decode(SmartOfferMatchResponse.self, from: data)
    }
}
