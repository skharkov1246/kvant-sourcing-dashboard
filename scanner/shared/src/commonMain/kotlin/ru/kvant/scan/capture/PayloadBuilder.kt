package ru.kvant.scan.capture

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Мост для платформенных обёрток: SwiftUI-стороне неудобно собирать
 * kotlinx `JsonObject` напрямую (интероп экспортирует его как непрозрачный
 * класс), поэтому простые payload'ы форм строятся здесь. Сложные payload'ы
 * (вложенные объекты) добавляются по мере надобности отдельными фабриками —
 * не универсальным Any-конвертером, чтобы типы оставались проверяемыми.
 */
object PayloadBuilder {
    fun fromStrings(values: Map<String, String>): JsonObject =
        JsonObject(values.mapValues { (_, v) -> JsonPrimitive(v) })

    fun fromMixed(
        strings: Map<String, String> = emptyMap(),
        numbers: Map<String, Double> = emptyMap(),
        booleans: Map<String, Boolean> = emptyMap(),
    ): JsonObject = JsonObject(
        strings.mapValues { (_, v) -> JsonPrimitive(v) } +
            numbers.mapValues { (_, v) -> JsonPrimitive(v) } +
            booleans.mapValues { (_, v) -> JsonPrimitive(v) }
    )
}
