import Foundation
import UIKit
import UserNotifications

extension Notification.Name {
    static let didRegisterForRemoteNotifications = Notification.Name("kurv.didRegisterForRemoteNotifications")
    static let didFailRemoteNotificationRegistration = Notification.Name("kurv.didFailRemoteNotificationRegistration")
}

final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        NotificationCenter.default.post(name: .didRegisterForRemoteNotifications, object: token)
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        NotificationCenter.default.post(name: .didFailRemoteNotificationRegistration, object: error)
    }
}

@MainActor
final class FlyerPushManager: ObservableObject {
    @Published private(set) var authorizationText = "Ikke valgt"
    @Published private(set) var availableRetailers: [String] = []
    @Published var selectedRetailers: Set<String> {
        didSet { savePreferences() }
    }
    @Published var enabled: Bool {
        didSet { savePreferences() }
    }
    @Published private(set) var isRegistered = false
    @Published private(set) var errorMessage: String?

    private let api = APIClient()
    private let deviceIDKey = "kurv-flyer-push-device-id-v1"
    private let tokenKey = "kurv-flyer-push-token-v1"
    private let retailersKey = "kurv-flyer-push-retailers-v1"
    private let enabledKey = "kurv-flyer-push-enabled-v1"

    init() {
        selectedRetailers = Set(UserDefaults.standard.stringArray(forKey: retailersKey) ?? [])
        enabled = UserDefaults.standard.object(forKey: enabledKey) as? Bool ?? false
        NotificationCenter.default.addObserver(
            forName: .didRegisterForRemoteNotifications, object: nil, queue: .main
        ) { [weak self] note in
            guard let token = note.object as? String else { return }
            Task { @MainActor in await self?.received(token: token) }
        }
        NotificationCenter.default.addObserver(
            forName: .didFailRemoteNotificationRegistration, object: nil, queue: .main
        ) { [weak self] note in
            Task { @MainActor in self?.errorMessage = (note.object as? Error)?.localizedDescription }
        }
    }

    func bootstrap() async {
        await refreshAuthorization()
        if let response = try? await api.fetchFlyerNotificationRetailers() {
            availableRetailers = response.retailers
        }
        if enabled { UIApplication.shared.registerForRemoteNotifications() }
    }

    func requestAndEnable() async {
        do {
            let granted = try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])
            await refreshAuthorization()
            guard granted else { return }
            enabled = true
            if selectedRetailers.isEmpty { selectedRetailers = Set(availableRetailers) }
            UIApplication.shared.registerForRemoteNotifications()
        } catch { errorMessage = error.localizedDescription }
    }

    func updateRetailer(_ retailer: String, selected: Bool) async {
        if selected { selectedRetailers.insert(retailer) } else { selectedRetailers.remove(retailer) }
        await sync()
    }

    func setEnabled(_ value: Bool) async {
        if value {
            await requestAndEnable()
        } else {
            enabled = false
            if let token = UserDefaults.standard.string(forKey: tokenKey) {
                await sync(token: token)
            }
        }
    }

    private func received(token: String) async {
        UserDefaults.standard.set(token, forKey: tokenKey)
        isRegistered = true
        await sync(token: token)
    }

    private func sync(token: String? = nil) async {
        guard let token = token ?? UserDefaults.standard.string(forKey: tokenKey) else { return }
        do {
            try await api.setFlyerNotificationDevice(
                deviceID: deviceID,
                deviceToken: token,
                retailers: Array(selectedRetailers),
                enabled: enabled
            )
            errorMessage = nil
            isRegistered = true
        } catch { errorMessage = error.localizedDescription }
    }

    private func refreshAuthorization() async {
        let status = await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
        authorizationText = switch status {
        case .authorized, .provisional, .ephemeral: "Tilladt"
        case .denied: "Afvist"
        case .notDetermined: "Ikke valgt"
        @unknown default: "Ukendt"
        }
    }

    private var deviceID: String {
        if let stored = UserDefaults.standard.string(forKey: deviceIDKey) { return stored }
        let value = UUID().uuidString
        UserDefaults.standard.set(value, forKey: deviceIDKey)
        return value
    }

    private func savePreferences() {
        UserDefaults.standard.set(Array(selectedRetailers), forKey: retailersKey)
        UserDefaults.standard.set(enabled, forKey: enabledKey)
    }
}
