package ru.kvant.scan.android.measure

import ru.kvant.scan.domain.AccuracyClass
import ru.kvant.scan.domain.MeasureMethod
import ru.kvant.scan.measure.MeasurementEngine

/**
 * Реализация обмера на Android.
 *
 * Класс B — основной рабочий режим (§05.3):
 *   1. опорная плоскость от ARCore задаёт систему координат и «низ» объекта;
 *   2. ML Kit Subject Segmentation отделяет товар от фона;
 *   3. точки глубины внутри маски фильтруются по карте достоверности
 *      и по расстоянию (отсечение 2 % хвостов — иначе одна залётная точка
 *      на соседнем стеллаже растянет габарит на метр);
 *   4. PCA в плоскости опоры даёт ОРИЕНТИРОВАННЫЙ параллелепипед: паллета
 *      под 30° к оператору при AABB даёт ошибку до 40 %;
 *   5. медиана по 8–15 кадрам, разброс между кадрами → confidence.
 *
 * Класс C (маркер) работает на любом телефоне и служит страховкой; класс D
 * (рулетка + обязательное фото) доступен всегда, поэтому тупика не бывает.
 */
class ArCoreMeasurementEngine(
    private val arSession: ArSessionFacade,
    private val segmenter: SubjectSegmenter,
    private val markerDetector: MarkerDetector,
    private val assetWriter: AssetWriter,
) : MeasurementEngine {

    override val capabilities: MeasurementEngine.Capabilities by lazy {
        val methods = buildSet {
            add(MeasureMethod.MANUAL)
            if (markerDetector.isAvailable) add(MeasureMethod.MARKER)
            if (arSession.supportsDepth) add(MeasureMethod.AR_DEPTH)
            if (arSession.hasTimeOfFlightSensor) add(MeasureMethod.LIDAR)
        }
        MeasurementEngine.Capabilities(
            supportedMethods = methods,
            maxAccuracy = methods.minByOrNull { it.accuracyClass.rank }
                ?.accuracyClass ?: AccuracyClass.D,
            hasDepthSensor = arSession.hasTimeOfFlightSensor,
            canCaptureDepthFrames = arSession.supportsDepth,
        )
    }

    override suspend fun measure(request: MeasurementEngine.Request): MeasurementEngine.Result =
        when (request.method) {
            MeasureMethod.LIDAR, MeasureMethod.AR_DEPTH -> measureWithDepth(request)
            MeasureMethod.MARKER -> measureWithMarker(request)
            MeasureMethod.MANUAL -> MeasurementEngine.Result.Unavailable(
                MeasurementEngine.Reason.NO_DEPTH_SUPPORT,
                "Ручной ввод обрабатывается формой, а не движком обмера",
            )
            MeasureMethod.PHOTOGRAMMETRY -> MeasurementEngine.Result.Unavailable(
                MeasurementEngine.Reason.NO_DEPTH_SUPPORT,
                "Фотограмметрия считается на сервере (§05.5)",
            )
        }

    private suspend fun measureWithDepth(request: MeasurementEngine.Request): MeasurementEngine.Result {
        val plane = arSession.findSupportPlane()
            ?: return MeasurementEngine.Result.Unavailable(
                MeasurementEngine.Reason.PLANE_NOT_FOUND,
                "Не найдена опорная плоскость. Отойдите и медленно поведите камерой по полу.",
            )

        val frames = arSession.collectFrames(request.frameBudget)
        if (frames.size < MIN_FRAMES) {
            return MeasurementEngine.Result.Unavailable(
                MeasurementEngine.Reason.TOO_FEW_FRAMES,
                "Недостаточно кадров: снято ${frames.size} из $MIN_FRAMES. Обойдите объект медленнее.",
            )
        }

        val boxes = frames.mapNotNull { frame ->
            val mask = segmenter.segment(frame.image) ?: return@mapNotNull null
            val points = frame.depthPoints
                .filter { mask.contains(it.u, it.v) && it.confidence >= MIN_DEPTH_CONFIDENCE }
                .let(::trimOutliers)
            if (points.size < MIN_POINTS) null else orientedBoundingBox(points, plane)
        }

        if (boxes.isEmpty()) {
            return MeasurementEngine.Result.Unavailable(
                MeasurementEngine.Reason.SUBJECT_NOT_SEGMENTED,
                "Объект не выделен на кадрах. Проверьте освещение и уберите лишнее из кадра.",
            )
        }

        val dims = medianDimensions(boxes)
        // Сырьё сохраняется всегда, если протокол этого просит: через год модель
        // станет точнее и габариты пересчитаются по архиву (ADR-0008).
        val assetIds = assetWriter.persist(frames, includeDepth = request.captureDepth)

        return MeasurementEngine.Result.Success(
            method = request.method,
            accuracyClass = request.method.accuracyClass,
            lengthMm = dims.lengthMm,
            widthMm = dims.widthMm,
            heightMm = dims.heightMm,
            confidence = dims.spreadBasedConfidence,
            assetIds = assetIds,
            framesUsed = boxes.size,
        )
    }

    private suspend fun measureWithMarker(request: MeasurementEngine.Request): MeasurementEngine.Result {
        val markerSizeMm = request.markerSizeMm
            ?: return MeasurementEngine.Result.Unavailable(
                MeasurementEngine.Reason.MARKER_NOT_DETECTED,
                "Протокол не задал размер эталонного маркера",
            )
        val detection = markerDetector.detect(request.markerType, markerSizeMm)
            ?: return MeasurementEngine.Result.Unavailable(
                MeasurementEngine.Reason.MARKER_NOT_DETECTED,
                "Маркер не распознан. Положите карточку на измеряемую грань целиком в кадр.",
            )
        // Честное ограничение: гомография даёт метрику ТОЛЬКО в плоскости
        // маркера. Высота берётся вторым кадром с маркером на боковой грани (§05.4).
        return detection.toResult(assetWriter)
    }

    private companion object {
        const val MIN_FRAMES = 8
        const val MIN_POINTS = 400
        const val MIN_DEPTH_CONFIDENCE = 0.5f
    }
}
