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

    private func request(path: String, method: String = "GET", body: Data? = nil) throws -> URLRequest {
        guard let token = KeychainStore.loadToken(), !token.isEmpty else { throw APIError.missingToken }
        let url = baseURL.appending(path: path)
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 15
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }

    private func perform(_ request: URLRequest) async throws -> Data {
        let (data, response) = try await URLSession.shared.data(for: request)
        try validate(response: response, data: data)
        return data
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

    private func validate(response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard 200..<300 ~= http.statusCode else {
            throw APIError.server(http.statusCode, String(data: data, encoding: .utf8) ?? "Ukendt fejl")
        }
    }
}
