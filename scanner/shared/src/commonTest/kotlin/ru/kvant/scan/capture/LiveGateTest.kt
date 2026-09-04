package ru.kvant.scan.capture

import kotlin.random.Random
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

/**
 * Живая проверка кадра: подсказки должны срабатывать на тех же дефектах,
 * что нашлись в реальном архиве (блик, темнота, смаз, пустой кадр).
 * Кадры синтезируются: логика не должна зависеть от платформенной камеры.
 */
class LiveGateTest {

    private val w = 80
    private val h = 60

    private fun flat(value: Int) = IntArray(w * h) { value }

    /** «Деталь»: крупные грани с фаской (шахматка 8 px) плюс лёгкий шум —
     *  по фактуре похоже на металл в кадре. seed сдвигает грани: два кадра
     *  с разным seed выглядят как сильно сдвинутая камера. */
    private fun textured(seed: Int = 1): IntArray {
        val r = Random(seed)
        return IntArray(w * h) { i ->
            val x = i % w; val y = i / w
            val block = if (((x + seed * 3) / 8 + y / 8) % 2 == 0) 100 else 165
            (block + r.nextInt(-12, 13)).coerceIn(0, 255)
        }
    }

    private fun withGlare(base: IntArray, fraction: Double): IntArray {
        val out = base.copyOf()
        val count = (out.size * fraction).toInt()
        for (i in 0 until count) out[i] = 255
        return out
    }

    @Test
    fun empty_table_asks_to_aim() {
        val m = FrameMetricsCalc.fromLuma(flat(128), w, h)
        val reading = LiveGate().onFrame(m)
        assertFalse(reading.ok)
        assertTrue(reading.hint!!.contains("Наведите"))
    }

    @Test
    fun dark_frame_asks_for_light() {
        // Тёмный, но с фактурой — чтобы не сработала подсказка «наведите».
        val luma = textured().map { it / 6 }.toIntArray()
        val m = FrameMetricsCalc.fromLuma(luma, w, h)
        val reading = LiveGate().onFrame(m)
        assertTrue(reading.hint!!.contains("Темно"), "получили: ${reading.hint}")
    }

    @Test
    fun glare_asks_to_tilt() {
        val m = FrameMetricsCalc.fromLuma(withGlare(textured(), 0.10), w, h)
        val reading = LiveGate().onFrame(m)
        assertTrue(reading.hint!!.contains("Блик"), "получили: ${reading.hint}")
    }

    @Test
    fun shaking_hands_ask_to_hold_steady() {
        val a = textured(seed = 1)
        val b = textured(seed = 2) // совсем другой кадр — сильное движение
        val m = FrameMetricsCalc.fromLuma(b, w, h, prev = a)
        val reading = LiveGate().onFrame(m)
        assertTrue(reading.hint!!.contains("ровно"), "получили: ${reading.hint}")
    }

    @Test
    fun good_frame_is_ok_and_streak_fires_auto_shutter() {
        val gate = LiveGate(stableFrames = 5)
        val m = FrameMetricsCalc.fromLuma(textured(), w, h)
        var fired = 0
        repeat(8) { if (gate.onFrame(m).autoFire) fired++ }
        assertEquals(1, fired, "автоспуск должен сработать ровно один раз")
    }

    @Test
    fun bad_frame_resets_streak() {
        val gate = LiveGate(stableFrames = 4)
        val good = FrameMetricsCalc.fromLuma(textured(), w, h)
        val dark = FrameMetricsCalc.fromLuma(flat(10), w, h)
        repeat(3) { gate.onFrame(good) }
        assertEquals(0f, gate.onFrame(dark).autoProgress)
        repeat(3) { assertFalse(gate.onFrame(good).autoFire) }
        assertTrue(gate.onFrame(good).autoFire)
    }

    @Test
    fun reset_allows_second_auto_fire_for_next_shot() {
        val gate = LiveGate(stableFrames = 2)
        val good = FrameMetricsCalc.fromLuma(textured(), w, h)
        repeat(2) { gate.onFrame(good) }
        gate.reset()
        var fired = false
        repeat(3) { if (gate.onFrame(good).autoFire) fired = true }
        assertTrue(fired)
    }

    @Test
    fun metrics_are_sane_on_textured_frame() {
        val m = FrameMetricsCalc.fromLuma(textured(), w, h)
        assertNotNull(m)
        assertTrue(m.sharpness > 6.0)
        assertTrue(m.fillFraction > 0.1)
        assertTrue(m.glareFraction < 0.02)
    }
}
