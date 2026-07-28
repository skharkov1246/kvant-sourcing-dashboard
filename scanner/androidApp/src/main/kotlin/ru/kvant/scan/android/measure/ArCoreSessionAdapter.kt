package ru.kvant.scan.android.measure

import android.content.Context
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.Image
import com.google.ar.core.Config
import com.google.ar.core.Frame
import com.google.ar.core.Plane
import com.google.ar.core.Session
import com.google.ar.core.TrackingState
import com.google.ar.core.exceptions.NotYetAvailableException
import kotlinx.coroutines.delay
import ru.kvant.scan.measure.DepthSample
import ru.kvant.scan.measure.SupportPlane
import ru.kvant.scan.measure.Vec3
import java.nio.ByteOrder
import kotlin.math.max
import kotlin.math.sqrt

/**
 * Боевая реализация [ArSessionFacade] поверх com.google.ar.core.Session.
 *
 * Границы ответственности (см. Facades.kt):
 *   * конвертация метров ARCore в миллиметры происходит ЗДЕСЬ — ядро с
 *     метрами не встречается;
 *   * все вызовы должны идти из потока, владеющего GL-контекстом AR-сеанса
 *     (session.update() в другом потоке кидает исключение) — хост-экран
 *     обязан вызывать движок обмера из своего рендер-скоупа;
 *   * кадры камеры ([ArCameraImage]) остаются открытыми до конца обмера:
 *     их читают сегментация и AssetWriter; закрывает их хост после
 *     завершения measure() — иначе ImageReader исчерпает буферы.
 */
class ArCameraImage(val image: Image) : CameraImage, AutoCloseable {
    override fun close() = image.close()
}

