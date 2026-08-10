import XCTest
@testable import BaggerShopping

final class ShoppingCategoryServiceTests: XCTestCase {
    func testCommonDanishItems() {
        XCTAssertEqual(ShoppingCategoryService.classify(ShoppingCategoryService.normalize("Mælk")), .dairy)
        XCTAssertEqual(ShoppingCategoryService.classify(ShoppingCategoryService.normalize("Bananer")), .fruitAndVegetables)
        XCTAssertEqual(ShoppingCategoryService.classify(ShoppingCategoryService.normalize("Toiletpapir")), .household)
        XCTAssertEqual(ShoppingCategoryService.classify(ShoppingCategoryService.normalize("Hamburgerryg")), .deli)
        XCTAssertEqual(ShoppingCategoryService.classify(ShoppingCategoryService.normalize("Hakket oksekød")), .meat)
        XCTAssertEqual(ShoppingCategoryService.classify(ShoppingCategoryService.normalize("Rugbrød")), .bakery)
        XCTAssertEqual(ShoppingCategoryService.classify(ShoppingCategoryService.normalize("Pesto")), .pantry)
        XCTAssertEqual(ShoppingCategoryService.classify(ShoppingCategoryService.normalize("Shampoo")), .personalCare)
    }

    func testUnknownItemFallsBackToOther() {
        XCTAssertEqual(
            ShoppingCategoryService.classify(ShoppingCategoryService.normalize("ChatGPT test vare")),
            .other
        )
    }

    @MainActor
    func testManualOverrideWinsAndPersistsWithinService() {
        let service = ShoppingCategoryService()
        let name = "Testprodukt-\(UUID().uuidString)"

        XCTAssertEqual(service.category(for: name), .other)
        service.setCategory(.dairy, for: name)
        XCTAssertEqual(service.category(for: name), .dairy)

        service.removeOverride(for: name)
        XCTAssertEqual(service.category(for: name), .other)
    }
}
