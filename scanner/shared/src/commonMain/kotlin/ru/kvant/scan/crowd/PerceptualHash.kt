package ru.kvant.scan.crowd

/**
 * Перцептивный хеш кадра (aHash 8×8) — вторая ступень дедупа крауд-сканов
 * (docs/15 §15.1): sha256 ловит тот же ФАЙЛ, phash — пересъёмку того же
 * КАДРА или фото экрана. Считается на устройстве и уезжает в заявке;
 * порог похожести (hamming-дистанция) — на стороне сервера.
 *
 * aHash сознательно прост: усреднение до 8×8, порог по среднему. Порог по
 * среднему делает хеш нечувствительным к равномерному сдвигу яркости —
 * та же деталь при другом освещении даёт тот же хеш; этого достаточно
 * против главного вектора фрода (повторная съёмка того же кадра).
 * Вход — яркость (luma), извлечение из YUV/CVPixelBuffer — забота
 * платформенных адаптеров, как и везде на границе сенсоров.
 */
object PerceptualHash {

    /** 64-битный aHash как 16 hex-символов. */
    fun aHash(luma: IntArray, width: Int, height: Int): String {
        require(width > 0 && height > 0) { "пустой кадр" }
        require(luma.size >= width * height) { "luma меньше width*height" }

        // Усреднение блоками до сетки 8×8 (грубое, но детерминированное).
        val cells = DoubleArray(64)
        for (cy in 0 until 8) {
            for (cx in 0 until 8) {
                val x0 = cx * width / 8
                val x1 = ((cx + 1) * width / 8).coerceAtLeast(x0 + 1)
                val y0 = cy * height / 8
                val y1 = ((cy + 1) * height / 8).coerceAtLeast(y0 + 1)
                var sum = 0L
                for (y in y0 until y1) {
                    for (x in x0 until x1) {
                        sum += luma[y * width + x]
                    }
                }
                cells[cy * 8 + cx] = sum.toDouble() / ((x1 - x0) * (y1 - y0))
            }
        }
        val mean = cells.average()

        var bits = 0UL
        for (i in 0 until 64) {
            if (cells[i] > mean) bits = bits or (1UL shl i)
        }
        return bits.toString(16).padStart(16, '0')
    }

    /** Hamming-дистанция двух хешей: сколько из 64 бит различаются. */
    fun hammingDistance(hexA: String, hexB: String): Int {
        val a = hexA.toULong(16)
        val b = hexB.toULong(16)
        return (a xor b).countOneBits()
    }
}
