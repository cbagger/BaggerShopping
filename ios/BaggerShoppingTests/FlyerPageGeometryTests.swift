import XCTest
@testable import BaggerShopping

final class FlyerPageGeometryTests: XCTestCase {
    func testOffersStayOnThePrintedProductOnNarrowFlyerPages() {
        let viewport = CGSize(width: 390, height: 650)
        let frame = FlyerPageGeometry.fittedImage(CGSize(width: 600, height: 1047), in: viewport)
        XCTAssertEqual(frame.width, 372.493, accuracy: 0.001)
        XCTAssertEqual(frame.height, 650, accuracy: 0.001)
        XCTAssertEqual(frame.midX, viewport.width / 2, accuracy: 0.001)

        let marker = FlyerPageGeometry.markerCenter(in: frame, x: 0.50, y: 0.60, width: 0.30, height: 0.20)
        XCTAssertEqual(marker.x, 250.874, accuracy: 0.001)
        XCTAssertEqual(marker.y, 455, accuracy: 0.001)
    }

    func testWidePagesUseTheirOwnHeightToPositionMarkers() {
        let viewport = CGSize(width: 390, height: 650)
        let frame = FlyerPageGeometry.fittedImage(CGSize(width: 600, height: 846), in: viewport)
        XCTAssertEqual(frame.width, 390, accuracy: 0.001)
        XCTAssertEqual(frame.height, 549.9, accuracy: 0.001)
        XCTAssertEqual(frame.minY, 50.05, accuracy: 0.001)

        let marker = FlyerPageGeometry.markerCenter(in: frame, x: 0.10, y: 0.10, width: 0.40, height: 0.20)
        XCTAssertEqual(marker.x, 117, accuracy: 0.001)
        XCTAssertEqual(marker.y, 160.03, accuracy: 0.001)
    }
}
