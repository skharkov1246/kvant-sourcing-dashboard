package ru.kvant.scan.directory

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/** Подсказки поставщиков: выбор из CRM вместо свободного ввода с опечатками. */
class SupplierDirectoryTest {

    private val doc = """
        {"directory": "suppliers", "version": 3,
         "sections": {"suppliers": {"values": [
            {"name": "ООО Уплотнитель", "inn": "7701234567", "ref": "101"},
            {"name": "Ningbo Seals Co", "ref": "102"},
            {"name": "ООО УплотнительПлюс", "inn": "7709998887", "ref": "103"}
         ]}}}
    """.trimIndent()

    private val directory = SupplierDirectory.parse(doc)

    @Test
    fun `поиск по подстроке названия без учёта регистра`() {
        val found = directory.search("уплотн")
        assertEquals(listOf("ООО Уплотнитель", "ООО УплотнительПлюс"), found.map { it.name })
    }

    @Test
    fun `поиск по ИНН`() {
        assertEquals("ООО УплотнительПлюс", directory.search("770999").single().name)
    }

    @Test
    fun `пустой запрос - первые limit для мгновенного списка формы`() {
        assertEquals(3, directory.search("").size)
        assertEquals(2, directory.search("", limit = 2).size)
    }

    @Test
    fun `выбранный поставщик несёт ref на CRM - трассировка ссылается на реального контрагента`() {
        val supplier = directory.search("Ningbo").single()
        assertEquals("102", supplier.ref)
        assertEquals(null, supplier.inn)
    }

    @Test
    fun `версия снапшота доступна для ETag-логики`() {
        assertTrue(directory.version == 3)
    }
}
