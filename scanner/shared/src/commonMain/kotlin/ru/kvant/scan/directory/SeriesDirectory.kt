package ru.kvant.scan.directory

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlin.math.abs

/**
 * Клиент справочника стандартных рядов (двойник backend/app/directories.py).
 *
 * Сервер раздаёт справочник через GET /v1/directories/standard_series с ETag
 * по версии; здесь — разбор документа и та же семантика подбора, что в
 * series.py: расхождение означало бы, что подсказка на устройстве и решение
 * сервера называют одну деталь разными категориями.
 *
 * Пустой ряд — явное «данных нет», и подбор по нему честно возвращает null,
 * а не правдоподобное выдуманное значение (правило пустого d1 из series.py).
 */
@Serializable
data class DirectorySection(
    val title: String = "",
    val unit: String = "",
    val values: List<Double> = emptyList(),
    val pending: String? = null,
)

@Serializable
data class SeriesDirectoryDoc(
    val directory: String,
    val version: Int,
    val sections: Map<String, DirectorySection> = emptyMap(),
)

class SeriesDirectory(val doc: SeriesDirectoryDoc) {

    val version: Int get() = doc.version

    fun values(section: String): List<Double>? = doc.sections[section]?.values

    /** Ближайшее значение ряда — новая деталь без направления износа. */
    fun nearest(section: String, measuredMm: Double): Double? =
        values(section)?.minByOrNull { abs(it - measuredMm) }

    /**
     * Вверх по ряду — б/у уплотнение: сечение кольца занижено остаточной
     * деформацией, прокладка обжата фланцем (Р-10 пп. 5.2–5.3). Значение
     * больше всего ряда упирается в максимум, как в series.py.
     */
    fun roundUp(section: String, measuredMm: Double): Double? {
        val row = values(section)?.takeIf { it.isNotEmpty() } ?: return null
        return row.firstOrNull { it >= measuredMm - EPS } ?: row.last()
    }

    /** Вниз по ряду — изношенное отверстие (Р-08 п. 3.3). */
    fun roundDown(section: String, measuredMm: Double): Double? {
        val row = values(section)?.takeIf { it.isNotEmpty() } ?: return null
        return row.lastOrNull { it <= measuredMm + EPS } ?: row.first()
    }

    companion object {
        private const val EPS = 1e-9
        private val json = Json { ignoreUnknownKeys = true }

        fun parse(text: String): SeriesDirectory =
            SeriesDirectory(json.decodeFromString(SeriesDirectoryDoc.serializer(), text))
    }
}

/**
 * Хранилище актуального справочника на устройстве.
 *
 * Протокол синхронизации: запрос с If-None-Match = [etag]; на 304 вызвать
 * [applyNotModified], на 200 — [apply]. Откат версии сервером не принимается:
 * технолог мог опубликовать v3, пока устройство лежало офлайн с v2, и пришедший
 * из кэша прокси v1 не должен затирать более свежие таблицы.
 */
class DirectoryStore(
    initial: SeriesDirectory? = null,
    initialEtag: String? = null,
) {
    var current: SeriesDirectory? = initial
        private set
    var etag: String? = initialEtag
        private set

    /** 200 от сервера. Возвращает true, если справочник обновился. */
    fun apply(newEtag: String?, body: String): Boolean {
        val parsed = SeriesDirectory.parse(body)
        val existing = current
        if (existing != null && parsed.version < existing.version) return false
        current = parsed
        etag = newEtag
        return true
    }

    /** 304 от сервера: версия на устройстве актуальна. */
    fun applyNotModified() = Unit
}
