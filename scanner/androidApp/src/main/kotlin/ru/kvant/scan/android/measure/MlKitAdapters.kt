package ru.kvant.scan.android.measure

import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.segmentation.subject.SubjectSegmentation
import com.google.mlkit.vision.segmentation.subject.SubjectSegmenterOptions
import kotlinx.coroutines.tasks.await

/**
 * Адаптеры ML Kit к фасадам сенсорного слоя (см. Facades.kt и
 * ArCoreSessionAdapter.kt — тот же принцип: тонкая перекладка платформенного
 * API, вся решающая логика выше).
 *
 * Ориентация: кадры ARCore (CPU-изображение и карта глубины) приходят в
 * ориентации СЕНСОРА, и DepthSample.u/v посчитаны в ней же. Поэтому кадр в
 * ML Kit отдаётся с rotationDegrees = 0 — иначе маска и точки глубины
 * разъедутся на 90°, и сегментация «промахнётся» мимо объекта.
 */
class MlKitSubjectSegmenter(
    private val minForegroundConfidence: Float = 0.5f,
) : SubjectSegmenter {

    private val client = SubjectSegmentation.getClient(
        SubjectSegmenterOptions.Builder()
            .enableForegroundConfidenceMask()
            .build(),
    )

    override suspend fun segment(image: CameraImage): SegmentMask? {
        val media = (image as ArCameraImage).image
        val input = InputImage.fromMediaImage(media, 0)
        val result = client.process(input).await()
        val mask = result.foregroundConfidenceMask ?: return null

        // Буфер живёт до следующего process() — копируем, чтобы маска
        // оставалась валидной, пока движок фильтрует точки других кадров.
        val width = input.width
        val height = input.height
        val confidence = FloatArray(width * height)
        mask.rewind()
        mask.get(confidence)

        return object : SegmentMask {
            override fun contains(u: Float, v: Float): Boolean {
                val x = (u * width).toInt().coerceIn(0, width - 1)
                val y = (v * height).toInt().coerceIn(0, height - 1)
                return confidence[y * width + x] >= minForegroundConfidence
            }
        }
    }
}

/**
 * Сырой код с кадра. `code_type` — предположение адаптера по формату и
 * структуре значения; окончательный тип подтверждает шаг протокола
 * (оператор видит, что именно распозналось).
 */
data class RawCodeDetection(
    val rawValue: String,
    val format: String,
    val codeType: String,
)

/** Чтение штрих-кодов/QR/DataMatrix с кадра камеры. */
interface CodeDetector {
    suspend fun detect(image: CameraImage): List<RawCodeDetection>
}

class MlKitCodeDetector : CodeDetector {

    private val client = BarcodeScanning.getClient(
        BarcodeScannerOptions.Builder()
            .setBarcodeFormats(
                Barcode.FORMAT_EAN_13, Barcode.FORMAT_EAN_8,
                Barcode.FORMAT_UPC_A, Barcode.FORMAT_UPC_E,
                Barcode.FORMAT_CODE_128, Barcode.FORMAT_CODE_39,
                Barcode.FORMAT_QR_CODE, Barcode.FORMAT_DATA_MATRIX,
            )
            .build(),
    )

    override suspend fun detect(image: CameraImage): List<RawCodeDetection> {
        val media = (image as ArCameraImage).image
        val input = InputImage.fromMediaImage(media, 0)
        return client.process(input).await().mapNotNull { barcode ->
            val raw = barcode.rawValue ?: return@mapNotNull null
            RawCodeDetection(
                rawValue = raw,
                format = formatName(barcode.format),
                codeType = guessCodeType(barcode.format, raw),
            )
        }
    }

    private fun formatName(format: Int): String = when (format) {
        Barcode.FORMAT_EAN_13 -> "ean13"
        Barcode.FORMAT_EAN_8 -> "ean8"
        Barcode.FORMAT_UPC_A -> "upca"
        Barcode.FORMAT_UPC_E -> "upce"
        Barcode.FORMAT_CODE_128 -> "code128"
        Barcode.FORMAT_CODE_39 -> "code39"
        Barcode.FORMAT_QR_CODE -> "qr"
        Barcode.FORMAT_DATA_MATRIX -> "datamatrix"
        else -> "unknown"
    }

    /**
     * Эвристика типа кода под словарь трассировки (TraceLinkInput):
     *   * EAN/UPC — товарный номер → gtin;
     *   * Code-128 с AI 00 — логистический SSCC этикетки палеты;
     *   * DataMatrix с AI 01 и криптохвостом — «Честный знак»;
     *   * остальное — custom, тип уточнит оператор на шаге.
     * Ошибка эвристики не теряет данные: rawValue уходит в payload целиком.
     */
    private fun guessCodeType(format: Int, raw: String): String = when {
        format in GTIN_FORMATS -> "gtin"
        format == Barcode.FORMAT_CODE_128 && raw.startsWith("00") && raw.length >= 20 -> "sscc"
        format == Barcode.FORMAT_DATA_MATRIX && raw.startsWith("01") && raw.length > 30 -> "chestny_znak"
        else -> "custom"
    }

    private companion object {
        val GTIN_FORMATS = setOf(
            Barcode.FORMAT_EAN_13, Barcode.FORMAT_EAN_8,
            Barcode.FORMAT_UPC_A, Barcode.FORMAT_UPC_E,
        )
    }
}
