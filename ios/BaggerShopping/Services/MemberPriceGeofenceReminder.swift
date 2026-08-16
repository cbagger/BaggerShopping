import Foundation

/// Conservative persisted geofence presence used by the shopping-list member
/// reminder. Unknown/stale state is treated as OUTSIDE: Kurv must never tell the
/// user to activate a store app merely because items are sorted under a store.
enum MemberPricePresence {
    private static let keyName = "member-price-inside-stores-v1"

    static func clear() {
        UserDefaults.standard.removeObject(forKey: keyName)
    }

    static func setInside(_ inside: Bool, storeName: String) {
        let trimmed = storeName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        var stores = Set(UserDefaults.standard.stringArray(forKey: keyName) ?? [])
        if inside {
            stores.insert(trimmed)
        } else {
            stores = Set(stores.filter { normalized($0) != normalized(trimmed) })
        }
        UserDefaults.standard.set(Array(stores).sorted(), forKey: keyName)
    }

    static func isInside(retailer: String) -> Bool {
        let retailerKey = normalized(retailer)
        guard !retailerKey.isEmpty else { return false }
        return (UserDefaults.standard.stringArray(forKey: keyName) ?? []).contains { storeName in
            let storeKey = normalized(storeName)
            return storeKey == retailerKey
                || storeKey.hasPrefix(retailerKey + " ")
                || storeKey.hasPrefix(retailerKey + "-")
        }
    }

    private static func normalized(_ value: String) -> String {
        value
            .folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "da_DK"))
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
    }
}

/// Builds the activation reminder shown inside the shopping list under a store.
/// This is deliberately presentation logic for the in-app shopping experience,
/// not notification copy.
enum MemberPriceReminder {
    static func message(
        retailer: String,
        storeItems: [ShoppingItem],
        metadata: [OfferMetadataDTO],
        now: Date = Date()
    ) -> String? {
        guard MemberPricePresence.isInside(retailer: retailer) else { return nil }

        let activeItemKeys = Set(storeItems.filter { !$0.checked }.map { key($0.name) })
        guard !activeItemKeys.isEmpty else { return nil }

        let retailerKey = key(retailer)
        let records = metadata.filter { record in
            key(record.retailer) == retailerKey
                && activeItemKeys.contains(key(record.itemName))
                && isActive(record, now: now)
                && record.offerSnapshot?.memberPrice != nil
                && record.offerSnapshot?.memberPriceRequiresActivation == true
        }
        guard !records.isEmpty else { return nil }

        let apps = Set(records.compactMap { record -> String? in
            let value = record.offerSnapshot?.memberPriceApp?.trimmingCharacters(in: .whitespacesAndNewlines)
            return value?.isEmpty == false ? value : nil
        })
        let labels = Set(records.compactMap { record -> String? in
            let value = record.offerSnapshot?.memberPriceDisplayLabel.trimmingCharacters(in: .whitespacesAndNewlines)
            return value?.isEmpty == false ? value : nil
        })

        if records.count == 1 {
            if let app = apps.first {
                return "Husk at aktivere tilbuddet i \(app)."
            }
            return "Husk at aktivere medlemsprisen i butikkens app."
        }

        if apps.count == 1, let app = apps.first {
            let priceName: String
            if labels.count == 1, let label = labels.first {
                priceName = label.localizedCaseInsensitiveContains("pris") ? label : "\(label)-pris"
            } else {
                priceName = "medlemspris"
            }
            return "Du har \(records.count) varer med \(priceName) – husk at aktivere tilbuddene i \(app)."
        }

        return "Du har \(records.count) varer med medlemspris – husk at aktivere tilbuddene i butikkens app."
    }

    private static func isActive(_ record: OfferMetadataDTO, now: Date) -> Bool {
        let calendar = Calendar(identifier: .gregorian)
        let today = calendar.startOfDay(for: now)
        if let start = parse(record.validFrom), calendar.startOfDay(for: start) > today {
            return false
        }
        if let end = parse(record.validUntil), calendar.startOfDay(for: end) < today {
            return false
        }
        return true
    }

    private static func parse(_ value: String?) -> Date? {
        guard let value else { return nil }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "da_DK")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.dateFormat = "dd.MM.yyyy"
        return formatter.date(from: value)
    }

    private static func key(_ value: String) -> String {
        value
            .folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "da_DK"))
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
    }
}

/// Kept as a compatibility shim for the existing geofence call site. Member
/// price activation reminders must never be appended to arrival notifications;
/// they are shown under the retailer heading only after confirmed geofence entry.
enum MemberPriceGeofenceReminder {
    static func message(
        retailer: String,
        storeItems: [ShoppingItem],
        metadata: [OfferMetadataDTO],
        now: Date = Date()
    ) -> String? {
        _ = retailer
        _ = storeItems
        _ = metadata
        _ = now
        return nil
    }
}
