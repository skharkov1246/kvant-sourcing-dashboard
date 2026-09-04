package ru.kvant.scan

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import ru.kvant.scan.domain.AccuracyClass
import ru.kvant.scan.domain.StepResult
import ru.kvant.scan.domain.StepStatus
import ru.kvant.scan.protocol.CaptureProtocol
import ru.kvant.scan.protocol.Condition
import ru.kvant.scan.protocol.ProtocolInterpreter
import ru.kvant.scan.protocol.ProtocolStatus

/**
 * Общий набор случаев интерпретатора протокола.
 *
 * ЭТИ ЖЕ случаи продублированы в `backend/tests/test_protocol.py`.
 * Клиент решает, какой шаг показать; сервер — принимать ли сессию
 * автоматически. Если два интерпретатора разойдутся, кладовщик и диспетчер
 * увидят разные данные — поэтому наборы должны меняться синхронно.
 */
class ProtocolInterpreterTest {

    private fun completed(
        stepId: String,
        data: JsonObject = JsonObject(emptyMap()),
        quality: Double? = null,
    ) = StepResult(
        stepId = stepId,
        status = StepStatus.COMPLETED,
        payload = data,
        qualityScore = quality,
    )

    private fun interpreter(
        steps: List<ru.kvant.scan.protocol.CaptureStep> = emptyList(),
        acceptance: ru.kvant.scan.protocol.Acceptance? = null,
        deviceMax: AccuracyClass = AccuracyClass.B,
    ) = ProtocolInterpreter(
        CaptureProtocol(
            schemaVersion = 1,
            code = "test",
            version = 1,
            status = ProtocolStatus.ACTIVE,
            scope = "global",
            title = "Тестовый протокол",
            steps = steps,
            acceptance = acceptance,
        ),
        deviceMax,
    )

    // ── Порядок классов точности ─────────────────────────────────────────────

    @Test
    fun `класс A лучше класса C`() {
        assertTrue(AccuracyClass.A.atLeast(AccuracyClass.C))
        assertTrue(AccuracyClass.B.atLeast(AccuracyClass.B))
        assertFalse(AccuracyClass.D.atLeast(AccuracyClass.B))
    }

    // ── Условия ──────────────────────────────────────────────────────────────

    @Test
    fun `отсутствующий шаг удовлетворяет только exists false`() {
        val ctx = ProtocolInterpreter.Context(results = emptyMap())
        val i = interpreter()
        assertFalse(
            i.evaluate(
                Condition.StepPredicate("supplier", field = "has_damage", eq = JsonPrimitive(true)),
                ctx,
            )
        )
        assertTrue(i.evaluate(Condition.StepPredicate("supplier", exists = false), ctx))
    }

    @Test
    fun `путь разбирается по точкам внутри payload`() {
        val payload = buildJsonObject {
            put("parsed", buildJsonObject { put("gtin", JsonPrimitive("04607034171234")) })
        }
        val ctx = ProtocolInterpreter.Context(results = mapOf("label" to completed("label", payload)))
        val i = interpreter()
        assertTrue(i.evaluate(Condition.StepPredicate("label", field = "parsed.gtin", exists = true), ctx))
        assertFalse(i.evaluate(Condition.StepPredicate("label", field = "parsed.serial", exists = true), ctx))
    }

    @Test
    fun `строка true из формы совпадает с булевым true из протокола`() {
        val payload = buildJsonObject { put("has_damage", JsonPrimitive("true")) }
        val ctx = ProtocolInterpreter.Context(results = mapOf("supplier" to completed("supplier", payload)))
        assertTrue(
            interpreter().evaluate(
                Condition.StepPredicate("supplier", field = "has_damage", eq = JsonPrimitive(true)),
                ctx,
            )
        )
    }

