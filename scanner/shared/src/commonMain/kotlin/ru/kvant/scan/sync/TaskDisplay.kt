package ru.kvant.scan.sync

/**
 * Перевод служебного языка системы на язык кладовщика.
 *
 * Коды протоколов, ключи полей из внешних систем и ISO-даты — рабочий язык
 * сервера, но на экране телефона им не место: человек у стеллажа должен
 * читать «Пружины · до 8 августа», а не «kv_cat_b@1 · 2026-08-08».
 * Незнакомые коды показываются как есть — честность важнее красоты.
 */
object TaskDisplay {

    private val PROTOCOL_NAMES = mapOf(
        "kv_router" to "Съёмка детали",
        "kv_cat_a" to "Стандартные покупные",
        "kv_cat_b" to "Пружины",
        "kv_cat_c" to "Тела вращения",
        "kv_cat_d" to "Корпусные детали",
        "kv_cat_e" to "Сложные поверхности",
        "kv_cat_f" to "Зубчатые детали",
        "kv_cat_g" to "Резина и пластик",
        "kv_cat_h" to "Сварные узлы",
        "kv_no_id" to "Деталь без маркировки",
        "kv_select" to "Подбор аналога",
        "kv_crowd" to "Краудскан",
        "kv_crowd_onboarding" to "Знакомство с краудсканом",
        "pallet_general" to "Приёмка паллеты",
    )

    /** Ключи subject из внешних систем → подписи для человека. */
    private val FIELD_LABELS = mapOf(
        "title" to "Заказ",
        "name" to "Название",
        "stage_id" to "Стадия",
        "company_id" to "Компания №",
        "kind" to "Тип",
        "outcome" to "Результат",
        "source_session" to "Исходная съёмка",
    )

    /** Значения стадий сделки → по-русски. */
    private val STAGE_NAMES = mapOf(
        "NEED_SCAN" to "Нужна съёмка",
        "IN_SCAN" to "Идёт съёмка",
        "SCANNED" to "Отснято",
    )

    private val MONTHS = listOf(
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    )

    /** «kv_cat_b» → «Пружины»; незнакомый код возвращается как есть. */
    fun protocolName(code: String): String = PROTOCOL_NAMES[code] ?: code

    /** «2026-08-08…» → «8 августа»; не-дата возвращается как есть. */
    fun humanDate(iso: String): String {
        val m = Regex("""^\d{4}-(\d{2})-(\d{2})""").find(iso) ?: return iso
        val month = m.groupValues[1].toIntOrNull()?.takeIf { it in 1..12 } ?: return iso
        val day = m.groupValues[2].toIntOrNull() ?: return iso
        return "$day ${MONTHS[month - 1]}"
    }

    /** Подпись поля subject; незнакомый ключ — как есть. */
    fun fieldLabel(key: String): String = FIELD_LABELS[key] ?: key

    /** Значение поля subject: известные коды стадий переводятся. */
    fun fieldValue(key: String, value: String): String =
        if (key == "stage_id") STAGE_NAMES[value] ?: value else value
}
