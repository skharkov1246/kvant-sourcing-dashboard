import ARKit
import Vision
import shared   // KMP-фреймворк: домен, интерпретатор протокола, outbox, sync

/// Реализация обмера на iOS.
///
/// Граница ровно та же, что на Android (ADR-0001): общее ядро решает, КАКОЙ
/// класс точности нужен и как выбирать ступень деградации; работа с сенсорами —
/// нативная. Логика ниже не должна знать ничего о протоколе съёмки: она получает
/// `Request` и возвращает `Result`.
///
/// На устройствах с LiDAR доступен класс A (±3–5 мм) — это единственная
/// платформенная разница по сравнению с Android, и она честно отражается
/// в `capabilities`, а не прячется.
final class ArKitMeasurementEngine: NSObject, MeasurementEngine {

    private let session = ARSession()
    private let assetWriter: AssetWriting
    private let markerDetector: MarkerDetecting

    init(assetWriter: AssetWriting, markerDetector: MarkerDetecting) {
        self.assetWriter = assetWriter
        self.markerDetector = markerDetector
        super.init()
    }

    var capabilities: MeasurementEngineCapabilities {
        var methods: Set<MeasureMethod> = [.manual]
        if markerDetector.isAvailable { methods.insert(.marker) }
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) { methods.insert(.arDepth) }
        if ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh) { methods.insert(.lidar) }

        let best = methods.min { $0.accuracyClass.rank < $1.accuracyClass.rank }?.accuracyClass ?? .d
        return MeasurementEngineCapabilities(
            supportedMethods: methods,
            maxAccuracy: best,
            hasDepthSensor: ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh),
            canCaptureDepthFrames: ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)
        )
    }

    func measure(request: MeasurementEngineRequest) async -> MeasurementEngineResult {
        switch request.method {
        case .lidar, .arDepth:
            return await measureWithSceneDepth(request)
        case .marker:
            return await measureWithMarker(request)
        case .manual, .photogrammetry:
            return MeasurementEngineResultUnavailable(
                reason: .noDepthSupport,
                message: "Обрабатывается вне движка обмера"
            )
        }
    }

    // MARK: - Класс A/B: сцена по глубине

    private func measureWithSceneDepth(_ request: MeasurementEngineRequest) async -> MeasurementEngineResult {
        guard let plane = await findSupportPlane() else {
            return MeasurementEngineResultUnavailable(
                reason: .planeNotFound,
                message: "Не найдена опорная плоскость. Отойдите и медленно поведите камерой по полу."
            )
        }

        let frames = await collectFrames(budget: Int(request.frameBudget))
        guard frames.count >= Self.minFrames else {
            return MeasurementEngineResultUnavailable(
                reason: .tooFewFrames,
                message: "Недостаточно кадров: \(frames.count) из \(Self.minFrames). Обойдите объект медленнее."
            )
        }

        // Сегментация объекта — нативная Vision, а не своя модель: работает
        // офлайн и использует Neural Engine.
        var boxes: [OrientedBox] = []
        for frame in frames {
            guard let mask = await segmentSubject(in: frame.capturedImage) else { continue }
            let points = frame.depthPoints
                .filter { mask.contains($0.uv) && $0.confidence >= Self.minDepthConfidence }
            let trimmed = trimOutliers(points)          // отсечение 2 % хвостов
            guard trimmed.count >= Self.minPoints else { continue }
            boxes.append(orientedBoundingBox(trimmed, supportPlane: plane))
        }

        guard !boxes.isEmpty else {
            return MeasurementEngineResultUnavailable(
                reason: .subjectNotSegmented,
                message: "Объект не выделен. Проверьте освещение и уберите лишнее из кадра."
            )
        }

        let dims = medianDimensions(boxes)   // медиана по кадрам, разброс → confidence
        let assetIds = await assetWriter.persist(frames, includeDepth: request.captureDepth)

        return MeasurementEngineResultSuccess(
            method: request.method,
            accuracyClass: request.method.accuracyClass,
            lengthMm: dims.length,
            widthMm: dims.width,
            heightMm: dims.height,
            confidence: dims.spreadBasedConfidence,
            assetIds: assetIds,
            framesUsed: Int32(boxes.count)
        )
    }

    // MARK: - Класс C: эталонный маркер

    private func measureWithMarker(_ request: MeasurementEngineRequest) async -> MeasurementEngineResult {
        guard let sizeMm = request.markerSizeMm?.doubleValue,
              let detection = await markerDetector.detect(type: request.markerType, sizeMm: sizeMm) else {
            return MeasurementEngineResultUnavailable(
                reason: .markerNotDetected,
                message: "Маркер не распознан. Положите карточку на измеряемую грань целиком в кадр."
            )
        }
        return await detection.toResult(assetWriter)
    }

    private static let minFrames = 8
    private static let minPoints = 400
    private static let minDepthConfidence: Float = 0.5
}
