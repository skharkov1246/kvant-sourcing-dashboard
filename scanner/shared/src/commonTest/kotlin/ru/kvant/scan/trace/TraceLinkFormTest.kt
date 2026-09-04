package ru.kvant.scan.trace

import kotlinx.coroutines.test.runTest
import ru.kvant.scan.directory.SupplierDirectory
import ru.kvant.scan.directory.SupplierDirectoryStore
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * Логика формы трассировки — общая для Compose и SwiftUI: подсказки из
 * справочника CRM, выбор целиком (не «наполовину выбранный» контрагент),
 * заявка без уровня доверия.
 */
class TraceLinkFormTest {

    private val directory = SupplierDirectory.parse(
        """
        {"directory": "suppliers", "version": 3,
         "sections": {"suppliers": {"values": [
            {"name": "ООО Уплотнитель", "inn": "7701234567", "ref": "101"},
            {"name": "Ningbo Seals Co", "ref": "102"}
         ]}}}
        """.trimIndent()
    )

    private var sent: TraceLinkDraft? = null

    private fun form(available: Boolean = true) = TraceLinkForm(
        suppliers = { if (available) directory else null },
        send = { draft -> sent = draft; true },
    )

    @Test
    fun `подсказки сужаются по запросу - и по названию и по ИНН`() {
        val form = form()
        form.editSupplierQuery("770123")
        assertEquals(listOf("ООО Уплотнитель"), form.suggestions().map { it.name })
    }

    @Test
    fun `без снапшота справочника подсказок нет - но форма работает`() = runTest {
        val form = form(available = false)
        assertTrue(form.suggestions().isEmpty())
        form.setCodeValue("SN-1")
        assertTrue(form.submit("itm-x"))
        assertNull(sent!!.supplierName)
    }

    @Test
    fun `правка текста после выбора снимает поставщика целиком`() {
        val form = form()
        form.selectSupplier(directory.search("Ningbo").single())
        assertEquals("Ningbo Seals Co", form.supplierQuery)
        form.editSupplierQuery("Ningbo Seals C")
        assertNull(form.selectedSupplier)
    }

    @Test
    fun `пустое значение кода блокирует отправку`() = runTest {
        val form = form()
        form.setCodeValue("   ")
        assertFalse(form.canSubmit())
        assertFailsWith<IllegalStateException> { form.submit("itm-x") }
        assertNull(sent)
    }

    @Test
    fun `заявка несёт выбранного контрагента целиком и не несёт confidence`() = runTest {
        val form = form()
        form.setCodeType("serial")
        form.setCodeValue(" SN-42 ")
        form.selectSupplier(directory.search("Уплотнитель").single())
        assertTrue(form.submit("itm-s-1"))
        val draft = sent!!
        assertEquals("itm-s-1", draft.itemId)
        assertEquals("serial", draft.codeType)
        assertEquals("SN-42", draft.codeValue)  // значение нормализовано
        assertEquals("ООО Уплотнитель", draft.supplierName)
        assertEquals("7701234567", draft.supplierInn)
        assertEquals("101", draft.supplierRef)
        // Уровня доверия в черновике нет вовсе — его ставит сервер (I-7).
    }

    @Test
    fun `неизвестный тип кода отвергается формой а не сервером`() {
        assertFailsWith<IllegalArgumentException> { form().setCodeType("qr_url") }
    }

    @Test
    fun `снапшот поставщиков не откатывается устаревшим ответом`() {
        val store = SupplierDirectoryStore()
        assertTrue(store.apply("\"suppliers-v3\"", doc(version = 3)))
        assertFalse(store.apply("\"suppliers-v2\"", doc(version = 2)))
        assertEquals(3, store.current!!.version)
        assertEquals("\"suppliers-v3\"", store.etag)
    }

    private fun doc(version: Int) =
        """{"directory": "suppliers", "version": $version,
            "sections": {"suppliers": {"values": [{"name": "ООО Уплотнитель"}]}}}"""
}
