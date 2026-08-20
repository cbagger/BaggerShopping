import XCTest
@testable import BaggerShopping

final class RetailerCatalogTests: XCTestCase {
    func testAllSupportedRetailersAreAvailableToShoppingUI() {
        XCTAssertEqual(RetailerCatalog.all, [
            "MENY",
            "365discount",
            "REMA 1000",
            "Bilka",
            "føtex",
            "Lidl",
            "Netto",
            "SPAR",
            "SuperBrugsen",
            "Kvickly",
            "Brugsen",
            "Min Købmand",
            "LET-KØB"
        ])
    }
}
