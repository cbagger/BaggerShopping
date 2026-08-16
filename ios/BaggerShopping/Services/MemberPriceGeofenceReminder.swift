import Foundation

enum MemberPriceGeofenceReminder {
    static func message(
        retailer: String,
        storeItems: [ShoppingItem],
        metadata: [OfferMetadataDTO],
        now: Date = Date()
    ) -> String? {
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
