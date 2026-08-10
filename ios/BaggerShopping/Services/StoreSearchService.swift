import Foundation
import MapKit

@MainActor
final class StoreSearchService: ObservableObject {
    @Published var results: [StoreSearchResult] = []
    @Published var isSearching = false
    @Published var errorMessage: String?

    func search(_ query: String) async {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            results = []
            return
        }

        isSearching = true
        defer { isSearching = false }

        let request = MKLocalSearch.Request()
        request.naturalLanguageQuery = trimmed
        request.resultTypes = [.pointOfInterest, .address]

        do {
            let response = try await MKLocalSearch(request: request).start()
            results = response.mapItems.prefix(12).map { item in
                let placemark = item.placemark
                return StoreSearchResult(
                    name: item.name ?? placemark.name ?? trimmed,
                    address: placemark.title ?? "",
                    latitude: placemark.coordinate.latitude,
                    longitude: placemark.coordinate.longitude
                )
            }
            errorMessage = results.isEmpty ? "Ingen steder fundet." : nil
        } catch {
            results = []
            errorMessage = error.localizedDescription
        }
    }
}
