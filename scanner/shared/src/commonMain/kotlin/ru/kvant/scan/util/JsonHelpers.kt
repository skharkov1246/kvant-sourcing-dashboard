package ru.kvant.scan.util

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject

/** Мост для Swift: собрать JsonObject из строки (kotlinx-классы в интеропе
 * непрозрачны — см. PayloadBuilder, тот же принцип). */
object JsonHelpers {
    fun parseJsonObject(text: String): JsonObject =
        Json.parseToJsonElement(text).jsonObject
}
