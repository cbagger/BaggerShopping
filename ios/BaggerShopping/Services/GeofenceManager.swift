import Foundation
import CoreLocation
import UserNotifications
import UIKit

struct GeofenceRegionDiagnostic: Identifiable, Hashable {
    let id: String
    let name: String
    let latitude: Double
    let longitude: Double
    let radius: Double
    var state: String
    var distanceMeters: Double?
}

@MainActor
final class GeofenceManager: NSObject, ObservableObject, CLLocationManagerDelegate, UNUserNotificationCenterDelegate {
    @Published private(set) var authorizationStatus: CLAuthorizationStatus = .notDetermined
    @Published private(set) var notificationAuthorizationText = "Ukendt"
    @Published private(set) var monitoredCount = 0
    @Published private(set) var lastMonitoringError: String?
    @Published private(set) var lastEnterEvent: String?
    @Published private(set) var lastExitEvent: String?
    @Published private(set) var lastStateCheck: String?
    @Published private(set) var currentLocationText: String?
    @Published private(set) var regionDiagnostics: [GeofenceRegionDiagnostic] = []
    @Published private(set) var lastNotificationAttempt: String?
    @Published private(set) var lastNotificationResult: String?
    @Published private(set) var lastListFetchResult: String?

    private let manager = CLLocationManager()
    private let api = APIClient()
    private let cooldownSeconds: TimeInterval = 2 * 60 * 60
    private let metadataCacheKey = "geofence-offer-metadata-cache-v1"
    private var storeNamesByIdentifier: [String: String] = [:]
    private var latestLocation: CLLocation?

    override init() {
        super.init()
        manager.delegate = self
        UNUserNotificationCenter.current().delegate = self

        authorizationStatus = manager.authorizationStatus
        monitoredCount = manager.monitoredRegions.count

        lastEnterEvent = UserDefaults.standard.string(forKey: "geofence-last-enter-event")
        lastExitEvent = UserDefaults.standard.string(forKey: "geofence-last-exit-event")
        lastNotificationAttempt = UserDefaults.standard.string(forKey: "geofence-last-notification-attempt")
        lastNotificationResult = UserDefaults.standard.string(forKey: "geofence-last-notification-result")
        lastListFetchResult = UserDefaults.standard.string(forKey: "geofence-last-list-fetch-result")

        refreshRegionDiagnostics()
        Task { await refreshNotificationAuthorization() }
    }

