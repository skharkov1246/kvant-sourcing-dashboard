package ru.kvant.scan.brand

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * Справочник производителей (brand_directory): выбор из списка вместо
 * свободного ввода — «Сименс», «SIEMENS» и «Siemens AG» иначе станут тремя
 * разными брендами и разобьют семью артикулов одной детали.
 */
class BrandDirectory(val version: Int, private val brands: List<String>) {

    fun search(query: String, limit: Int = 10): List<String> {
        val needle = query.trim().lowercase()
        val matched = if (needle.isEmpty()) brands
        else brands.filter { it.lowercase().contains(needle) }
        return matched.take(limit)
    }

    fun contains(brand: String): Boolean = brands.any { it.equals(brand, ignoreCase = true) }

    companion object {
        /** Служебный исход Р-11 п.3: легальный ответ «не знаю». */
        const val UNKNOWN = "БРЕНД НЕ ОПРЕДЕЛЁН"

        fun parse(text: String): BrandDirectory {
            val doc = Json { ignoreUnknownKeys = true }
                .parseToJsonElement(text).jsonObject
            val values = doc["sections"]?.jsonObject?.get("brands")
                ?.jsonObject?.get("values")?.jsonArray
                ?.map { it.jsonPrimitive.content } ?: emptyList()
            return BrandDirectory(
                version = doc["version"]?.jsonPrimitive?.content?.toIntOrNull() ?: 0,
                brands = values,
            )
        }
    }
}

/** Роль имени на детали. Порядок — от изготовителя к владельцу шильдика. */
enum class BrandRole(val code: String, val title: String, val hint: String) {
    MANUFACTURER("manufacturer", "Изготовитель",
        "Имя на самой детали — тот, кто её сделал"),
    ASSEMBLY_OEM("assembly_oem", "OEM узла",
        "Имя на узле, в котором стоит деталь (насос, клапан)"),
    EQUIPMENT_OEM("equipment_oem", "OEM установки",
        "Имя на установке целиком (турбина, компрессор)"),
    PRIVATE_LABEL("private_label", "Частная марка",
        "Перемаркировка без производства"),
    DISTRIBUTOR("distributor", "Дистрибьютор",
        "Продавец под своим номером"),
    ;

    /** Уровень установки, на котором это имя обычно читается. */
    val level: String?
        get() = when (this) {
            MANUFACTURER -> "part"
            ASSEMBLY_OEM -> "assembly"
            EQUIPMENT_OEM -> "equipment"
            else -> null
        }
}

/** Одна строка формы: бренд + артикул + роль. */
data class BrandEntry(
    val brand: String,
    val article: String?,
    val role: BrandRole,
)

/**
 * Форма многоуровневого бренда на устройстве (docs/06 §6.8).
 *
 * Смысл: кладовщик не будет заполнять три уровня руками — поэтому форма
 * подтягивает распознанный шильдик и СПРАШИВАЕТ роль одним вопросом
 * («чьё это имя?»). Уровень доверия форма не знает вовсе: его ставит
 * сервер по роли пользователя (I-7).
 */
class BrandChainForm(
    private val brands: () -> BrandDirectory?,
    /** Чтение шильдика с сервера по кадру; null — плечо недоступно. */
    private val readNameplate: suspend (assetId: String) -> NameplateFields?,
    private val send: suspend (itemId: String, entries: List<BrandEntry>) -> Boolean,
) {
    var role: BrandRole = BrandRole.MANUFACTURER
        private set
    var brandQuery: String = ""
        private set
    var article: String = ""
        private set
    /** Уже добавленные строки — их видно списком до отправки. */
    val entries: MutableList<BrandEntry> = mutableListOf()
    /** Что именно подсказал шильдик — показывается человеку для сверки. */
    var prefillNote: String? = null
        private set

    fun setRole(next: BrandRole) { role = next }
    fun setBrand(value: String) { brandQuery = value }
    fun setArticle(value: String) { article = value }

    fun brandSuggestions(limit: Int = 8): List<String> =
        brands()?.search(brandQuery, limit) ?: emptyList()

    /**
     * Подстановка из распознанного шильдика. НЕ отправляет ничего сама:
     * человек видит, что подставилось, и правит — по этим полям заказывают
     * детали за деньги.
     */
    suspend fun prefillFromNameplate(assetId: String): Boolean {
        val fields = readNameplate(assetId) ?: run {
            prefillNote = "Распознавание недоступно — заполните вручную"
            return false
        }
        if (!fields.legible) {
            prefillNote = "Шильдик нечитаем — переснимите с боковым светом " +
                "или заполните вручную"
            return false
        }
        fields.brand?.let { brandQuery = it }
        (fields.article ?: fields.modelDesignation)?.let { article = it }
        prefillNote = "Подставлено с шильдика — проверьте перед отправкой"
        return true
    }

    /** Бренд обязателен, артикул — нет: имя без номера тоже след. */
    fun canAdd(): Boolean = brandQuery.isNotBlank()

    fun addEntry() {
        check(canAdd()) { "Бренд не заполнен" }
        entries += BrandEntry(brandQuery.trim(), article.trim().ifBlank { null }, role)
        brandQuery = ""
        article = ""
        prefillNote = null
    }

    /** Роли, которые ещё не заполнены — подсказка «что осталось спросить». */
    fun missingRoles(): List<BrandRole> {
        val filled = entries.map { it.role }.toSet()
        return listOf(BrandRole.MANUFACTURER, BrandRole.ASSEMBLY_OEM,
                      BrandRole.EQUIPMENT_OEM).filterNot { it in filled }
    }

    suspend fun submit(itemId: String): Boolean {
        check(entries.isNotEmpty()) { "Нечего отправлять" }
        return send(itemId, entries.toList())
    }
}

/** Поля шильдика с сервера (ответ /v1/assets/{id}/nameplate). */
data class NameplateFields(
    val legible: Boolean,
    val brand: String?,
    val modelDesignation: String?,
    val serialNumber: String?,
    val article: String?,
    val confidence: Double,
)
