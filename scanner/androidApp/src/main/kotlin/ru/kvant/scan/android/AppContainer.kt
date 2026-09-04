package ru.kvant.scan.android

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import ru.kvant.scan.capture.CaptureSessionController
import ru.kvant.scan.capture.CaptureSessionFactory
import ru.kvant.scan.sync.LocalTask
import ru.kvant.scan.sync.SyncConfig
import ru.kvant.scan.sync.SyncStack
import java.util.UUID

/**
 * Граф зависимостей приложения — тонкая обёртка над общим [SyncStack]
 * (двойник — AppContainer в KvantScanApp.swift). Здесь только то, что
 * обязано быть платформенным: идентификатор устройства и адрес дев-стенда
 * (10.0.2.2 — loopback хоста из эмулятора Android).
 */
/** Единственный экземпляр графа: активити и фоновые воркеры WorkManager
 * обязаны видеть ОДНУ очередь медиа, а не каждый свою. */
object AppGraph {
    val container: AppContainer by lazy { AppContainer() }
}

class AppContainer(
    // Живой стенд. Для локальной отладки на эмуляторе — "http://10.0.2.2:8077".
    baseUrl: String = "https://89.23.102.183.nip.io",
    tenantId: String = "t-internal",
    userId: String = "u-operator",
) {
    val stack = SyncStack(SyncConfig(
        baseUrl = baseUrl,
        tenantId = tenantId,
        userId = userId,
        deviceId = "android-dev",
        capabilities = Json.parseToJsonElement(
            """{"schema_version": 1, "app_version": "0.1.0",
                "platform": "android", "max_accuracy_class": "B"}"""
        ).jsonObject,
    ))

    /** Сессия по заданию: протокол из локального TaskStore (фабрика ядра). */
    suspend fun captureController(task: LocalTask): CaptureSessionController =
        CaptureSessionFactory.forTask(
            task = task,
            tasks = stack.tasks,
            sessionId = "s-" + UUID.randomUUID(),
            outbox = stack.outbox,
            clock = stack::now,
            // UUIDv4 совместим с сервером (идемпотентность — по значению);
            // сортируемый v7-генератор придёт вместе с SQLDelight-слоем.
            newEventId = { UUID.randomUUID().toString() },
        )

    /** Инициативная сессия без задания: кладовщик наполняет базу сам. */
    suspend fun initiativeCaptureController(): CaptureSessionController =
        CaptureSessionFactory.initiative(
            tasks = stack.tasks,
            sessionId = "s-" + UUID.randomUUID(),
            outbox = stack.outbox,
            clock = stack::now,
            newEventId = { UUID.randomUUID().toString() },
        )
}