    @Test
    fun `логические операторы`() {
        val ctx = ProtocolInterpreter.Context(
            results = mapOf("a" to completed("a"), "b" to completed("b"))
        )
        val i = interpreter()
        assertTrue(
            i.evaluate(
                Condition.All(listOf(Condition.StepPredicate("a"), Condition.StepPredicate("b"))),
                ctx,
            )
        )
        assertTrue(
            i.evaluate(
                Condition.Any(listOf(Condition.StepPredicate("a"), Condition.StepPredicate("zzz"))),
                ctx,
            )
        )
        assertTrue(i.evaluate(Condition.Not(Condition.StepPredicate("zzz")), ctx))
    }

    @Test
    fun `предикат по классу точности требует записанного обмера`() {
        val results = mapOf("dims" to completed("dims"))
        val without = ProtocolInterpreter.Context(results = results)
        val with = ProtocolInterpreter.Context(
            results = results,
            measurementAccuracy = mapOf("dims" to AccuracyClass.A),
        )
        val predicate = Condition.StepPredicate("dims", accuracyClassAtLeast = AccuracyClass.B)
        assertFalse(interpreter().evaluate(predicate, without))
        assertTrue(interpreter().evaluate(predicate, with))
    }

    @Test
    fun `шаг без оценки качества считается идеальным`() {
        // Иначе необязательные шаги несправедливо тянули бы сессию вниз.
        val ctx = ProtocolInterpreter.Context(
            results = mapOf(
                "a" to completed("a", quality = 0.9),
                "b" to completed("b"),
            )
        )
        assertEquals(0.95, ctx.qualityScore, absoluteTolerance = 1e-9)
    }

    // ── Видимость шагов ──────────────────────────────────────────────────────

    @Test
    fun `условный шаг скрыт пока флаг не выставлен`() {
        val damageStep = ru.kvant.scan.protocol.CaptureStep(
            id = "damage",
            kind = ru.kvant.scan.protocol.StepKind.PHOTO,
            title = "Фото повреждений",
            visibleIf = Condition.StepPredicate("supplier", field = "has_damage", eq = JsonPrimitive(true)),
        )
        val i = interpreter(steps = listOf(damageStep))

        val hidden = ProtocolInterpreter.Context(
            results = mapOf(
                "supplier" to completed("supplier", buildJsonObject { put("has_damage", JsonPrimitive(false)) })
            )
        )
        assertTrue(i.visibleSteps(hidden).isEmpty())

        val shown = ProtocolInterpreter.Context(
            results = mapOf(
                "supplier" to completed("supplier", buildJsonObject { put("has_damage", JsonPrimitive(true)) })
            )
        )
        assertEquals(listOf("damage"), i.visibleSteps(shown).map { it.id })
    }

    // ── Совместимость ────────────────────────────────────────────────────────

    @Test
    fun `более новый формат документа не исполняется`() {
        val protocol = CaptureProtocol(
            schemaVersion = 99,
            code = "test", version = 1,
            status = ProtocolStatus.ACTIVE, scope = "global",
            title = "Из будущего", steps = emptyList(),
        )
        assertTrue(protocol.compatibility() is ru.kvant.scan.protocol.Compatibility.RequiresAppUpdate)
    }

    @Test
    fun `неизвестный обязательный шаг блокирует, необязательный - нет`() {
        val unknownRequired = ru.kvant.scan.protocol.CaptureStep(
            id = "hologram", kind = null, title = "Голограмма", required = true,
        )
        val unknownOptional = unknownRequired.copy(required = false)

        val blocking = CaptureProtocol(
            schemaVersion = 1, code = "t", version = 1,
            status = ProtocolStatus.ACTIVE, scope = "global",
            title = "T", steps = listOf(unknownRequired),
        )
        val tolerable = blocking.copy(steps = listOf(unknownOptional))

        assertTrue(blocking.compatibility() is ru.kvant.scan.protocol.Compatibility.RequiresAppUpdate)
        assertTrue(tolerable.compatibility() is ru.kvant.scan.protocol.Compatibility.PartiallySupported)
    }
}
