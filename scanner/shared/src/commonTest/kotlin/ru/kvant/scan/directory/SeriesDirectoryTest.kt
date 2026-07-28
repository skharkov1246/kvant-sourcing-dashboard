package ru.kvant.scan.directory

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * Двойник семантики series.py: правила «вверх для б/у уплотнений, вниз для
 * изношенных отверстий» на устройстве обязаны совпадать с сервером.
 */
class SeriesDirectoryTest {

    // Уменьшенная копия структуры series_directory.json: та же схема,
    // значения — реальные затравки рядов.
    private val doc = """
        {
          "directory": "standard_series",
          "version": 2,
          "sections": {
            "oring_d2_iso3601": {
              "title": "d2 ISO 3601", "unit": "mm",
              "values": [1.78, 2.62, 3.53, 5.33]
            },
            "gasket_sheet_thickness": {
              "title": "толщины листа", "unit": "mm",
              "values": [0.5, 0.8, 1.0, 1.5, 2.0, 3.0]
            },
            "oring_d1_iso3601": {
              "title": "d1 ISO 3601", "unit": "mm",
              "values": [],
              "pending": "заполняет владелец реестра"
            }
          }
        }
    """.trimIndent()

    private val directory = SeriesDirectory.parse(doc)

    @Test
    fun `б_у кольцо - сечение округляется вверх по ряду`() {
        // Р-10 п. 5.2: остаточная деформация занижает d2 — 3.4 стало из 3.53.
        assertEquals(3.53, directory.roundUp("oring_d2_iso3601", 3.4))
    }

    @Test
    fun `точное попадание в ряд не сдвигается ни вверх ни вниз`() {
        assertEquals(2.62, directory.roundUp("oring_d2_iso3601", 2.62))
        assertEquals(2.62, directory.roundDown("oring_d2_iso3601", 2.62))
    }

    @Test
    fun `значение больше всего ряда упирается в максимум`() {
        assertEquals(5.33, directory.roundUp("oring_d2_iso3601", 9.0))
    }

    @Test
    fun `новая деталь - ближайшее значение ряда`() {
        assertEquals(1.78, directory.nearest("oring_d2_iso3601", 1.9))
    }

    @Test
    fun `б_у прокладка - толщина строго вверх`() {
        // Р-10 п. 5.3: прокладка обжата, новая толще измеренной.
        assertEquals(1.5, directory.roundUp("gasket_sheet_thickness", 1.2))
    }

    @Test
    fun `пустой ряд - честный null а не выдуманное значение`() {
        assertNull(directory.roundUp("oring_d1_iso3601", 25.0))
        assertNull(directory.nearest("oring_d1_iso3601", 25.0))
    }

    @Test
    fun `неизвестная секция - null`() {
        assertNull(directory.nearest("ряд_которого_нет", 1.0))
    }
}

class DirectoryStoreTest {

    private fun docWithVersion(version: Int) = """
        {"directory": "standard_series", "version": $version,
         "sections": {"oring_d2_iso3601": {"values": [1.78]}}}
    """.trimIndent()

    @Test
    fun `свежая версия применяется и запоминает etag`() {
        val store = DirectoryStore()
        assertTrue(store.apply("\"standard_series-v1\"", docWithVersion(1)))
        assertEquals(1, store.current?.version)
        assertEquals("\"standard_series-v1\"", store.etag)
    }

    @Test
    fun `более новая версия замещает текущую`() {
        val store = DirectoryStore()
        store.apply("\"v1\"", docWithVersion(1))
        assertTrue(store.apply("\"v3\"", docWithVersion(3)))
        assertEquals(3, store.current?.version)
    }

    @Test
    fun `откат версии не принимается - устаревший кэш не затирает свежие таблицы`() {
        val store = DirectoryStore()
        store.apply("\"v3\"", docWithVersion(3))
        assertFalse(store.apply("\"v1\"", docWithVersion(1)))
        assertEquals(3, store.current?.version)
        assertEquals("\"v3\"", store.etag)
    }

    @Test
    fun `повтор той же версии - идемпотентно`() {
        val store = DirectoryStore()
        store.apply("\"v2\"", docWithVersion(2))
        assertTrue(store.apply("\"v2\"", docWithVersion(2)))
        assertEquals(2, store.current?.version)
    }
}
