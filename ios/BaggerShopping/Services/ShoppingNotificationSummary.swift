import Foundation

enum ShoppingNotificationSummary {
    nonisolated static func body(for items: [ShoppingItem], cached: Bool) -> String {
        let remaining = items.filter { !$0.checked }

        guard !remaining.isEmpty else {
            return "Din indkøbsliste er tom."
        }

        let grouped = Dictionary(grouping: remaining) { item in
            ShoppingCategoryService.classify(
                ShoppingCategoryService.normalize(item.name)
            )
        }

        let rankedGroups = grouped
            .map { category, items in (category: category, count: items.count) }
            .sorted {
                if $0.count != $1.count { return $0.count > $1.count }
                return $0.category.sortOrder < $1.category.sortOrder
            }

        let visibleGroups = Array(rankedGroups.prefix(4))
        let coveredCount = visibleGroups.reduce(0) { $0 + $1.count }
        let remainder = max(remaining.count - coveredCount, 0)

        let categories = visibleGroups
            .map { "\($0.count) \($0.category.rawValue)" }
            .joined(separator: ", ")

        let remainderSuffix = remainder > 0 ? " + \(remainder) øvrige" : ""
        let cacheSuffix = cached ? " · senest synkroniserede liste" : ""

        return "\(remaining.count) varer · \(categories)\(remainderSuffix)\(cacheSuffix)"
    }
}
