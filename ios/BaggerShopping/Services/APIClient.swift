import Foundation

struct APIClient {
    let baseURL = URL(string: "https://shopping.chewbagger.dk")!
    private static let pendingOfferMetadataKey = "bagger-shopping-pending-offer-metadata-v1"

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
        let retryableMethods = ["GET", "PUT"]
        let attempts = retryableMethods.contains(request.httpMethod ?? "") ? 3 : 1
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

    func deleteAllCheckedItems() async throws {
        _ = try await perform(request(path: "/api/mobile/v1/actions/clear-checked", method: "DELETE"))
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
        try await flushPendingOfferMetadata()
        let data = try await perform(request(path: "/api/mobile/v1/offer-metadata"))
        return try JSONDecoder().decode(OfferMetadataResponse.self, from: data)
    }

    func setOfferMetadata(_ metadata: OfferMetadataDTO) async throws {
        enqueuePendingOfferMetadata(metadata)
        do {
            try await writeOfferMetadata(metadata)
            removePendingOfferMetadata(itemName: metadata.itemName)
        } catch {
            // Keep the exact intended write in UserDefaults. The next metadata
            // refresh will retry it before reading QNAP, so a transient iPhone
            // request can no longer silently lose family-shared offer state.
            throw error
        }
    }

    func syncOfferMetadata(_ metadata: [OfferMetadataDTO]) async throws -> OfferMetadataResponse {
        try await flushPendingOfferMetadata()
        let body = try JSONEncoder().encode(OfferMetadataSyncRequest(metadata: metadata))
        let data = try await perform(request(path: "/api/mobile/v1/offer-metadata/sync", method: "PUT", body: body))
        return try JSONDecoder().decode(OfferMetadataResponse.self, from: data)
    }

    func removeOfferMetadata(itemName: String) async throws {
        // A delete/manual same-name add must also cancel any queued stale PUT,
        // otherwise an offline write could resurrect metadata after removal.
        removePendingOfferMetadata(itemName: itemName)
        let body = try JSONSerialization.data(withJSONObject: ["item_name": itemName])
        _ = try await perform(request(path: "/api/mobile/v1/offer-metadata/remove", method: "POST", body: body))
    }

    private func writeOfferMetadata(_ metadata: OfferMetadataDTO) async throws {
        let body = try JSONEncoder().encode(metadata)
        _ = try await perform(request(path: "/api/mobile/v1/offer-metadata", method: "PUT", body: body))
    }

    private func flushPendingOfferMetadata() async throws {
        let pending = loadPendingOfferMetadata()
        guard !pending.isEmpty else { return }
        for metadata in pending.values {
            try await writeOfferMetadata(metadata)
            removePendingOfferMetadata(itemName: metadata.itemName)
        }
    }

