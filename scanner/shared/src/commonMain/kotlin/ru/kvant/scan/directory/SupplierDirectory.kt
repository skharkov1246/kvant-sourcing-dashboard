package ru.kvant.scan.directory

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * Подсказки поставщиков в форме трассировки (docs/09 §9.5).
 *
 * Справочник suppliers приезжает снапшотом из CRM тем же каналом, что
 * остальные (GET /v1/directories/suppliers, ETag): оператор ВЫБИРАЕТ
 * контрагента, а не печатает название с опечатками — заявленная
 * трассировка сразу ссылается на реального поставщика (ref = id в CRM).
 */
@Serializable
data class Supplier(
    val name: String,
    val inn: String? = null,
    val ref: String? = null,
)

@Serializable
private data class SupplierSection(val values: List<Supplier> = emptyList())

@Serializable
private data class SupplierDoc(
    val version: Int = 0,
    val sections: Map<String, SupplierSection> = emptyMap(),
)

class SupplierDirectory(val version: Int, private val suppliers: List<Supplier>) {

    fun all(): List<Supplier> = suppliers

    /**
     * Поиск для поля формы: подстрока без учёта регистра по названию и ИНН.
     * Пустой запрос — первые [limit] (форма показывает список сразу).
     */
    fun search(query: String, limit: Int = 10): List<Supplier> {
        val needle = query.trim().lowercase()
        val matched = if (needle.isEmpty()) suppliers else suppliers.filter {
            it.name.lowercase().contains(needle) || (it.inn?.contains(needle) == true)
        }
        return matched.take(limit)
    }

    companion object {
        private val json = Json { ignoreUnknownKeys = true }

        fun parse(text: String): SupplierDirectory {
            val doc = json.decodeFromString(SupplierDoc.serializer(), text)
            return SupplierDirectory(
                version = doc.version,
                suppliers = doc.sections["suppliers"]?.values ?: emptyList(),
            )
        }
    }
}
