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

    func testPluralAndCompoundNames() {
        XCTAssertEqual(ShoppingCategoryService.classify(ShoppingCategoryService.normalize("Æbler")), .fruitAndVegetables)
        XCTAssertEqual(ShoppingCategoryService.classify(ShoppingCategoryService.normalize("Bananer øko")), .fruitAndVegetables)
        XCTAssertEqual(ShoppingCategoryService.classify(ShoppingCategoryService.normalize("Letmælk")), .dairy)
        XCTAssertEqual(ShoppingCategoryService.classify(ShoppingCategoryService.normalize("Minimælk 1 liter")), .dairy)
        XCTAssertEqual(ShoppingCategoryService.classify(ShoppingCategoryService.normalize("Kartofler")), .fruitAndVegetables)
    }

    func testShortTermsDoNotCreateFalsePositives() {
        XCTAssertEqual(ShoppingCategoryService.classify(ShoppingCategoryService.normalize("Ris")), .pantry)
        XCTAssertEqual(ShoppingCategoryService.classify(ShoppingCategoryService.normalize("Frisk pasta")), .pantry)
        XCTAssertEqual(ShoppingCategoryService.classify(ShoppingCategoryService.normalize("Mystisk vare")), .other)
    }

    func testCleaningSpongeDoesNotBecomeProduce() {
        XCTAssertEqual(
            ShoppingCategoryService.classify(ShoppingCategoryService.normalize("Opvaskesvamp")),
            .household
        )
    }

    func testUnknownItemFallsBackToOther() {
        XCTAssertEqual(
            ShoppingCategoryService.classify(ShoppingCategoryService.normalize("ChatGPT test vare")),
            .other
        )
    }

    @MainActor
    func testManualOverrideWinsAndCanBeRemoved() {
        let service = ShoppingCategoryService()
        let name = "Testprodukt-\(UUID().uuidString)"

        XCTAssertEqual(service.category(for: name), .other)
        service.setCategory(.dairy, for: name)
        XCTAssertEqual(service.category(for: name), .dairy)

        service.removeOverride(for: name)
        XCTAssertEqual(service.category(for: name), .other)
    }
}
