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
        return cached
    }
}

struct CachedSmartOfferMatches: Codable {
    let savedAt: Date
    let matches: [String: [GroceryOffer]]
}

enum SmartOfferMatchCache {
    private static let key = "kurv-cached-smart-offer-matches-v1"

    static func save(_ matches: [String: [GroceryOffer]]) {
        let cached = CachedSmartOfferMatches(savedAt: Date(), matches: matches)
        guard let data = try? JSONEncoder().encode(cached) else { return }
        UserDefaults.standard.set(data, forKey: key)
    }

    static func load(maxAge: TimeInterval = 30 * 60) -> CachedSmartOfferMatches? {
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
