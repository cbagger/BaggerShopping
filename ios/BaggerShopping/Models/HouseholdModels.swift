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
