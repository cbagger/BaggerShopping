import Foundation

struct APIClient {
    let baseURL = URL(string: "https://shopping.chewbagger.dk")!

    enum APIError: LocalizedError {
        case missingToken
        case invalidResponse
        case missingItemID
        case server(Int, String)

        var errorDescription: String? {
            switch self {
            case .missingToken: return "Mobil-API token mangler."
            case .invalidResponse: return "Ugyldigt svar fra Bagger Shopping."
            case .missingItemID: return "Varen mangler et Samsung-ID."
            case let .server(code, message): return "Serverfejl \(code): \(message)"
            }
        }
    }

    private func request(path: String, method: String = "GET", body: Data? = nil, queryItems: [URLQueryItem] = []) throws -> URLRequest {
        guard let token = KeychainStore.loadToken(), !token.isEmpty else { throw APIError.missingToken }
        let url = baseURL.appending(path: path)
        guard var components = URLComponents(url: url, resolvingAgainstBaseURL: false) else { throw APIError.invalidResponse }
        if !queryItems.isEmpty {
            components.queryItems = queryItems
        }
        guard let finalURL = components.url else { throw APIError.invalidResponse }
        var request = URLRequest(url: finalURL)
        request.httpMethod = method
        request.timeoutInterval = 20
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }

    private func perform(_ request: URLRequest) async throws -> Data {
        var lastError: Error?
        let attempts = request.httpMethod == "GET" ? 3 : 1
        for attempt in 1...attempts {
            do {
                let (data, response) = try await URLSession.shared.data(for: request)
                try validate(response: response, data: data)
                return data
            } catch {
                lastError = error
                guard attempt < attempts, isTransient(error) else { throw error }
                try await Task.sleep(for: .milliseconds(350 * attempt))
            }
        }
        throw lastError ?? APIError.invalidResponse
    }

    private func isTransient(_ error: Error) -> Bool {
        if let urlError = error as? URLError {
            return [.timedOut, .cannotFindHost, .cannotConnectToHost, .dnsLookupFailed,
                    .networkConnectionLost, .notConnectedToInternet].contains(urlError.code)
        }
        if case let APIError.server(code, message) = error {
            return [502, 503, 504].contains(code) || message.localizedCaseInsensitiveContains("name resolution")
        }
        return false
    }

    func fetchList() async throws -> ShoppingListResponse {
        let data = try await perform(request(path: "/api/mobile/v1/list"))
        return try JSONDecoder().decode(ShoppingListResponse.self, from: data)
    }

    func addItem(name: String) async throws {
        let body = try JSONEncoder().encode(["name": name])
        _ = try await perform(request(path: "/api/mobile/v1/items", method: "POST", body: body))
    }

    func setChecked(item: ShoppingItem, checked: Bool) async throws {
        guard let id = item.id else { throw APIError.missingItemID }
        let body = try JSONEncoder().encode(["checked": checked])
        _ = try await perform(request(path: "/api/mobile/v1/items/\(id)/checked", method: "PATCH", body: body))
    }

    func setQuantity(item: ShoppingItem, quantity: Double, unit: String = "stk") async throws {
        guard let id = item.id else { throw APIError.missingItemID }
        let body = try JSONSerialization.data(withJSONObject: ["quantity": quantity, "unit": unit])
        _ = try await perform(request(path: "/api/mobile/v1/items/\(id)/quantity", method: "PATCH", body: body))
    }

    func deleteItem(_ item: ShoppingItem) async throws {
        guard let id = item.id else { throw APIError.missingItemID }
        _ = try await perform(request(path: "/api/mobile/v1/items/\(id)", method: "DELETE"))
    }

    func fetchCategoryOverrides() async throws -> CategoryOverridesResponse {
        let data = try await perform(request(path: "/api/mobile/v1/category-overrides"))
        return try JSONDecoder().decode(CategoryOverridesResponse.self, from: data)
    }

    func setCategoryOverride(itemName: String, category: ShoppingCategory) async throws {
        let body = try JSONSerialization.data(withJSONObject: [
            "item_name": itemName,
            "category": category.rawValue
        ])
        _ = try await perform(request(path: "/api/mobile/v1/category-overrides", method: "PUT", body: body))
    }

    func removeCategoryOverride(itemName: String) async throws {
        let body = try JSONSerialization.data(withJSONObject: ["item_name": itemName])
        _ = try await perform(request(path: "/api/mobile/v1/category-overrides/remove", method: "POST", body: body))
    }

    func clearCategoryOverrides() async throws {
        _ = try await perform(request(path: "/api/mobile/v1/category-overrides", method: "DELETE"))
    }

    func fetchOfferMetadata() async throws -> OfferMetadataResponse {
        let data = try await perform(request(path: "/api/mobile/v1/offer-metadata"))
        return try JSONDecoder().decode(OfferMetadataResponse.self, from: data)
    }

    func setOfferMetadata(_ metadata: OfferMetadataDTO) async throws {
        let body = try JSONEncoder().encode(metadata)
        _ = try await perform(request(path: "/api/mobile/v1/offer-metadata", method: "PUT", body: body))
    }

    func syncOfferMetadata(_ metadata: [OfferMetadataDTO]) async throws -> OfferMetadataResponse {
        let body = try JSONEncoder().encode(OfferMetadataSyncRequest(metadata: metadata))
        let data = try await perform(request(path: "/api/mobile/v1/offer-metadata/sync", method: "PUT", body: body))
        return try JSONDecoder().decode(OfferMetadataResponse.self, from: data)
    }

    func removeOfferMetadata(itemName: String) async throws {
        let body = try JSONSerialization.data(withJSONObject: ["item_name": itemName])
        _ = try await perform(request(path: "/api/mobile/v1/offer-metadata/remove", method: "POST", body: body))
    }

    func fetchOfferPublications() async throws -> PublicationsResponse {
        let data = try await perform(request(path: "/api/mobile/v1/offers/publications"))
        return try JSONDecoder().decode(PublicationsResponse.self, from: data)
    }

    func searchOffers(query: String, retailers: [String] = []) async throws -> OfferSearchResponse {
        var queryItems = [URLQueryItem(name: "q", value: query)]
        if !retailers.isEmpty {
            queryItems.append(URLQueryItem(name: "retailer", value: retailers.joined(separator: ",")))
        }
        let data = try await perform(
            request(
                path: "/api/mobile/v1/offers/search",
                queryItems: queryItems
            )
        )
        return try JSONDecoder().decode(OfferSearchResponse.self, from: data)
    }

    func fetchOffers(publicationID: String) async throws -> PublicationOffersResponse {
        let data = try await perform(request(path: "/api/mobile/v1/offers/publications/\(publicationID)/offers"))
        return try JSONDecoder().decode(PublicationOffersResponse.self, from: data)
    }

    private func validate(response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard 200..<300 ~= http.statusCode else {
            throw APIError.server(http.statusCode, String(data: data, encoding: .utf8) ?? "Ukendt fejl")
        }
    }
}
