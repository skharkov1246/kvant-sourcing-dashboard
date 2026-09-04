package ru.kvant.scan.measure

import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Геометрия обмера — общая для ArCore и ArKit (§05.3, шаги 3–5).
 *
 * Здесь нет ни одной ссылки на платформенный API: платформа отдаёт точки
 * глубины и опорную плоскость, ядро считает ориентированный габарит. Логика
 * обязана совпадать на Android и iOS — расхождение в OBB означает, что один
 * и тот же ящик на двух телефонах имеет разные размеры.
 *
 * Единицы: миллиметры. Платформенный фасад конвертирует метры ARCore/ARKit
 * на своей стороне — ядро с метрами не встречается вовсе.
 */

data class Vec3(val x: Double, val y: Double, val z: Double) {
    operator fun plus(o: Vec3) = Vec3(x + o.x, y + o.y, z + o.z)
    operator fun minus(o: Vec3) = Vec3(x - o.x, y - o.y, z - o.z)
    operator fun times(s: Double) = Vec3(x * s, y * s, z * s)
    infix fun dot(o: Vec3): Double = x * o.x + y * o.y + z * o.z
    infix fun cross(o: Vec3) = Vec3(y * o.z - z * o.y, z * o.x - x * o.z, x * o.y - y * o.x)
    val norm: Double get() = sqrt(this dot this)
    fun normalized(): Vec3 = norm.let { if (it == 0.0) this else Vec3(x / it, y / it, z / it) }
}

/**
 * Точка глубины: координаты кадра (u,v в [0,1]) для сверки с маской сегментации,
 * достоверность сенсора и мировая позиция в миллиметрах.
 */
data class DepthSample(
    val u: Float,
    val v: Float,
    val confidence: Float,
    val world: Vec3,
)

/** Опорная плоскость от AR-трекинга: точка и единичная нормаль («низ» объекта). */
data class SupportPlane(val origin: Vec3, val normal: Vec3) {
    /** Высота точки над плоскостью, мм. Отрицательная — под плоскостью (шум). */
    fun heightOf(p: Vec3): Double = (p - origin) dot normal.normalized()
}

/** Ориентированный габарит одного кадра, мм. length ≥ width по построению. */
data class OrientedBox(val lengthMm: Double, val widthMm: Double, val heightMm: Double)

/** Итог по серии кадров: медианные габариты и оценка из разброса, не выдуманная. */
data class Dimensions(
    val lengthMm: Double,
    val widthMm: Double,
    val heightMm: Double,
    val spreadBasedConfidence: Double,
)

/**
 * Отсечение хвостов по расстоянию от центроида (§05.3, шаг 3): одна залётная
 * точка на соседнем стеллаже растянет габарит на метр, поэтому дальние 2 %
 * отбрасываются безусловно.
 */
fun trimOutliers(points: List<DepthSample>, trimFraction: Double = 0.02): List<DepthSample> {
    if (points.size < 3) return points
    val c = points.fold(Vec3(0.0, 0.0, 0.0)) { acc, p -> acc + p.world } * (1.0 / points.size)
    val keep = (points.size * (1.0 - trimFraction)).toInt().coerceAtLeast(1)
    return points.sortedBy { (it.world - c).norm }.take(keep)
}

/**
 * Ориентированный параллелепипед в системе опорной плоскости (§05.3, шаг 4).
 *
 * Точки проецируются в плоскость, 2D-PCA даёт главные оси — паллета под 30°
 * к оператору при осевом (AABB) боксе получила бы ошибку до 40 %. Высота —
 * максимум над плоскостью, потому что «низ» объекта задаёт сама плоскость.
 */
fun orientedBoundingBox(points: List<DepthSample>, plane: SupportPlane): OrientedBox {
    require(points.isNotEmpty()) { "OBB по пустому множеству точек не существует" }
    val n = plane.normal.normalized()
    // Ортонормальный базис плоскости: любой вектор не коллинеарный нормали.
    val seed = if (abs(n.x) < 0.9) Vec3(1.0, 0.0, 0.0) else Vec3(0.0, 1.0, 0.0)
    val e1 = (seed - n * (seed dot n)).normalized()
    val e2 = (n cross e1).normalized()

    val us = DoubleArray(points.size)
    val vs = DoubleArray(points.size)
    var height = 0.0
    points.forEachIndexed { i, p ->
        val d = p.world - plane.origin
        us[i] = d dot e1
        vs[i] = d dot e2
        val h = plane.heightOf(p.world)
        if (h > height) height = h
    }

    // 2D-PCA замкнутой формой: угол главной оси из ковариационной матрицы.
    val mu = us.average()
    val mv = vs.average()
    var cuu = 0.0; var cvv = 0.0; var cuv = 0.0
    for (i in us.indices) {
        val du = us[i] - mu
        val dv = vs[i] - mv
        cuu += du * du; cvv += dv * dv; cuv += du * dv
    }
    val theta = 0.5 * atan2(2.0 * cuv, cuu - cvv)
    val ct = cos(theta)
    val st = sin(theta)

    var minA = Double.MAX_VALUE; var maxA = -Double.MAX_VALUE
    var minB = Double.MAX_VALUE; var maxB = -Double.MAX_VALUE
    for (i in us.indices) {
        val a = us[i] * ct + vs[i] * st
        val b = -us[i] * st + vs[i] * ct
        if (a < minA) minA = a; if (a > maxA) maxA = a
        if (b < minB) minB = b; if (b > maxB) maxB = b
    }
    val extentA = maxA - minA
    val extentB = maxB - minB
    return OrientedBox(
        lengthMm = maxOf(extentA, extentB),
        widthMm = minOf(extentA, extentB),
        heightMm = height,
    )
}

/**
 * Медиана по серии кадров (§05.3, шаг 5). Confidence выводится из разброса
 * между кадрами: стабильные кадры → близко к 1, прыгающие → к 0. Это оценка
 * повторяемости, а не точности — и ровно так она названа.
 */
fun medianDimensions(boxes: List<OrientedBox>): Dimensions {
    require(boxes.isNotEmpty()) { "Медиана по пустой серии кадров не существует" }
    val l = boxes.map { it.lengthMm }.sorted()
    val w = boxes.map { it.widthMm }.sorted()
    val h = boxes.map { it.heightMm }.sorted()

    fun median(sorted: List<Double>): Double {
        val mid = sorted.size / 2
        return if (sorted.size % 2 == 1) sorted[mid] else (sorted[mid - 1] + sorted[mid]) / 2.0
    }

    val ml = median(l)
    val mw = median(w)
    val mh = median(h)

    // Относительный межквартильный разброс, усреднённый по трём осям.
    fun relSpread(sorted: List<Double>, med: Double): Double {
        if (sorted.size < 4 || med <= 0.0) return 0.0
        val q1 = sorted[sorted.size / 4]
        val q3 = sorted[sorted.size * 3 / 4]
        return (q3 - q1) / med
    }

    val spread = (relSpread(l, ml) + relSpread(w, mw) + relSpread(h, mh)) / 3.0
    return Dimensions(
        lengthMm = ml,
        widthMm = mw,
        heightMm = mh,
        spreadBasedConfidence = (1.0 - spread * 5.0).coerceIn(0.0, 1.0),
    )
}
