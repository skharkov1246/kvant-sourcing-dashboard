package ru.kvant.scan.android.measure

import ru.kvant.scan.domain.AccuracyClass
import ru.kvant.scan.domain.MeasureMethod
import ru.kvant.scan.measure.DepthSample
import ru.kvant.scan.measure.MeasurementEngine
import ru.kvant.scan.measure.SupportPlane

/**
 * Фасады платформенных сенсоров для [ArCoreMeasurementEngine].
 *
 * Смысл слоя — тестируемость: движок обмера работает с этими интерфейсами,
 * а не с ARCore/ML Kit напрямую, поэтому его логика (деградация, фильтрация,
 * confidence) проверяется без эмулятора. Боевые реализации — тонкие адаптеры:
 * `ArCoreSessionAdapter` (com.google.ar.core.Session), `MlKitSubjectSegmenter`
 * (ML Kit Subject Segmentation), `OpenCvArucoDetector` (ArUco/AprilTag).
 * Конвертация метров ARCore в миллиметры — обязанность адаптера: ядро с
 * метрами не встречается (см. Geometry.kt).
 */

/** Непрозрачная ссылка на кадр камеры; конкретный тип знает только адаптер. */
interface CameraImage

/** Кадр AR-сеанса: изображение для сегментации + точки глубины в мм. */
data class ArFrame(
    val image: CameraImage,
    val depthPoints: List<DepthSample>,
    val timestampMs: Long,
)

/** Обёртка над AR-сеансом (ARCore Session + Frame API). */
interface ArSessionFacade {
    /** Depth API доступен (аппаратно или ML-глубина). */
    val supportsDepth: Boolean

    /** Аппаратный ToF/LiDAR — класс A вместо B (§05.2). */
    val hasTimeOfFlightSensor: Boolean

    /** Опорная плоскость: система координат и «низ» объекта. null — не найдена. */
    suspend fun findSupportPlane(): SupportPlane?

    /** Накапливает до [budget] кадров, отбрасывая кадры без глубины. */
    suspend fun collectFrames(budget: Int): List<ArFrame>
}

/** Маска объекта в координатах кадра (u,v в [0,1]). */
interface SegmentMask {
    fun contains(u: Float, v: Float): Boolean
}

/** Сегментация товара от фона (ML Kit Subject Segmentation). */
interface SubjectSegmenter {
    /** null — объект не выделен на кадре. */
    suspend fun segment(image: CameraImage): SegmentMask?
}

/** Детектор эталонного маркера (ArUco / AprilTag / кредитная карта). */
interface MarkerDetector {
    val isAvailable: Boolean

    /** null — маркер не найден в потоке камеры за отведённое время. */
    suspend fun detect(markerType: String?, markerSizeMm: Double): MarkerDetection?
}

/**
 * Результат обмера по маркеру. Гомография даёт метрику только в плоскости
 * маркера, поэтому высота приходит вторым кадром с маркером на боковой
 * грани (§05.4) — собрать оба кадра обязан детектор.
 */
data class MarkerDetection(
    val lengthMm: Double,
    val widthMm: Double,
    val heightMm: Double,
    /** Из остаточной ошибки гомографии, а не константа. */
    val confidence: Double,
    val frames: List<ArFrame>,
) {
    suspend fun toResult(assetWriter: AssetWriter): MeasurementEngine.Result {
        val assetIds = assetWriter.persist(frames, includeDepth = false)
        return MeasurementEngine.Result.Success(
            method = MeasureMethod.MARKER,
            accuracyClass = AccuracyClass.C,
            lengthMm = lengthMm,
            widthMm = widthMm,
            heightMm = heightMm,
            confidence = confidence,
            assetIds = assetIds,
            framesUsed = frames.size,
        )
    }
}

/**
 * Сохранение сырья на диск и регистрация ассетов в локальной БД.
 * Возвращает идентификаторы — sha256 и догрузка чанками остаются за
 * медиа-каналом синхронизации (§04.2, инвариант I-1).
 */
interface AssetWriter {
    suspend fun persist(frames: List<ArFrame>, includeDepth: Boolean): List<String>
}
