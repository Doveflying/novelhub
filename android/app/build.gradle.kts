plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

android {
    namespace = "com.firefly.library"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.firefly.library"
        minSdk = 24          // Chaquopy 16 的下限
        targetSdk = 35
        versionCode = 1
        versionName = "0.4.0"

        // Python 解释器是原生组件，必须显式声明 ABI。
        // 只保留 arm64（真机）与 x86_64（模拟器），体积能省一半。
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // 自签名（不上架，装自己手机），proguard 关掉避免 Chaquopy 反射被裁
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    // 打包时把前端资源塞进 assets，Python 侧从 assets 读
    sourceSets["main"].assets.srcDirs("src/main/assets")
}

chaquopy {
    defaultConfig {
        version = "3.11"

        // buildPython：本机 Python，用来编译 pyc / 处理 pip 包。
        // 这台机器的 python 是 Microsoft Store 别名，真实路径含中文用户名，
        // 写死在 gradle 里容易被路径转义坑到，所以从环境变量取，取不到就交给
        // Chaquopy 自己按 PATH 找（找不到只影响 pyc 优化，不影响功能）。
        val pyExe = System.getenv("BOOKFLY_BUILD_PYTHON")
        if (!pyExe.isNullOrBlank()) {
            buildPython(pyExe)
        }

        pip {
            // pip 的默认源 files.pythonhosted.org 在这台机器上慢到不可用
            // （实测 97KB 要 17 秒，Chaquopy 直接 ReadTimeout 失败）。
            // 换成清华镜像；Chaquopy 自己的原生包仓库仍是标准 PEP503 索引
            // （https://chaquo.com/pypi-13.1），用 extra-index 保留，
            // lxml / cryptography 这类带 .so 的包还得从那里取。
            options("--timeout", "180")
            options("--retries", "8")
            options("--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple")
            options("--extra-index-url", "https://chaquo.com/pypi-13.1")
            options("--trusted-host", "pypi.tuna.tsinghua.edu.cn")
            options("--trusted-host", "chaquo.com")

            // 纯 Python：从镜像取
            install("Flask>=3.0")
            install("requests>=2.31")
            install("beautifulsoup4>=4.12")
            install("EbookLib>=0.18")
            install("zhconv>=1.4")
            // cssselect：lxml 的 CSS 选择器支持，规则型源用得上
            install("cssselect>=1.2")
            // 带原生扩展：Chaquopy 的 pypi-13.1 镜像里有预编译 wheel
            install("lxml>=5.0")
            install("cryptography>=41.0")
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.11.0")
}
