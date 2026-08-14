import Foundation

struct HouseholdProfile: Codable {
    let householdID: String
    let householdName: String
    let memberName: String
    let role: String
    let listBackend: String

    enum CodingKeys: String, CodingKey {
        case householdID = "household_id"
        case householdName = "household_name"
        case memberName = "member_name"
        case role
        case listBackend = "list_backend"
    }
}

struct HouseholdAuthResponse: Codable {
    let accessToken: String
    let householdID: String
    let householdName: String
    let memberName: String
    let role: String
    let listBackend: String
    let recoveryCode: String?

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case householdID = "household_id"
        case householdName = "household_name"
        case memberName = "member_name"
        case role
        case listBackend = "list_backend"
        case recoveryCode = "recovery_code"
    }
}

struct HouseholdRecoveryStatus: Codable {
    let configured: Bool
}

struct HouseholdRecoveryCodeResponse: Codable {
    let recoveryCode: String

    enum CodingKeys: String, CodingKey {
        case recoveryCode = "recovery_code"
    }
}

struct HouseholdInviteResponse: Codable {
    let inviteCode: String
    let expiresInDays: Int

    enum CodingKeys: String, CodingKey {
        case inviteCode = "invite_code"
        case expiresInDays = "expires_in_days"
    }
}

struct HouseholdMember: Codable, Identifiable {
    let id: String
    var name: String
    let role: String
}

struct HouseholdMembersResponse: Codable {
    let members: [HouseholdMember]
}

struct SamsungIntegrationStatus: Codable {
    let provider: String
    let status: String
    let listName: String?
    let listID: String?
    let lastSuccessfulSync: Int?
    let errorMessage: String?
    let canManage: Bool
    let selfServiceLoginAvailable: Bool

    enum CodingKeys: String, CodingKey {
        case provider, status
        case listName = "list_name"
        case listID = "list_id"
        case lastSuccessfulSync = "last_successful_sync"
        case errorMessage = "error_message"
        case canManage = "can_manage"
        case selfServiceLoginAvailable = "self_service_login_available"
    }
}

struct SamsungDisconnectResponse: Codable {
    let preservedListName: String
    let preservedItemCount: Int

    enum CodingKeys: String, CodingKey {
        case preservedListName = "preserved_list_name"
        case preservedItemCount = "preserved_item_count"
    }
}

struct SamsungLoginStartResponse: Codable {
    let sessionID: String
    let loginURL: URL
    let expiresAt: Int

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case loginURL = "login_url"
        case expiresAt = "expires_at"
    }
}

struct SamsungListChoice: Codable, Identifiable, Hashable {
    let id: String
    let name: String
}

struct SamsungLoginStatus: Codable {
    let sessionID: String
    let status: String
    let expiresAt: Int
    let lists: [SamsungListChoice]

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case status
        case expiresAt = "expires_at"
        case lists
    }
}
