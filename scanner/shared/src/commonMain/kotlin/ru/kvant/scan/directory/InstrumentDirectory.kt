package ru.kvant.scan.directory

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * Парк СИ с офлайн-отсечкой просроченной поверки (docs/14, серверная
 * дельта; двойник серверной проверки app/si_check.py).
 *
 * Формат строки справочника: «Тип Модель №номер, поверка до ГГГГ-ММ».
 * Правила честности:
 *   * строка без срока поверки НЕ проходит фильтр — нет поверки, нет замера
 *     (Р-08: замер неповеренным СИ недействителен);
 *   * сравнение дат — лексикографическое по «ГГГГ-ММ», работает офлайн без
 *     календарных библиотек;
 *   * неизвестная строка — null, решает вызывающий (сервер флажит приёмку).
 */
@Serializable
private data class SiSection(val values: List<String> = emptyList())

@Serializable
private data class SiDoc(
    val version: Int = 0,
    val sections: Map<String, SiSection> = emptyMap(),
)

data class Instrument(
    val label: String,
    /** «ГГГГ-ММ» или null — срок в строке не указан. */
    val validThrough: String?,
)

class InstrumentDirectory(val version: Int, private val instruments: List<Instrument>) {

    fun all(): List<Instrument> = instruments

    /** Список для формы: только СИ с непросроченной поверкой на дату съёмки. */
    fun validOn(yearMonth: String): List<String> =
        instruments.filter { it.validThrough != null && it.validThrough >= yearMonth }
            .map { it.label }

    /** null — строки нет в справочнике (решение за вызывающим). */
    fun isValid(label: String, yearMonth: String): Boolean? {
        val found = instruments.firstOrNull { it.label == label } ?: return null
        return found.validThrough != null && found.validThrough >= yearMonth
    }

    companion object {
        private val json = Json { ignoreUnknownKeys = true }
        private val deadline = Regex("""поверка до (\d{4}-\d{2})""")

        fun parse(text: String): InstrumentDirectory {
            val doc = json.decodeFromString(SiDoc.serializer(), text)
            val values = doc.sections["instruments"]?.values ?: emptyList()
            return InstrumentDirectory(
                version = doc.version,
                instruments = values.map {
                    Instrument(it, deadline.find(it)?.groupValues?.get(1))
                },
            )
        }
    }
}
