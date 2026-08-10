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

    func fetchList() async throws -> ShoppingListResponse {
        let (data, response) = try await URLSession.shared.data(for: request(path: "/api/mobile/v1/list"))
        try validate(response: response, data: data)
        return try JSONDecoder().decode(ShoppingListResponse.self, from: data)
    }

    func addItem(name: String) async throws {
        let body = try JSONEncoder().encode(["name": name])
        let (data, response) = try await URLSession.shared.data(for: request(path: "/api/mobile/v1/items", method: "POST", body: body))
        try validate(response: response, data: data)
    }

    func setChecked(item: ShoppingItem, checked: Bool) async throws {
        guard let id = item.id else { throw APIError.missingItemID }
        let body = try JSONEncoder().encode(["checked": checked])
        let (data, response) = try await URLSession.shared.data(for: request(path: "/api/mobile/v1/items/\(id)/checked", method: "PATCH", body: body))
        try validate(response: response, data: data)
    }

    func deleteItem(_ item: ShoppingItem) async throws {
        guard let id = item.id else { throw APIError.missingItemID }
        let (data, response) = try await URLSession.shared.data(for: request(path: "/api/mobile/v1/items/\(id)", method: "DELETE"))
        try validate(response: response, data: data)
    }

    private func validate(response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard 200..<300 ~= http.statusCode else {
            throw APIError.server(http.statusCode, String(data: data, encoding: .utf8) ?? "Ukendt fejl")
        }
    }
}
