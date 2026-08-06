package ru.kvant.scan.capture

import kotlin.math.abs

/**
 * Живая проверка кадра в видоискателе — как при банковской съёмке паспорта.
 *
 * Камера непрерывно отдаёт кадры анализатору; по каждому считаются простые
 * метрики (резкость, блик, свет, заполнение кадра, движение), и человек
 * получает ОДНУ подсказку что исправить — до нажатия кнопки, а не после.
 * Когда несколько кадров подряд хорошие, срабатывает автоспуск: телефон
 * фотографирует сам, как банковские приложения делают с документами.
 *
 * Пороги выведены из разбора живого архива компании (87 фото: блик на 55,
 * темнота, смаз) — это защита от ошибок, которые реально происходили.
 * Логика платформонезависимая и покрыта тестами; Android/iOS поставляют
 * только массив яркости кадра.
 */
data class FrameMetrics(
    /** Средний градиент яркости, 0..~50: ниже порога — смаз или расфокус. */
    val sharpness: Double,
    /** Доля почти белых пикселей (яркость > 250), 0..1: блик от металла. */
    val glareFraction: Double,
    /** Средняя яркость кадра, 0..1. */
    val meanLuma: Double,
    /** Доля пикселей с заметной фактурой, 0..1: пустой стол ≈ 0, деталь крупно ≈ 0.2+. */
    val fillFraction: Double,
    /** Средняя попиксельная разница с прошлым кадром, 0..1: дрожание рук. */
    val motion: Double,
)

object FrameMetricsCalc {

    /**
     * Метрики из массива яркости (Y-плоскость кадра, даунсемпл ~160×120).
     * Считается на каждом кадре превью, поэтому только целочисленные
     * проходы без аллокаций.
     */
    fun fromLuma(luma: IntArray, width: Int, height: Int, prev: IntArray? = null): FrameMetrics {
        require(luma.size == width * height) { "размер массива не совпадает с кадром" }
        var gradSum = 0L
        var gradCount = 0
        var textured = 0
        var bright = 0
        var lumaSum = 0L
        for (y in 0 until height) {
            for (x in 0 until width) {
                val i = y * width + x
                val v = luma[i]
                lumaSum += v
                if (v > 250) bright++
                if (x + 1 < width && y + 1 < height) {
                    val g = abs(v - luma[i + 1]) + abs(v - luma[i + width])
                    gradSum += g
                    gradCount++
                    if (g > 24) textured++
                }
            }
        }
        var motion = 0.0
        if (prev != null && prev.size == luma.size) {
            var diff = 0L
            for (i in luma.indices) diff += abs(luma[i] - prev[i])
            motion = diff.toDouble() / luma.size / 255.0
        }
        val n = luma.size.toDouble()
        return FrameMetrics(
            sharpness = if (gradCount > 0) gradSum.toDouble() / gradCount else 0.0,
            glareFraction = bright / n,
            meanLuma = lumaSum / n / 255.0,
            fillFraction = if (gradCount > 0) textured.toDouble() / gradCount else 0.0,
            motion = motion,
        )
    }
}

/** Решение по кадру: одна подсказка за раз — человек не читает списки в видоискателе. */
data class GateReading(
    val ok: Boolean,
    val hint: String?,
    /** 0..1 — прогресс до автоспуска, рисуется кольцом вокруг кнопки. */
    val autoProgress: Float,
    /** true ровно один раз, когда серия хороших кадров дозрела до автоспуска. */
    val autoFire: Boolean,
)

class LiveGate(
    private val minSharpness: Double = 6.0,
    private val maxGlare: Double = 0.04,
    private val minLuma: Double = 0.14,
    private val minFill: Double = 0.03,
    private val maxFill: Double = 0.85,
    private val maxMotion: Double = 0.045,
    /** Сколько хороших кадров подряд нужно для автоспуска (~1 с при 10 fps). */
    private val stableFrames: Int = 10,
) {
    private var goodStreak = 0
    private var fired = false

    /** Новый шаг/кадр сделан — серия начинается заново. */
    fun reset() {
        goodStreak = 0
        fired = false
    }

    fun onFrame(m: FrameMetrics): GateReading {
        // Порядок подсказок — от грубого к тонкому: сначала «в кадре пусто»,
        // потом свет, потом блик, потом уже резкость и дрожание.
        val hint = when {
            // Свет — раньше всего: в темноте остальные метрики бессмысленны
            // (фактура «пропадает», и подсказка ушла бы не про то).
            m.meanLuma < minLuma -> "Темно — добавьте света"
            m.fillFraction < minFill -> "Наведите камеру на деталь — ближе"
            m.fillFraction > maxFill -> "Слишком близко — отойдите"
            m.glareFraction > maxGlare -> "Блик — наклоните телефон на 45°"
            m.motion > maxMotion -> "Держите телефон ровно"
            m.sharpness < minSharpness -> "Нет резкости — замрите на секунду"
            else -> null
        }
        if (hint == null && !fired) goodStreak++ else if (hint != null) goodStreak = 0
        val progress = (goodStreak.toFloat() / stableFrames).coerceIn(0f, 1f)
        val fire = !fired && goodStreak >= stableFrames
        if (fire) fired = true
        return GateReading(ok = hint == null, hint = hint, autoProgress = progress, autoFire = fire)
    }
}