    func requestPermissions() async {
        _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])
        await refreshNotificationAuthorization()
        manager.requestAlwaysAuthorization()
    }

    func refreshNotificationAuthorization() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        notificationAuthorizationText = Self.notificationStatusText(settings.authorizationStatus)
    }

    func sync(stores: [StoreLocation]) {
        storeNamesByIdentifier = Dictionary(
            uniqueKeysWithValues: stores.map { ("store:\($0.id.uuidString)", $0.name) }
        )

        for region in manager.monitoredRegions where region.identifier.hasPrefix("store:") {
            manager.stopMonitoring(for: region)
        }

        for store in stores.filter(\.enabled).prefix(20) {
            let radius = min(max(store.radius, 100), manager.maximumRegionMonitoringDistance)
            let region = CLCircularRegion(
                center: store.coordinate,
                radius: radius,
                identifier: "store:\(store.id.uuidString)"
            )
            region.notifyOnEntry = true
            region.notifyOnExit = true
            manager.startMonitoring(for: region)
        }

        refreshRegionDiagnostics()
    }

    func runDiagnostics() async {
        await refreshNotificationAuthorization()

        guard CLLocationManager.isMonitoringAvailable(for: CLCircularRegion.self) else {
            lastMonitoringError = "Region monitoring er ikke tilgængelig på denne enhed."
            return
        }

        lastStateCheck = "Kontrol startet \(Self.timestamp())"

        if authorizationStatus == .authorizedAlways || authorizationStatus == .authorizedWhenInUse {
            manager.requestLocation()
        }

        for region in manager.monitoredRegions where region.identifier.hasPrefix("store:") {
            manager.requestState(for: region)
        }

        refreshRegionDiagnostics()
    }

    func sendSimpleTestNotification() async throws {
        let content = UNMutableNotificationContent()
        content.title = "Kurv test"
        content.body = "Lokale notifikationer virker på denne iPhone."
        content.sound = .default

        try await UNUserNotificationCenter.current().add(
            UNNotificationRequest(
                identifier: "bagger-shopping-test-\(UUID().uuidString)",
                content: content,
                trigger: nil
            )
        )
    }

    func sendShoppingListTestNotification() async throws {
        let list = try await api.fetchList()
        let metadata = try await api.fetchOfferMetadata().metadata
        saveMetadataCache(metadata)
        ShoppingListCache.save(list)
        let retailer = storeNamesByIdentifier.values.sorted().first ?? "valgte butik"
        let items = Self.items(for: retailer, in: list, metadata: metadata)
        try await scheduleShoppingNotification(
            items: items,
            retailer: retailer,
            cached: false
        )
    }

    func resetCooldowns() {
        let prefix = "geofence-last-notification-store:"
        for (key, _) in UserDefaults.standard.dictionaryRepresentation() where key.hasPrefix(prefix) {
            UserDefaults.standard.removeObject(forKey: key)
        }

        lastNotificationResult = "Cooldown nulstillet \(Self.timestamp())"
        UserDefaults.standard.set(lastNotificationResult, forKey: "geofence-last-notification-result")
    }

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        Task { @MainActor in
            self.authorizationStatus = manager.authorizationStatus
        }
    }

    nonisolated func locationManager(
        _ manager: CLLocationManager,
        didStartMonitoringFor region: CLRegion
    ) {
        Task { @MainActor in
            self.refreshRegionDiagnostics()
        }
    }

    nonisolated func locationManager(
        _ manager: CLLocationManager,
        monitoringDidFailFor region: CLRegion?,
        withError error: Error
    ) {
        Task { @MainActor in
            let regionName = region.map { self.displayName(for: $0.identifier) } ?? "ukendt region"
            self.lastMonitoringError = "\(regionName): \(error.localizedDescription)"
            self.refreshRegionDiagnostics()
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didEnterRegion region: CLRegion) {
        guard region.identifier.hasPrefix("store:") else { return }

        Task { @MainActor in
            let message = "\(self.displayName(for: region.identifier)) – \(Self.timestamp())"
            self.lastEnterEvent = message
            UserDefaults.standard.set(message, forKey: "geofence-last-enter-event")
            self.setRegionState(identifier: region.identifier, state: "INSIDE")
            await self.handleStoreEntry(regionIdentifier: region.identifier)
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didExitRegion region: CLRegion) {
        guard region.identifier.hasPrefix("store:") else { return }

        Task { @MainActor in
            let message = "\(self.displayName(for: region.identifier)) – \(Self.timestamp())"
            self.lastExitEvent = message
            UserDefaults.standard.set(message, forKey: "geofence-last-exit-event")
            self.setRegionState(identifier: region.identifier, state: "OUTSIDE")
        }
    }

    nonisolated func locationManager(
        _ manager: CLLocationManager,
        didDetermineState state: CLRegionState,
        for region: CLRegion
    ) {
        guard region.identifier.hasPrefix("store:") else { return }

        let stateText: String
        switch state {
        case .inside: stateText = "INSIDE"
        case .outside: stateText = "OUTSIDE"
        case .unknown: stateText = "UNKNOWN"
        @unknown default: stateText = "UNKNOWN"
        }

        Task { @MainActor in
            self.setRegionState(identifier: region.identifier, state: stateText)
            self.lastStateCheck = "Senest opdateret \(Self.timestamp())"
        }
    }

    nonisolated func locationManager(
        _ manager: CLLocationManager,
        didUpdateLocations locations: [CLLocation]
    ) {
        guard let location = locations.last else { return }

        Task { @MainActor in
            self.latestLocation = location
            self.currentLocationText = String(
                format: "%.6f, %.6f (±%.0f m)",
                location.coordinate.latitude,
                location.coordinate.longitude,
                max(location.horizontalAccuracy, 0)
            )
            self.refreshRegionDiagnostics()
        }
    }

    nonisolated func locationManager(
        _ manager: CLLocationManager,
        didFailWithError error: Error
    ) {
        Task { @MainActor in
            self.lastMonitoringError = "Placering: \(error.localizedDescription)"
        }
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .list, .sound]
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        let userInfo = response.notification.request.content.userInfo
        guard userInfo["route"] as? String == "shopping-list",
              let retailer = userInfo["retailer"] as? String else { return }
        NotificationCenter.default.post(name: .openShoppingListRetailer, object: retailer)
    }

    private func handleStoreEntry(regionIdentifier: String) async {
        let storeName = displayName(for: regionIdentifier)
        let attempt = "\(storeName) – \(Self.timestamp())"
        lastNotificationAttempt = attempt
        UserDefaults.standard.set(attempt, forKey: "geofence-last-notification-attempt")

        let cooldownKey = "geofence-last-notification-\(regionIdentifier)"
        let last = UserDefaults.standard.double(forKey: cooldownKey)

        if last > 0, Date().timeIntervalSince1970 - last < cooldownSeconds {
            let result = "SPRING OVER: cooldown aktiv – \(Self.timestamp())"
            lastNotificationResult = result
            UserDefaults.standard.set(result, forKey: "geofence-last-notification-result")
            return
        }

        var backgroundTask = UIBackgroundTaskIdentifier.invalid
        backgroundTask = UIApplication.shared.beginBackgroundTask(
            withName: "BaggerShopping-Geofence-\(storeName)"
        ) {
            if backgroundTask != .invalid {
                UIApplication.shared.endBackgroundTask(backgroundTask)
                backgroundTask = .invalid
            }
        }

        defer {
            if backgroundTask != .invalid {
                UIApplication.shared.endBackgroundTask(backgroundTask)
                backgroundTask = .invalid
            }
        }

        do {
            let list = try await api.fetchList()
            let metadata = try await api.fetchOfferMetadata().metadata
            saveMetadataCache(metadata)
            ShoppingListCache.save(list)

            let fetchResult = "LIVE OK: \(list.items.filter { !$0.checked }.count) varer – \(Self.timestamp())"
            lastListFetchResult = fetchResult
            UserDefaults.standard.set(fetchResult, forKey: "geofence-last-list-fetch-result")

            let remaining = Self.items(for: storeName, in: list, metadata: metadata)
            guard !remaining.isEmpty else {
                let result = "INGEN NOTIFIKATION: ingen varer til \(storeName) – \(Self.timestamp())"
                lastNotificationResult = result
                UserDefaults.standard.set(result, forKey: "geofence-last-notification-result")
                return
            }

            try await scheduleShoppingNotification(
                items: remaining,
                retailer: storeName,
                cached: false
            )

            UserDefaults.standard.set(Date().timeIntervalSince1970, forKey: cooldownKey)

            let result = "SENDT med LIVE liste – \(Self.timestamp())"
            lastNotificationResult = result
            UserDefaults.standard.set(result, forKey: "geofence-last-notification-result")
        } catch {
            let fetchResult = "LIVE FEJL: \(error.localizedDescription) – \(Self.timestamp())"
            lastListFetchResult = fetchResult
            UserDefaults.standard.set(fetchResult, forKey: "geofence-last-list-fetch-result")

            if let cached = ShoppingListCache.load(), let metadata = loadMetadataCache() {
                do {
                    let remaining = Self.items(for: storeName, in: cached.list, metadata: metadata)
                    guard !remaining.isEmpty else {
                        let result = "INGEN NOTIFIKATION: cache har ingen varer til \(storeName) – \(Self.timestamp())"
                        lastNotificationResult = result
                        UserDefaults.standard.set(result, forKey: "geofence-last-notification-result")
                        return
                    }

                    try await scheduleShoppingNotification(
                        items: remaining,
                        retailer: storeName,
                        cached: true
                    )

                    UserDefaults.standard.set(Date().timeIntervalSince1970, forKey: cooldownKey)

                    let result = "SENDT med CACHE efter live-fejl – \(Self.timestamp())"
                    lastNotificationResult = result
                    UserDefaults.standard.set(result, forKey: "geofence-last-notification-result")
                    return
                } catch {
                    let result = "NOTIFIKATIONSFEJL: \(error.localizedDescription) – \(Self.timestamp())"
                    lastNotificationResult = result
                    UserDefaults.standard.set(result, forKey: "geofence-last-notification-result")
                }
            }

            lastMonitoringError = "Geofence-notifikation: \(error.localizedDescription)"
        }
    }

    private func scheduleShoppingNotification(
        items remaining: [ShoppingItem],
        retailer: String,
        cached: Bool
    ) async throws {
        let content = UNMutableNotificationContent()
        content.title = "Du er ved \(retailer)"

        if remaining.isEmpty {
            content.body = "Din indkøbsliste er tom."
        } else {
            let categorySummary = Self.categorySummary(for: remaining)
            let cacheSuffix = cached ? " · senest synkroniserede liste" : ""
            let noun = remaining.count == 1 ? "vare" : "varer"
            content.body = "Du har \(remaining.count) \(noun) her · \(categorySummary)\(cacheSuffix)"
        }

        content.sound = .default
        content.userInfo = ["route": "shopping-list", "retailer": retailer]

        try await UNUserNotificationCenter.current().add(
            UNNotificationRequest(
                identifier: "shopping-\(UUID().uuidString)",
                content: content,
                trigger: nil
            )
        )
    }

    nonisolated static func items(
        for retailer: String,
        in list: ShoppingListResponse,
        metadata: [OfferMetadataDTO]
    ) -> [ShoppingItem] {
        let retailerKey = normalizedKey(retailer)
        let retailerByItem = metadata.reduce(into: [String: String]()) { result, record in
            result[normalizedKey(record.itemName)] = normalizedKey(record.retailer)
        }
        return list.items.filter { item in
            !item.checked && retailerByItem[normalizedKey(item.name)] == retailerKey
        }
    }

    nonisolated private static func normalizedKey(_ value: String) -> String {
        value
            .folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "da_DK"))
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
    }

    private func saveMetadataCache(_ metadata: [OfferMetadataDTO]) {
        guard let data = try? JSONEncoder().encode(metadata) else { return }
        UserDefaults.standard.set(data, forKey: metadataCacheKey)
    }

    private func loadMetadataCache() -> [OfferMetadataDTO]? {
        guard let data = UserDefaults.standard.data(forKey: metadataCacheKey) else { return nil }
        return try? JSONDecoder().decode([OfferMetadataDTO].self, from: data)
    }

    nonisolated private static func categorySummary(for items: [ShoppingItem]) -> String {
        let grouped = Dictionary(grouping: items) { item in
            ShoppingCategoryService.classify(
                ShoppingCategoryService.normalize(item.name)
            )
        }

        let parts = grouped
            .map { category, items in (category, items.count) }
            .sorted {
                if $0.1 != $1.1 { return $0.1 > $1.1 }
                return $0.0.sortOrder < $1.0.sortOrder
            }
            .prefix(4)
            .map { category, count in "\(count) \(category.rawValue)" }

        let covered = grouped
            .map { $0.value.count }
            .sorted(by: >)
            .prefix(4)
            .reduce(0, +)
        let remainder = max(items.count - covered, 0)
        let suffix = remainder > 0 ? " + \(remainder) øvrige" : ""

        return parts.joined(separator: ", ") + suffix
    }

    private func refreshRegionDiagnostics() {
        let monitored = manager.monitoredRegions
            .compactMap { $0 as? CLCircularRegion }
            .filter { $0.identifier.hasPrefix("store:") }
            .sorted { displayName(for: $0.identifier) < displayName(for: $1.identifier) }

        monitoredCount = monitored.count

        let existingStates = Dictionary(
            uniqueKeysWithValues: regionDiagnostics.map { ($0.id, $0.state) }
        )

        regionDiagnostics = monitored.map { region in
            let distance: Double?
            if let latestLocation {
                let centerLocation = CLLocation(
                    latitude: region.center.latitude,
                    longitude: region.center.longitude
                )
                distance = latestLocation.distance(from: centerLocation)
            } else {
                distance = nil
            }

            return GeofenceRegionDiagnostic(
                id: region.identifier,
                name: displayName(for: region.identifier),
                latitude: region.center.latitude,
                longitude: region.center.longitude,
                radius: region.radius,
                state: existingStates[region.identifier] ?? "IKKE KONTROLLERET",
                distanceMeters: distance
            )
        }
    }

    private func setRegionState(identifier: String, state: String) {
        guard let index = regionDiagnostics.firstIndex(where: { $0.id == identifier }) else {
            refreshRegionDiagnostics()
            return
        }

        regionDiagnostics[index].state = state
    }

    private func displayName(for identifier: String) -> String {
        storeNamesByIdentifier[identifier] ?? identifier.replacingOccurrences(of: "store:", with: "")
    }

    private static func notificationStatusText(_ status: UNAuthorizationStatus) -> String {
        switch status {
        case .notDetermined: return "Ikke valgt"
        case .denied: return "Afvist"
        case .authorized: return "Tilladt"
        case .provisional: return "Foreløbig"
        case .ephemeral: return "Midlertidig"
        @unknown default: return "Ukendt"
        }
    }

    private static func timestamp() -> String {
        Date.now.formatted(date: .abbreviated, time: .standard)
    }
}
