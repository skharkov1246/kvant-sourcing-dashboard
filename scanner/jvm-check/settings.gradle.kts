// Автономный проверочный проект: компилирует общее ядро как чистый JVM-модуль
// и гоняет commonTest без Android SDK и без Xcode. Нужен, потому что среда
// разработки и CI не обязаны иметь AGP: правило «тестируй до пуша» (CLAUDE.md)
// должно выполняться на любой машине.
rootProject.name = "kvant-scan-jvm-check"

pluginManagement {
    repositories {
        gradlePluginPortal()
        mavenCentral()
    }
}

dependencyResolutionManagement {
    repositories { mavenCentral() }
}
