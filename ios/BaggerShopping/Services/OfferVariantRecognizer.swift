import Foundation
import UIKit
import Vision

enum OfferVariantRecognizer {
    static func names(in imageURL: URL, heading: String) async -> [String] {
        guard let (data, _) = try? await URLSession.shared.data(from: imageURL),
              let image = UIImage(data: data), let cgImage = image.cgImage else { return [] }

        return await withCheckedContinuation { continuation in
            let request = VNRecognizeTextRequest { request, _ in
                let observations = (request.results as? [VNRecognizedTextObservation]) ?? []
                let lines = observations.compactMap { $0.topCandidates(1).first?.string }
                continuation.resume(returning: candidates(from: lines, heading: heading))
            }
            request.recognitionLevel = .accurate
            request.recognitionLanguages = ["da-DK", "en-US"]
            request.usesLanguageCorrection = true
            DispatchQueue.global(qos: .userInitiated).async {
                try? VNImageRequestHandler(cgImage: cgImage).perform([request])
            }
        }
    }

    private static func candidates(from lines: [String], heading: String) -> [String] {
        let rejected = ["kg-pris", "literpris", "frit valg", "pr. stk", "pr stk", "spar", "maks."]
        let base = heading.trimmingCharacters(in: CharacterSet(charactersIn: "* "))
        var result: [String] = []

        for raw in lines {
            let value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            let lower = value.lowercased()
            let isOnlyAmount = lower.range(
                of: #"^\d+[,.]?\d*\s*(kr|g|kg|ml|cl|l|stk)?$"#,
                options: .regularExpression
            ) != nil
            guard (2...60).contains(value.count), value.rangeOfCharacter(from: .letters) != nil,
                  !rejected.contains(where: lower.contains),
                  lower != base.lowercased(),
                  !isOnlyAmount
            else { continue }

            let candidate: String
            if lower.hasPrefix("i ") || lower.hasPrefix("med ") || lower.hasPrefix("uden ") {
                candidate = "\(base) \(value.lowercased())"
            } else {
                candidate = value
            }
            if !result.contains(where: { $0.caseInsensitiveCompare(candidate) == .orderedSame }) {
                result.append(candidate)
            }
        }
        return Array(result.prefix(8))
    }
}
