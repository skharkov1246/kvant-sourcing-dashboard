package ru.kvant.scan.trace

import ru.kvant.scan.directory.Supplier
import ru.kvant.scan.directory.SupplierDirectory

/**
 * Форма заявленной трассировки с устройства (docs/09 §9.5) — одна логика
 * на обе платформы, UI (Compose / SwiftUI) — тонкая обёртка.
 *
 * Три поля: поставщик (выбор из подсказок CRM-справочника, не свободный
 * ввод), тип кода, значение кода. Уровень доверия форма не знает вовсе —
 * его ставит сервер по роли (I-7), см. KtorSyncTransport.addTraceLink.
 */
data class TraceLinkDraft(
    val itemId: String,
    val codeType: String,
    val codeValue: String,
    val supplierName: String? = null,
    val supplierInn: String? = null,
    val supplierRef: String? = null,
)

class TraceLinkForm(
    private val suppliers: () -> SupplierDirectory?,
    private val send: suspend (TraceLinkDraft) -> Boolean,
) {
    /** Типы кодов, которые сервер считает идентичностью карточки. */
    val codeTypes: List<String> = listOf("gtin", "serial", "batch")

    var codeType: String = codeTypes.first()
        private set
    var codeValue: String = ""
        private set
    var supplierQuery: String = ""
        private set
    var selectedSupplier: Supplier? = null
        private set

    fun setCodeType(type: String) {
        require(type in codeTypes) { "Неизвестный тип кода: $type" }
        codeType = type
    }

    fun setCodeValue(value: String) {
        codeValue = value
    }

    /**
     * Правка текста поля поставщика снимает сделанный выбор: заявка либо
     * ссылается на контрагента из справочника целиком (name+inn+ref), либо
     * не называет его вовсе — «наполовину выбранного» поставщика не бывает.
     */
    fun editSupplierQuery(query: String) {
        supplierQuery = query
        if (selectedSupplier?.name != query) selectedSupplier = null
    }

    fun selectSupplier(supplier: Supplier) {
        selectedSupplier = supplier
        supplierQuery = supplier.name
    }

    /** Подсказки под полем; без снапшота справочника — пустой список. */
    fun suggestions(limit: Int = 10): List<Supplier> =
        suppliers()?.search(supplierQuery, limit) ?: emptyList()

    /** Поставщик опционален (код без контрагента — валидная заявка), код — нет. */
    fun canSubmit(): Boolean = codeValue.isNotBlank()

    suspend fun submit(itemId: String): Boolean {
        check(canSubmit()) { "Значение кода не заполнено" }
        return send(
            TraceLinkDraft(
                itemId = itemId,
                codeType = codeType,
                codeValue = codeValue.trim(),
                supplierName = selectedSupplier?.name,
                supplierInn = selectedSupplier?.inn,
                supplierRef = selectedSupplier?.ref,
            )
        )
    }
}
