package ru.kvant.scan.crowd

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * aHash против главного вектора фрода при оплате за скан: пересъёмка того
 * же кадра узнаётся, другое освещение не спасает, разные детали различимы.
 */
class PerceptualHashTest {

    /** Детерминированный «кадр»: диагональный градиент с пятном. */
    private fun frame(width: Int = 64, height: Int = 64, spotX: Int = 16): IntArray =
        IntArray(width * height) { i ->
            val x = i % width
            val y = i / width
            val base = (x + y) * 255 / (width + height)
            val spot = if (x in spotX..(spotX + 8) && y in 20..28) 200 else 0
            (base + spot).coerceAtMost(255)
        }

    @Test
    fun `тот же кадр - тот же хеш из 16 hex-символов`() {
        val a = PerceptualHash.aHash(frame(), 64, 64)
        val b = PerceptualHash.aHash(frame(), 64, 64)
        assertEquals(a, b)
        assertEquals(16, a.length)
        assertTrue(a.all { it in "0123456789abcdef" }, a)
    }

    @Test
    fun `равномерный сдвиг яркости не меняет хеш - другое освещение не обманет дедуп`() {
        val dark = frame()
        val bright = IntArray(dark.size) { (dark[it] + 40).coerceAtMost(255) }
        // без клиппинга в этом кадре: максимум base+spot заведомо < 215
        assertEquals(
            PerceptualHash.aHash(dark, 64, 64),
            PerceptualHash.aHash(bright, 64, 64),
        )
    }

    @Test
    fun `другой кадр - большая дистанция, тот же кадр - нулевая`() {
        val a = PerceptualHash.aHash(frame(spotX = 16), 64, 64)
        val b = PerceptualHash.aHash(
            IntArray(64 * 64) { i -> if ((i % 64) < 32) 230 else 20 }, 64, 64)
        assertEquals(0, PerceptualHash.hammingDistance(a, a))
        assertTrue(PerceptualHash.hammingDistance(a, b) > 10,
            "разные кадры должны различаться: ${PerceptualHash.hammingDistance(a, b)}")
    }

    @Test
    fun `лёгкий шум - малая дистанция, узнаваемость сохраняется`() {
        val clean = frame()
        val noisy = clean.copyOf()
        for (i in noisy.indices step 97) {  // ~1% пикселей
            noisy[i] = (noisy[i] + 25).coerceAtMost(255)
        }
        val d = PerceptualHash.hammingDistance(
            PerceptualHash.aHash(clean, 64, 64),
            PerceptualHash.aHash(noisy, 64, 64),
        )
        assertTrue(d <= 4, "лёгкий шум не должен разваливать хеш: d=$d")
    }
}
