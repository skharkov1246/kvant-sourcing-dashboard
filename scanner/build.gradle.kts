// Версии плагинов объявлены здесь один раз; модули подключают их без версий.
// Сборка Android/iOS требует Android SDK / Xcode. Для машин без них есть
// jvm-check/ — автономная JVM-компиляция общего ядра с прогоном его тестов
// (правило CLAUDE.md «тестируй до пуша» работает на любой машине).
plugins {
    kotlin("multiplatform") version "2.0.21" apply false
    kotlin("android") version "2.0.21" apply false
    kotlin("plugin.serialization") version "2.0.21" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.21" apply false
    id("com.android.application") version "8.7.3" apply false
    id("com.android.library") version "8.7.3" apply false
    id("app.cash.sqldelight") version "2.0.2" apply false
}
