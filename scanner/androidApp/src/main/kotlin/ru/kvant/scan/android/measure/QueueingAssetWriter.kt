package ru.kvant.scan.android.measure

import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.YuvImage
import android.media.Image
import ru.kvant.scan.sync.MediaQueue
import ru.kvant.scan.sync.PendingMedia
import java.io.File
import java.io.FileOutputStream
import java.util.UUID

/**
 * Боевая реализация [AssetWriter]: кадры на диск + запись в очередь медиа.
 *
 * Прямой загрузки отсюда нет намеренно — съёмка не ждёт сеть (§04.1):
 * файл ложится в приватный каталог приложения, запись встаёт в
 * [MediaQueue], загрузку двигает фоновый воркер (MediaWorker поверх
 * WorkManager). Файл принадлежит очереди до подтверждения сервера —
 * удаление только через deletablePaths() (инвариант I-1).
 *
 * Возвращаемые идентификаторы — локальные UUID: серверный asset_id
 * появится при загрузке, событие asset.attached свяжет их на сервере
 * по sha256 (§04.2).
 */
class QueueingAssetWriter(
    private val queue: MediaQueue,
    private val sessionId: String,
    /** Текущий шаг задаёт хост-экран: один writer живёт всю сессию. */
    private val currentStepId: () -> String,
    private val storageDir: File,
    private val jpegQuality: Int = 92,
) : AssetWriter {

    override suspend fun persist(frames: List<ArFrame>, includeDepth: Boolean): List<String> {
        val ids = mutableListOf<String>()
        for (frame in frames) {
            val localId = UUID.randomUUID().toString()
            val jpeg = File(storageDir, "$localId.jpg")
            writeJpeg((frame.image as ArCameraImage).image, jpeg)
            queue.enqueue(PendingMedia(
                localPath = jpeg.absolutePath,
                sessionId = sessionId,
                stepId = currentStepId(),
                kind = "photo",
                mime = "image/jpeg",
            ))
            ids += localId

            if (includeDepth && frame.depthPoints.isNotEmpty()) {
                // Сырьё глубины — построчный CSV (u;v;confidence;x;y;z в мм):
                // через год модель станет точнее и габариты пересчитаются
                // по архиву (ADR-0008); формат читается без спецбиблиотек.
                val cloud = File(storageDir, "$localId.depth.csv")
                cloud.bufferedWriter().use { out ->
                    frame.depthPoints.forEach { p ->
                        out.appendLine(
                            "${p.u};${p.v};${p.confidence};" +
                                "${p.world.x};${p.world.y};${p.world.z}")
                    }
                }
                queue.enqueue(PendingMedia(
                    localPath = cloud.absolutePath,
                    sessionId = sessionId,
                    stepId = currentStepId(),
                    kind = "point_cloud",
                    mime = "text/csv",
                ))
            }
        }
        return ids
    }

    /** YUV_420_888 (CPU-кадр ARCore) → NV21 → JPEG. */
    private fun writeJpeg(image: Image, target: File) {
        val nv21 = yuv420ToNv21(image)
        val yuv = YuvImage(nv21, ImageFormat.NV21, image.width, image.height, null)
        FileOutputStream(target).use { out ->
            yuv.compressToJpeg(Rect(0, 0, image.width, image.height), jpegQuality, out)
        }
    }

    private fun yuv420ToNv21(image: Image): ByteArray {
        val width = image.width
        val height = image.height
        val out = ByteArray(width * height * 3 / 2)

        val y = image.planes[0]
        var pos = 0
        val yBuffer = y.buffer
        for (row in 0 until height) {
            yBuffer.position(row * y.rowStride)
            if (y.pixelStride == 1) {
                yBuffer.get(out, pos, width)
                pos += width
            } else {
                for (col in 0 until width) {
                    out[pos++] = yBuffer.get(row * y.rowStride + col * y.pixelStride)
                }
            }
        }

        // NV21 хочет чередование VU; в YUV_420_888 U и V — отдельные плоскости
        // с шагом pixelStride (обычно 2 при полуплоскостном формате).
        val u = image.planes[1]
        val v = image.planes[2]
        val chromaHeight = height / 2
        val chromaWidth = width / 2
        for (row in 0 until chromaHeight) {
            for (col in 0 until chromaWidth) {
                out[pos++] = v.buffer.get(row * v.rowStride + col * v.pixelStride)
                out[pos++] = u.buffer.get(row * u.rowStride + col * u.pixelStride)
            }
        }
        return out
    }
}