class ArCoreSessionAdapter(
    private val session: Session,
    override val hasTimeOfFlightSensor: Boolean,
    private val frameIntervalMs: Long = 66,
) : ArSessionFacade {

    override val supportsDepth: Boolean =
        session.isDepthModeSupported(Config.DepthMode.AUTOMATIC)

    override suspend fun findSupportPlane(): SupportPlane? {
        // Плоскость ищется ограниченное время: если пол не найден за таймаут,
        // движок честно вернёт PLANE_NOT_FOUND с подсказкой, а не зависнет.
        var elapsed = 0L
        while (elapsed < PLANE_TIMEOUT_MS) {
            val frame = session.update()
            if (frame.camera.trackingState == TrackingState.TRACKING) {
                val plane = session.getAllTrackables(Plane::class.java)
                    .filter {
                        it.trackingState == TrackingState.TRACKING &&
                            it.type == Plane.Type.HORIZONTAL_UPWARD_FACING &&
                            it.subsumedBy == null
                    }
                    // Самая большая устойчивая плоскость — почти всегда пол/стол.
                    .maxByOrNull { it.extentX * it.extentZ }
                if (plane != null && plane.extentX * plane.extentZ >= MIN_PLANE_AREA_M2) {
                    val pose = plane.centerPose
                    val yAxis = pose.yAxis
                    return SupportPlane(
                        origin = Vec3(pose.tx() * MM, pose.ty() * MM, pose.tz() * MM),
                        normal = Vec3(
                            yAxis[0].toDouble(), yAxis[1].toDouble(), yAxis[2].toDouble(),
                        ),
                    )
                }
            }
            delay(frameIntervalMs)
            elapsed += frameIntervalMs
        }
        return null
    }

    override suspend fun collectFrames(budget: Int): List<ArFrame> {
        val frames = mutableListOf<ArFrame>()
        // Попыток больше бюджета: кадры без трекинга/глубины пропускаются молча,
        // но общий цикл конечен — деградацию решает движок, а не адаптер.
        var attempts = 0
        while (frames.size < budget && attempts < budget * ATTEMPTS_PER_FRAME) {
            attempts++
            val frame = session.update()
            if (frame.camera.trackingState != TrackingState.TRACKING) {
                delay(frameIntervalMs); continue
            }
            val depthPoints = try {
                frame.acquireDepthImage16Bits().use { sampleDepth(frame, it) }
            } catch (_: NotYetAvailableException) {
                delay(frameIntervalMs); continue
            }
            if (depthPoints.size < MIN_POINTS_PER_FRAME) {
                delay(frameIntervalMs); continue
            }
            val image = try {
                frame.acquireCameraImage()
            } catch (_: NotYetAvailableException) {
                delay(frameIntervalMs); continue
            }
            frames += ArFrame(
                image = ArCameraImage(image),
                depthPoints = depthPoints,
                timestampMs = frame.timestamp / 1_000_000,
            )
            delay(frameIntervalMs)
        }
        return frames
    }

    /**
     * DEPTH16 → мировые точки в мм.
     *
     * Формат пикселя (android.graphics.ImageFormat.DEPTH16): биты 0–12 —
     * глубина в мм, биты 13–15 — достоверность (0 = 100 %, 1 = 0 %, 7 ≈ 100 %).
     * Пиксель депроецируется интринсиками CPU-кадра (пересчитанными под
     * разрешение карты глубины) в систему камеры OpenGL (+X вправо, +Y вверх,
     * −Z вперёд) и переводится позой камеры в мир.
     */
    private fun sampleDepth(frame: Frame, depth: Image): List<DepthSample> {
        val intrinsics = frame.camera.imageIntrinsics
        val fx = intrinsics.focalLength[0].toDouble()
        val fy = intrinsics.focalLength[1].toDouble()
        val cx = intrinsics.principalPoint[0].toDouble()
        val cy = intrinsics.principalPoint[1].toDouble()
        val scaleX = depth.width.toDouble() / intrinsics.imageDimensions[0]
        val scaleY = depth.height.toDouble() / intrinsics.imageDimensions[1]

        val plane = depth.planes[0]
        // DEPTH16 отдаётся в порядке байт устройства, а ByteBuffer по
        // умолчанию big-endian — без явного порядка глубина превращается в шум.
        val buffer = plane.buffer.order(ByteOrder.LITTLE_ENDIAN)
        val rowStride = plane.rowStride
        val pose = frame.camera.pose
        // Шаг выборки подобран так, чтобы кадр давал ~MAX_POINTS точек:
        // больше не нужно — OBB считается по облаку, а не по каждому пикселю.
        val stride = max(1, sqrt(depth.width * depth.height / MAX_POINTS.toDouble()).toInt())

        val out = ArrayList<DepthSample>(MAX_POINTS)
        var py = 0
        while (py < depth.height) {
            var px = 0
            while (px < depth.width) {
                val raw = buffer.getShort(py * rowStride + px * 2).toInt() and 0xFFFF
                val depthMm = raw and 0x1FFF
                val confBits = (raw shr 13) and 0x7
                if (depthMm > 0) {
                    val confidence =
                        if (confBits == 0) 1.0f else (confBits - 1) / 7.0f
                    val zM = depthMm / 1000.0
                    val xImg = px / scaleX
                    val yImg = py / scaleY
                    val camPoint = floatArrayOf(
                        ((xImg - cx) * zM / fx).toFloat(),
                        (-(yImg - cy) * zM / fy).toFloat(),
                        (-zM).toFloat(),
                    )
                    val world = pose.transformPoint(camPoint)
                    out += DepthSample(
                        u = (px.toFloat() / depth.width),
                        v = (py.toFloat() / depth.height),
                        confidence = confidence,
                        world = Vec3(
                            world[0] * MM, world[1] * MM, world[2] * MM,
                        ),
                    )
                }
                px += stride
            }
            py += stride
        }
        return out
    }

    companion object {
        private const val MM = 1000.0
        private const val PLANE_TIMEOUT_MS = 6_000L
        private const val MIN_PLANE_AREA_M2 = 0.04f // 20×20 см — меньше не «пол»
        private const val MIN_POINTS_PER_FRAME = 50
        private const val MAX_POINTS = 1_500
        private const val ATTEMPTS_PER_FRAME = 4

        /**
         * Аппаратный ToF/LiDAR ARCore не сообщает — определяется по
         * Camera2: тыловая камера с capability DEPTH_OUTPUT. Это эвристика
         * класса A против B (§05.2); ложный минус безопасен — режим просто
         * останется классом B.
         */
        fun probeTimeOfFlight(context: Context): Boolean {
            val manager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
            return manager.cameraIdList.any { id ->
                val ch = manager.getCameraCharacteristics(id)
                val caps = ch.get(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES)
                val backFacing = ch.get(CameraCharacteristics.LENS_FACING) ==
                    CameraCharacteristics.LENS_FACING_BACK
                backFacing && caps?.contains(
                    CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_DEPTH_OUTPUT,
                ) == true
            }
        }
    }
}
