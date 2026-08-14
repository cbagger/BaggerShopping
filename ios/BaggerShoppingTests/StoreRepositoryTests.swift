import XCTest
@testable import BaggerShopping

@MainActor
final class StoreRepositoryTests: XCTestCase {
    private let defaultsKey = "bagger-shopping-stores"

    override func setUp() {
        super.setUp()
        UserDefaults.standard.removeObject(forKey: defaultsKey)
    }

    override func tearDown() {
        UserDefaults.standard.removeObject(forKey: defaultsKey)
        super.tearDown()
    }

    func testRadiusCannotBeSetBelowOneHundredMeters() {
        let repository = StoreRepository()
        let store = StoreLocation(name: "MENY", latitude: 56, longitude: 10)
        repository.add(store)

        repository.setRadius(50, for: store.id)

        XCTAssertEqual(repository.stores.first?.radius, 100)
    }

    func testStoredLegacyRadiusIsMigratedToOneHundredMeters() throws {
        let store = StoreLocation(name: "MENY", latitude: 56, longitude: 10, radius: 50)
        UserDefaults.standard.set(try JSONEncoder().encode([store]), forKey: defaultsKey)

        let repository = StoreRepository()

        XCTAssertEqual(repository.stores.first?.radius, 100)
    }
}
