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

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case householdID = "household_id"
        case householdName = "household_name"
        case memberName = "member_name"
        case role
        case listBackend = "list_backend"
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