    private func pendingOfferMetadataKey(for itemName: String) -> String {
        itemName.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    private func loadPendingOfferMetadata() -> [String: OfferMetadataDTO] {
        guard let data = UserDefaults.standard.data(forKey: Self.pendingOfferMetadataKey),
              let decoded = try? JSONDecoder().decode([String: OfferMetadataDTO].self, from: data) else {
            return [:]
        }
        return decoded
    }

    private func savePendingOfferMetadata(_ pending: [String: OfferMetadataDTO]) {
        if pending.isEmpty {
            UserDefaults.standard.removeObject(forKey: Self.pendingOfferMetadataKey)
            return
        }
        guard let data = try? JSONEncoder().encode(pending) else { return }
        UserDefaults.standard.set(data, forKey: Self.pendingOfferMetadataKey)
    }

    private func enqueuePendingOfferMetadata(_ metadata: OfferMetadataDTO) {
        var pending = loadPendingOfferMetadata()
        pending[pendingOfferMetadataKey(for: metadata.itemName)] = metadata
        savePendingOfferMetadata(pending)
    }

    private func removePendingOfferMetadata(itemName: String) {
        var pending = loadPendingOfferMetadata()
        pending.removeValue(forKey: pendingOfferMetadataKey(for: itemName))
        savePendingOfferMetadata(pending)
    }

    func fetchOfferPublications() async throws -> PublicationsResponse {
        let data = try await perform(request(path: "/api/mobile/v1/offers/publications"))
        return try JSONDecoder().decode(PublicationsResponse.self, from: data)
    }

    func fetchFlyerNotificationRetailers() async throws -> FlyerNotificationRetailersResponse {
        let data = try await perform(request(path: "/api/mobile/v1/flyer-notifications/retailers"))
        return try JSONDecoder().decode(FlyerNotificationRetailersResponse.self, from: data)
    }

    func setFlyerNotificationDevice(
        deviceID: String,
        deviceToken: String,
        retailers: [String],
        enabled: Bool
    ) async throws {
        #if DEBUG
        let environment = "sandbox"
        #else
        let environment = "production"
        #endif
        let body = try JSONSerialization.data(withJSONObject: [
            "device_id": deviceID,
            "device_token": deviceToken,
            "retailers": retailers,
            "enabled": enabled,
            "environment": environment
        ])
        _ = try await perform(request(path: "/api/mobile/v1/flyer-notifications/device", method: "PUT", body: body))
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
        let response = try JSONDecoder().decode(OfferSearchResponse.self, from: data)
        return response
    }

    func compareProducts(
        left: String,
        leftQuantity: Double?,
        leftUnit: String?,
        right: String,
        rightQuantity: Double?,
        rightUnit: String?
    ) async throws -> ProductIdentityCompareResponse {
        var payload: [String: Any] = ["left": left, "right": right]
        if let leftQuantity { payload["left_quantity"] = leftQuantity }
        if let leftUnit { payload["left_unit"] = leftUnit }
        if let rightQuantity { payload["right_quantity"] = rightQuantity }
        if let rightUnit { payload["right_unit"] = rightUnit }
        let body = try JSONSerialization.data(withJSONObject: payload)
        let data = try await perform(request(path: "/api/mobile/v1/product-identity/compare", method: "POST", body: body))
        return try JSONDecoder().decode(ProductIdentityCompareResponse.self, from: data)
    }

    func submitProductMatchFeedback(left: String, right: String, decision: String) async throws {
        let body = try JSONSerialization.data(withJSONObject: [
            "left": left,
            "right": right,
            "decision": decision
        ])
        _ = try await perform(request(path: "/api/mobile/v1/product-identity/feedback", method: "POST", body: body))
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

    func fetchHouseholdProfile() async throws -> HouseholdProfile {
        let data = try await perform(request(path: "/api/mobile/v1/households/me"))
        return try JSONDecoder().decode(HouseholdProfile.self, from: data)
    }

    func createHousehold(name: String, memberName: String) async throws -> HouseholdAuthResponse {
        let body = try JSONSerialization.data(withJSONObject: ["household_name": name, "member_name": memberName])
        let data = try await performPublic(path: "/api/mobile/v1/households/create", body: body)
        return try JSONDecoder().decode(HouseholdAuthResponse.self, from: data)
    }

    func joinHousehold(code: String, memberName: String) async throws -> HouseholdAuthResponse {
        let body = try JSONSerialization.data(withJSONObject: ["invite_code": code, "member_name": memberName])
        let data = try await performPublic(path: "/api/mobile/v1/households/join", body: body)
        return try JSONDecoder().decode(HouseholdAuthResponse.self, from: data)
    }

    func recoverHousehold(code: String, memberName: String) async throws -> HouseholdAuthResponse {
        let body = try JSONSerialization.data(withJSONObject: ["recovery_code": code, "member_name": memberName])
        let data = try await performPublic(path: "/api/mobile/v1/households/recover", body: body)
        return try JSONDecoder().decode(HouseholdAuthResponse.self, from: data)
    }

    func fetchRecoveryStatus() async throws -> HouseholdRecoveryStatus {
        let data = try await perform(request(path: "/api/mobile/v1/households/recovery"))
        return try JSONDecoder().decode(HouseholdRecoveryStatus.self, from: data)
    }

    func rotateRecoveryCode() async throws -> String {
        let data = try await perform(request(path: "/api/mobile/v1/households/recovery/rotate", method: "POST", body: Data("{}".utf8)))
        return try JSONDecoder().decode(HouseholdRecoveryCodeResponse.self, from: data).recoveryCode
    }

    func createHouseholdInvite() async throws -> HouseholdInviteResponse {
        let body = try JSONSerialization.data(withJSONObject: ["expires_in_days": 7])
        let data = try await perform(request(path: "/api/mobile/v1/households/invite", method: "POST", body: body))
        return try JSONDecoder().decode(HouseholdInviteResponse.self, from: data)
    }

    func fetchHouseholdMembers() async throws -> [HouseholdMember] {
        let data = try await perform(request(path: "/api/mobile/v1/households/members"))
        return try JSONDecoder().decode(HouseholdMembersResponse.self, from: data).members
    }

    func updateHouseholdMember(id: String, name: String) async throws {
        let body = try JSONEncoder().encode(["name": name])
        _ = try await perform(request(path: "/api/mobile/v1/households/members/\(id)", method: "PATCH", body: body))
    }

    func removeHouseholdMember(id: String) async throws {
        _ = try await perform(request(path: "/api/mobile/v1/households/members/\(id)", method: "DELETE"))
    }

    func fetchSamsungIntegration() async throws -> SamsungIntegrationStatus {
        let data = try await perform(request(path: "/api/mobile/v1/integrations/samsung-food"))
        return try JSONDecoder().decode(SamsungIntegrationStatus.self, from: data)
    }

    func disconnectSamsungIntegration() async throws -> SamsungDisconnectResponse {
        let data = try await perform(request(
            path: "/api/mobile/v1/integrations/samsung-food/disconnect",
            method: "POST",
            body: Data("{}".utf8)
        ))
        return try JSONDecoder().decode(SamsungDisconnectResponse.self, from: data)
    }

    func startSamsungLogin() async throws -> SamsungLoginStartResponse {
        let data = try await perform(request(
            path: "/api/mobile/v1/integrations/samsung-food/login/start",
            method: "POST",
            body: Data("{}".utf8)
        ))
        return try JSONDecoder().decode(SamsungLoginStartResponse.self, from: data)
    }

    func fetchSamsungLoginStatus(sessionID: String) async throws -> SamsungLoginStatus {
        let data = try await perform(request(path: "/api/mobile/v1/integrations/samsung-food/login/\(sessionID)"))
        return try JSONDecoder().decode(SamsungLoginStatus.self, from: data)
    }

    func selectSamsungList(sessionID: String, listID: String) async throws {
        let body = try JSONSerialization.data(withJSONObject: ["session_id": sessionID, "list_id": listID])
        _ = try await perform(request(
            path: "/api/mobile/v1/integrations/samsung-food/login/select-list",
            method: "POST",
            body: body
        ))
    }

    private func performPublic(path: String, body: Data) async throws -> Data {
        let url = baseURL.appending(path: path)
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 20
        request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, response) = try await URLSession.shared.data(for: request)
        try validate(response: response, data: data)
        return data
    }
}
