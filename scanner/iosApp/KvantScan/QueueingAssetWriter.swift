import ARKit
import CoreImage
import Foundation
import shared

/// Двойник Android `QueueingAssetWriter.kt`: кадры на диск + запись в
/// общую очередь медиа (MediaQueue из KMP-ядра).
///
/// Прямой загрузки нет намеренно — съёмка не ждёт сеть (§04.1): JPEG
/// ложится в Application Support, запись встаёт в очередь, загрузку двигает
/// фоновый воркер (MediaWorker поверх BGTaskScheduler). Файл принадлежит
/// очереди до подтверждения сервера — удаление только через
/// deletablePaths() (инвариант I-1).
final class QueueingAssetWriter {

    private let queue: MediaQueue
    private let sessionId: String
    /// Текущий шаг задаёт хост-экран: один writer живёт всю сессию.
    private let currentStepId: () -> String
    private let storageDir: URL
    private let jpegQuality: CGFloat
    private let ciContext = CIContext()

    init(
        queue: MediaQueue,
        sessionId: String,
        currentStepId: @escaping () -> String,
        storageDir: URL,
        jpegQuality: CGFloat = 0.92
    ) {
        self.queue = queue
        self.sessionId = sessionId
        self.currentStepId = currentStepId
        self.storageDir = storageDir
    	self.jpegQuality = jpegQuality
    }

    /// Возвращает локальные UUID; серверные asset_id свяжутся при загрузке
    /// по sha256 (§04.2). Ошибка кодирования кадра пропускает кадр, а не
    /// роняет обмер — количество принятых кадров контролирует движок.
    func persist(frames: [ARFrame], includeDepth: Bool) async -> [String] {
        var ids: [String] = []
        for frame in frames {
            guard let jpeg = encodeJpeg(frame.capturedImage) else { continue }
            let localId = UUID().uuidString.lowercased()
            let url = storageDir.appendingPathComponent("\(localId).jpg")
            do {
                try jpeg.write(to: url, options: .atomic)
            } catch {
                continue
            }
            try? await queue.enqueue(media: PendingMedia(
                localPath: url.path,
                sessionId: sessionId,
                stepId: currentStepId(),
                kind: "photo",
                mime: "image/jpeg",
                state: .pending,
                attempts: 0,
                lastError: nil,
                assetId: nil
            ))
            ids.append(localId)

            if includeDepth, let depth = frame.sceneDepth {
                // Сырьё глубины — тот же построчный CSV, что и на Android
                // (ADR-0008): формат обязан совпадать, архив один на платформу.
                if let csv = Self.depthCsv(depth) {
                    let cloudUrl = storageDir.appendingPathComponent("\(localId).depth.csv")
                    if (try? csv.write(to: cloudUrl, atomically: true, encoding: .utf8)) != nil {
                        try? await queue.enqueue(media: PendingMedia(
                            localPath: cloudUrl.path,
                            sessionId: sessionId,
                            stepId: currentStepId(),
                            kind: "point_cloud",
                            mime: "text/csv",
                            state: .pending,
                            attempts: 0,
                            lastError: nil,
                            assetId: nil
                        ))
                    }
                }
            }
        }
        return ids
    }

    private func encodeJpeg(_ pixelBuffer: CVPixelBuffer) -> Data? {
        let image = CIImage(cvPixelBuffer: pixelBuffer)
        guard let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) else { return nil }
        return ciContext.jpegRepresentation(
            of: image,
            colorSpace: colorSpace,
            options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: jpegQuality]
        )
    }

    /// u;v;confidence;x;y;z — конвертацию метров ARKit в мм и депроекцию
    /// делает ARKit-движок обмера; здесь сохраняется карта глубины как есть
    /// (метры), с пометкой единиц в первой строке — честнее, чем молча
    /// смешать форматы до готовности iOS-движка.
    private static func depthCsv(_ depth: ARDepthData) -> String? {
        let map = depth.depthMap
        CVPixelBufferLockBaseAddress(map, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(map, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(map) else { return nil }
        let width = CVPixelBufferGetWidth(map)
        let height = CVPixelBufferGetHeight(map)
        let stride = CVPixelBufferGetBytesPerRow(map) / MemoryLayout<Float32>.size
        let values = base.assumingMemoryBound(to: Float32.self)

        var lines = ["# units=meters;layout=u;v;depth"]
        let step = max(1, Int((Double(width * height) / 1500.0).squareRoot()))
        var y = 0
        while y < height {
            var x = 0
            while x < width {
                let d = values[y * stride + x]
                if d > 0 {
                    lines.append("\(Double(x) / Double(width));\(Double(y) / Double(height));\(d)")
                }
                x += step
            }
            y += step
        }
        return lines.joined(separator: "\n")
    }
}
