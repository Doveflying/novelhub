// 顶层构建脚本：只声明插件版本，不在这里 apply。
//
// Chaquopy 16.1.0 要求 Android Gradle Plugin 在 7.0.x ~ 8.13.x 之间，
// 这里用 8.7.2（与 Gradle 8.9 搭配，本机已有 gradle-8.9-all 缓存）。
plugins {
    id("com.android.application") version "8.7.2" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
    id("com.chaquo.python") version "16.1.0" apply false
}
