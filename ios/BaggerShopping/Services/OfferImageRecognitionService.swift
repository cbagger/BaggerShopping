import Foundation
import UIKit
import Vision

struct OfferImageTextObservation: Codable, Hashable {
    let text: String
    let confidence: Double
}

struct RecognizedImageVariant: Codable, Identifiable, Hashable {
    let name: String
    let confidence: Double
    let matchLevel: String
    let explanation: String
    let evidence: [String]

    var id: String { name.lowercased() }

    enum CodingKeys: String, CodingKey {
        case name, confidence, explanation, evidence
        case matchLevel = "match_level"
    }
}

struct OfferImageEvidenceResponse: Codable, Hashable {
    let ok: Bool
    let observedText: String
    let variants: [RecognizedImageVariant]
    let confidence: Double
    let requiresConfirmation: Bool

    enum CodingKeys: String, CodingKey {
        case ok, variants, confidence
        case observedText = "observed_text"
        case requiresConfirmation = "requires_confirmation"
    }
}

enum OfferImageGeometry {
    static func validatedHotspot(for offer: GroceryOffer) -> CGRect? {
        guard let x = offer.hotspotX, let y = offer.hotspotY,
              let width = offer.hotspotWidth, let height = offer.hotspotHeight,
              x.isFinite, y.isFinite, width.isFinite, height.isFinite,
              x >= 0, y >= 0, width >= 0.008, height >= 0.008,
              x < 1, y < 1,
              x + width <= 1.015, y + height <= 1.015,
              width * height <= 0.92 else { return nil }
        return CGRect(
            x: x,
            y: y,
            width: min(width, 1 - x),
            height: min(height, 1 - y)
        )
    }
}

actor OfferImageRecognitionService {
    static let shared = OfferImageRecognitionService()

    enum RecognitionError: Error {
        case missingImage
        case invalidImage
        case invalidCrop
    }

    private var cache: [String: OfferImageEvidenceResponse] = [:]

    func recognize(offer: GroceryOffer) async throws -> OfferImageEvidenceResponse {
        let cacheKey = "\(offer.publicationID)|\(offer.id)|\(offer.imageURL?.absoluteString ?? "missing")"
        if let cached = cache[cacheKey] { return cached }
        guard let url = offer.imageURL else { throw RecognitionError.missingImage }
        let (data, response) = try await URLSession.shared.data(from: url)
        guard let http = response as? HTTPURLResponse, 200..<300 ~= http.statusCode,
              let image = UIImage(data: data), let cgImage = image.cgImage else {
            throw RecognitionError.invalidImage
        }
        let input = try croppedImage(from: cgImage, offer: offer)
        let observations = try await recognizeText(in: input)
        guard !observations.isEmpty else {
            let empty = OfferImageEvidenceResponse(
                ok: true, observedText: "", variants: [], confidence: 0,
                requiresConfirmation: true
            )
            cache[cacheKey] = empty
            return empty
        }
        let result = try await APIClient().analyzeOfferImage(
            offer: offer,
            observations: observations
        )
        cache[cacheKey] = result
        return result
    }

    private func croppedImage(from image: CGImage, offer: GroceryOffer) throws -> CGImage {
        // Tjek/Schwarz normally provide a dedicated offer crop. MENY/iPaper
        // supplies a full page, so crop around the authoritative hotspot first.
        if offer.qualitySignals.contains("offer-crop") { return image }
        guard let hotspot = OfferImageGeometry.validatedHotspot(for: offer) else {
            throw RecognitionError.invalidCrop
        }
        let x = hotspot.minX
        let y = hotspot.minY
        let width = hotspot.width
        let height = hotspot.height
        let marginX = max(0.025, width * 0.18)
        let marginY = max(0.025, height * 0.18)
        let normalized = CGRect(
            x: max(0, x - marginX),
            y: max(0, y - marginY),
            width: min(1 - max(0, x - marginX), width + marginX * 2),
            height: min(1 - max(0, y - marginY), height + marginY * 2)
        )
        let pixels = CGRect(
            x: normalized.minX * CGFloat(image.width),
            y: normalized.minY * CGFloat(image.height),
            width: normalized.width * CGFloat(image.width),
            height: normalized.height * CGFloat(image.height)
        ).integral
        guard let cropped = image.cropping(to: pixels) else { throw RecognitionError.invalidCrop }
        return cropped
    }

    private func recognizeText(in image: CGImage) async throws -> [OfferImageTextObservation] {
        try await withCheckedThrowingContinuation { continuation in
            let request = VNRecognizeTextRequest { request, error in
                if let error {
                    continuation.resume(throwing: error)
                    return
                }
                let values = (request.results as? [VNRecognizedTextObservation] ?? [])
                    .compactMap { observation -> (CGRect, OfferImageTextObservation)? in
                        guard let candidate = observation.topCandidates(1).first,
                              candidate.confidence >= 0.35 else { return nil }
                        return (
                            observation.boundingBox,
                            OfferImageTextObservation(
                                text: candidate.string,
                                confidence: Double(candidate.confidence)
                            )
                        )
                    }
                    .sorted {
                        if abs($0.0.midY - $1.0.midY) > 0.025 { return $0.0.midY > $1.0.midY }
                        return $0.0.minX < $1.0.minX
                    }
                    .map { $0.1 }
                continuation.resume(returning: values)
            }
            request.recognitionLevel = .accurate
            request.recognitionLanguages = ["da-DK", "en-US"]
            request.usesLanguageCorrection = true
            request.minimumTextHeight = 0.012
            do {
                try VNImageRequestHandler(cgImage: image, orientation: .up).perform([request])
            } catch {
                continuation.resume(throwing: error)
            }
        }
    }
}
