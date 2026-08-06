package ru.kvant.scan.android.capture

import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import ru.kvant.scan.capture.FrameMetrics
import ru.kvant.scan.capture.FrameMetricsCalc

/**
 * Мост CameraX → платформонезависимые метрики кадра.
 *
 * Берёт Y-плоскость (яркость) кадра превью, даунсемплит до ~160×120 —
 * этого хватает для резкости/блика/заполнения, а счёт укладывается в
 * миллисекунды на слабом телефоне. Вся интерпретация — в общем ядре
 * (LiveGate), здесь только выкапывание байтов из ImageProxy.
 */
class GateFrameAnalyzer(
    private val onMetrics: (FrameMetrics) -> Unit,
) : ImageAnalysis.Analyzer {

    private var prev: IntArray? = null
    private var luma: IntArray = IntArray(0)

    override fun analyze(image: ImageProxy) {
        try {
            val plane = image.planes[0]
            val buf = plane.buffer
            val rowStride = plane.rowStride
            val pixStride = plane.pixelStride
            val srcW = image.width
            val srcH = image.height
            // шаг даунсемпла: целимся в ~160 по ширине
            val step = maxOf(1, srcW / 160)
            val w = srcW / step
            val h = srcH / step
            if (luma.size != w * h) luma = IntArray(w * h)
            var i = 0
            for (y in 0 until h) {
                val srcRow = y * step * rowStride
                for (x in 0 until w) {
                    luma[i++] = buf.get(srcRow + x * step * pixStride).toInt() and 0xFF
                }
            }
            val metrics = FrameMetricsCalc.fromLuma(luma, w, h, prev)
            // prev переиспользуется без аллокаций: массив меняется местами
            val keep = prev
            prev = if (keep != null && keep.size == luma.size) {
                luma.copyInto(keep); keep
            } else {
                luma.copyOf()
            }
            onMetrics(metrics)
        } finally {
            image.close()
        }
    }
}
