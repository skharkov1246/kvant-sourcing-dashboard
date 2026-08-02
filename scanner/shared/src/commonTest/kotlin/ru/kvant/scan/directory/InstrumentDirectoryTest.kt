package ru.kvant.scan.directory

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** Офлайн-отсечка СИ: нет поверки — нет замера (Р-08). */
class InstrumentDirectoryTest {

    private val doc = """
        {"directory": "si_directory", "version": 1,
         "sections": {"instruments": {"values": [
            "Микрометр МК-25 №Б0001, поверка до 2027-01",
            "Нутромер НИ-18-50 №В0001, поверка до 2026-12",
            "Штангенциркуль без данных о поверке"
         ]}}}
    """.trimIndent()

    private val directory = InstrumentDirectory.parse(doc)

    @Test
    fun `срок поверки разбирается из строки`() {
        assertEquals("2027-01",
            directory.all().first { "МК-25" in it.label }.validThrough)
    }

    @Test
    fun `фильтр формы отдаёт только непросроченные на дату съёмки`() {
        // В 2026-12 нутромер ещё валиден (включительно), в 2027-01 — уже нет.
        assertTrue(directory.validOn("2026-12").any { "НИ-18-50" in it })
        assertFalse(directory.validOn("2027-01").any { "НИ-18-50" in it })
        assertTrue(directory.validOn("2027-01").any { "МК-25" in it })
    }

    @Test
    fun `строка без срока поверки не проходит фильтр никогда`() {
        assertFalse(directory.validOn("2020-01").any { "без данных" in it })
        assertEquals(false, directory.isValid("Штангенциркуль без данных о поверке", "2020-01"))
    }

    @Test
    fun `неизвестное СИ - null, решение за вызывающим`() {
        assertNull(directory.isValid("Линейка из канцелярии", "2026-08"))
    }

    @Test
    fun `просроченная поверка - false`() {
        assertEquals(false,
            directory.isValid("Нутромер НИ-18-50 №В0001, поверка до 2026-12", "2027-06"))
        assertEquals(true,
            directory.isValid("Нутромер НИ-18-50 №В0001, поверка до 2026-12", "2026-11"))
    }
}
