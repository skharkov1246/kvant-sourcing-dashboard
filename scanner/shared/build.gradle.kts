// Общее ядро Android и iOS (ADR-0001).
//
// Здесь живёт только то, что обязано вести себя одинаково на обеих платформах:
// домен, интерпретатор протокола съёмки, outbox, движок синхронизации, проекция
// сессии. Ничего платформенного — доступ к камере, ARCore/ARKit и ML идёт через
// интерфейсы, реализуемые нативно.

plugins {
    kotlin("multiplatform")
    kotlin("plugin.serialization")
    id("com.android.library")
    id("app.cash.sqldelight")
}

kotlin {
    androidTarget()
    // Статический фреймворк, чтобы iOS-приложение подключалось без лишней обвязки.
    listOf(iosX64(), iosArm64(), iosSimulatorArm64()).forEach { target ->
        target.binaries.framework {
            baseName = "shared"
            isStatic = true
        }
    }

    sourceSets {
        val commonMain by getting {
            dependencies {
                implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")
                implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.9.0")
                implementation("org.jetbrains.kotlinx:kotlinx-datetime:0.6.1")
                implementation("io.ktor:ktor-client-core:3.0.1")
                implementation("io.ktor:ktor-client-content-negotiation:3.0.1")
                implementation("io.ktor:ktor-serialization-kotlinx-json:3.0.1")
                implementation("app.cash.sqldelight:runtime:2.0.2")
            }
        }
        val commonTest by getting {
            dependencies {
                implementation(kotlin("test"))
                implementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.9.0")
            }
        }
        val androidMain by getting {
            dependencies {
                implementation("io.ktor:ktor-client-okhttp:3.0.1")
                implementation("app.cash.sqldelight:android-driver:2.0.2")
                // SQLCipher: локальная БД шифруется, ключ — в Android Keystore (§07.5).
                implementation("net.zetetic:sqlcipher-android:4.6.1")
            }
        }
        val iosMain by creating {
            dependencies {
                implementation("io.ktor:ktor-client-darwin:3.0.1")
                implementation("app.cash.sqldelight:native-driver:2.0.2")
            }
        }
    }
}

android {
    namespace = "ru.kvant.scan.shared"
    compileSdk = 35
    defaultConfig {
        // Ниже 26 не имеет смысла: ARCore Depth API, современный CameraX и
        // аппаратное хранилище ключей всё равно недоступны (см. §05.8).
        minSdk = 26
    }
}

sqldelight {
    databases {
        create("ScanDatabase") {
            packageName.set("ru.kvant.scan.data")
        }
    }
}
