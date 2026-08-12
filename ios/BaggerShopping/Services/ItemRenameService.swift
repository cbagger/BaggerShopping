import Foundation

struct ItemRenameResult {
    let warning: String?
}

struct ItemRenameService {
    private let baseURL = URL(string: "https://shopping.chewbagger.dk")!
    private let api = APIClient()

    enum RenameError: LocalizedError {
        case missingItemID
        case missingToken
        case invalidResponse
        case server(Int, String)

        var errorDescription: String? {
            switch self {
            case .missingItemID:
                return "Varen mangler et Samsung-ID og kan ikke omdøbes endnu."
            case .missingToken:
                return "Mobil-API token mangler."
            case .invalidResponse:
                return "Ugyldigt svar fra Bagger Shopping."
            case let .server(code, message):
                return "Serverfejl \(code): \(message)"
            }
        }
    }

    func rename(
        item: ShoppingItem,
        to requestedName: String,
        categoryOverride: ShoppingCategory?
    ) async throws -> ItemRenameResult {
        guard let itemID = item.id else { throw RenameError.missingItemID }
        guard let token = KeychainStore.loadToken(), !token.isEmpty else { throw RenameError.missingToken }

        let newName = requestedName
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
        guard !newName.isEmpty else { throw RenameError.invalidResponse }

        // Flush any queued offer-metadata write before the server moves the
        // metadata key from the old item name to the new one. This prevents an
        // old offline outbox entry from resurrecting the previous key later.
        _ = try await api.fetchOfferMetadata()

        let url = baseURL.appending(path: "/api/mobile/v1/items/\(itemID)/name")
        var request = URLRequest(url: url)
        request.httpMethod = "PATCH"
        request.timeoutInterval = 20
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["name": newName])

        let data = try await performIdempotentRename(request)

        // Decode just enough to make sure the server completed the requested
        // rename. The backend also moves shared offer metadata in the same call.
        guard let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              payload["ok"] as? Bool == true else {
            throw RenameError.invalidResponse
        }

        var warning: String?
        if let categoryOverride {
            do {
                try await api.setCategoryOverride(itemName: newName, category: categoryOverride)
                if ShoppingCategoryService.normalize(item.name) != ShoppingCategoryService.normalize(newName) {
                    try await api.removeCategoryOverride(itemName: item.name)
                }
            } catch {
                warning = "Varen blev omdøbt, men den valgte kategori kunne ikke flyttes til det nye navn endnu: \(error.localizedDescription)"
            }
        }

        return ItemRenameResult(warning: warning)
    }

    private func performIdempotentRename(_ request: URLRequest) async throws -> Data {
        var lastError: Error?
        for attempt in 1...3 {
            do {
                let (data, response) = try await URLSession.shared.data(for: request)
                guard let http = response as? HTTPURLResponse else {
                    throw RenameError.invalidResponse
                }
                guard 200..<300 ~= http.statusCode else {
                    throw RenameError.server(
                        http.statusCode,
                        String(data: data, encoding: .utf8) ?? "Ukendt fejl"
                    )
                }
                return data
            } catch {
                lastError = error
                guard attempt < 3, isTransient(error) else { throw error }
                try await Task.sleep(for: .milliseconds(350 * attempt))
            }
        }
        throw lastError ?? RenameError.invalidResponse
    }

    private func isTransient(_ error: Error) -> Bool {
        if let urlError = error as? URLError {
            return [
                .timedOut,
                .cannotFindHost,
                .cannotConnectToHost,
                .dnsLookupFailed,
                .networkConnectionLost,
                .notConnectedToInternet,
            ].contains(urlError.code)
        }
        if case let RenameError.server(code, _) = error {
            return [502, 503, 504].contains(code)
        }
        return false
    }
}
