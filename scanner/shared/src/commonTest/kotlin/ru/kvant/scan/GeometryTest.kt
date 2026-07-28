package ru.kvant.scan

import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.sin
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import ru.kvant.scan.measure.DepthSample
import ru.kvant.scan.measure.OrientedBox
import ru.kvant.scan.measure.SupportPlane
import ru.kvant.scan.measure.Vec3
import ru.kvant.scan.measure.medianDimensions
import ru.kvant.scan.measure.orientedBoundingBox
import ru.kvant.scan.measure.trimOutliers

/**
 * Геометрия обмера: PCA-габарит, отсечение выбросов, медиана серии.
 *
 * Смысл набора — §05.3: паллета под углом к оператору не должна «толстеть»,
 * одна залётная точка не должна растягивать габарит, confidence обязан
 * отражать разброс между кадрами, а не быть константой.
 */
class GeometryTest {

    private val floor = SupportPlane(origin = Vec3(0.0, 0.0, 0.0), normal = Vec3(0.0, 0.0, 1.0))

    /** Равномерная сетка точек коробки L×W×H, повёрнутой на angle вокруг нормали. */
    private fun boxCloud(
        lengthMm: Double,
        widthMm: Double,
        heightMm: Double,
        angleRad: Double,
        step: Double = 50.0,
    ): List<DepthSample> {
        val points = mutableListOf<DepthSample>()
        val ct = cos(angleRad)
        val st = sin(angleRad)
        var x = -lengthMm / 2
        while (x <= lengthMm / 2) {
            var y = -widthMm / 2
            while (y <= widthMm / 2) {
                // верхняя грань + рёбра по высоте достаточно: OBB важна проекция
                for (z in listOf(0.0, heightMm)) {
                    val rx = x * ct - y * st
                    val ry = x * st + y * ct
                    points += DepthSample(u = 0.5f, v = 0.5f, confidence = 1.0f, world = Vec3(rx, ry, z))
                }
                y += step
            }
            x += step
        }
        return points
    }

    @Test
    fun `повёрнутая на 30 градусов коробка сохраняет размеры`() {
        // AABB на этом облаке дал бы ~1379×1179 мм вместо 1200×800 — ошибка,
        // ради которой и существует PCA-габарит.
        val cloud = boxCloud(1200.0, 800.0, 600.0, angleRad = 30.0 * 3.14159265 / 180.0)
        val box = orientedBoundingBox(cloud, floor)
        assertTrue(abs(box.lengthMm - 1200.0) < 30.0, "length=${box.lengthMm}")
        assertTrue(abs(box.widthMm - 800.0) < 30.0, "width=${box.widthMm}")
        assertTrue(abs(box.heightMm - 600.0) < 1.0, "height=${box.heightMm}")
    }

    @Test
    fun `выброс на соседнем стеллаже отсекается до OBB`() {
        val cloud = boxCloud(1000.0, 600.0, 400.0, angleRad = 0.0)
        val withOutlier = cloud + DepthSample(0.9f, 0.9f, 1.0f, Vec3(2500.0, 0.0, 200.0))
        val trimmed = trimOutliers(withOutlier, trimFraction = 0.02)
        assertTrue(trimmed.none { it.world.x > 2000.0 }, "выброс должен быть отброшен")
        val box = orientedBoundingBox(trimmed, floor)
        assertTrue(abs(box.lengthMm - 1000.0) < 30.0, "length=${box.lengthMm}")
    }

    @Test
    fun `высота считается от опорной плоскости а не от разброса точек`() {
        val cloud = boxCloud(500.0, 500.0, 300.0, angleRad = 0.0)
        val box = orientedBoundingBox(cloud, floor)
        assertEquals(300.0, box.heightMm, absoluteTolerance = 0.5)
    }

    @Test
    fun `медиана устойчива к одному плохому кадру`() {
        val boxes = List(9) { OrientedBox(1200.0, 800.0, 600.0) } +
            OrientedBox(1900.0, 800.0, 600.0) // кадр с куском стеллажа в маске
        val dims = medianDimensions(boxes)
        assertEquals(1200.0, dims.lengthMm, absoluteTolerance = 0.5)
    }

    @Test
    fun `стабильная серия даёт высокий confidence а прыгающая низкий`() {
        val stable = medianDimensions(List(10) { OrientedBox(1000.0, 700.0, 500.0) })
        assertTrue(stable.spreadBasedConfidence > 0.95, "stable=${stable.spreadBasedConfidence}")

        val noisy = medianDimensions(
            (0 until 10).map { i ->
                val jitter = if (i % 2 == 0) 0.85 else 1.15
                OrientedBox(1000.0 * jitter, 700.0 * jitter, 500.0 * jitter)
            }
        )
        assertTrue(
            noisy.spreadBasedConfidence < stable.spreadBasedConfidence - 0.3,
            "noisy=${noisy.spreadBasedConfidence} stable=${stable.spreadBasedConfidence}",
        )
    }
}
