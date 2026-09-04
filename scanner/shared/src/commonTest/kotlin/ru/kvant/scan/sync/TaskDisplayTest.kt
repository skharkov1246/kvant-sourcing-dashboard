package ru.kvant.scan.sync

import kotlin.test.Test
import kotlin.test.assertEquals

/** Язык кладовщика: коды системы не должны доезжать до экрана. */
class TaskDisplayTest {

    @Test
    fun protocol_codes_become_russian_names() {
        assertEquals("Пружины", TaskDisplay.protocolName("kv_cat_b"))
        assertEquals("Съёмка детали", TaskDisplay.protocolName("kv_router"))
        assertEquals("Приёмка паллеты", TaskDisplay.protocolName("pallet_general"))
    }

    @Test
    fun unknown_protocol_stays_as_is() {
        assertEquals("kv_future_x", TaskDisplay.protocolName("kv_future_x"))
    }

    @Test
    fun iso_date_becomes_human() {
        assertEquals("8 августа", TaskDisplay.humanDate("2026-08-08"))
        assertEquals("1 января", TaskDisplay.humanDate("2027-01-01T10:00:00Z"))
    }

    @Test
    fun garbage_date_stays_as_is() {
        assertEquals("завтра", TaskDisplay.humanDate("завтра"))
        assertEquals("2026-13-08", TaskDisplay.humanDate("2026-13-08"))
    }

    @Test
    fun subject_fields_translate() {
        assertEquals("Заказ", TaskDisplay.fieldLabel("title"))
        assertEquals("Стадия", TaskDisplay.fieldLabel("stage_id"))
        assertEquals("custom_field", TaskDisplay.fieldLabel("custom_field"))
        assertEquals("Нужна съёмка", TaskDisplay.fieldValue("stage_id", "NEED_SCAN"))
        assertEquals("RFQ_356 / FRISTAM", TaskDisplay.fieldValue("title", "RFQ_356 / FRISTAM"))
    }
}
