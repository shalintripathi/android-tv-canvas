plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "io.github.shalintripathi.canvas"
    compileSdk = 35

    defaultConfig {
        applicationId = "io.github.shalintripathi.canvas"
        minSdk = 24
        targetSdk = 35
        versionCode = (findProperty("versionCode") as String?)?.toInt() ?: 1
        versionName = (findProperty("versionName") as String?) ?: "0.1.0-dev"
    }

    signingConfigs {
        getByName("debug") {
            // Committed on purpose so every CI build updates in place on the TV. Debug only.
            storeFile = file("debug.keystore")
            storePassword = "android"
            keyAlias = "androiddebugkey"
            keyPassword = "android"
        }
        create("release") {
            val keystore = System.getenv("CANVAS_KEYSTORE")
            if (keystore != null) {
                storeFile = file(keystore)
                storePassword = System.getenv("CANVAS_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("CANVAS_KEY_ALIAS")
                keyPassword = System.getenv("CANVAS_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.getByName(
                if (System.getenv("CANVAS_KEYSTORE") != null) "release" else "debug"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        abortOnError = true
        warningsAsErrors = false
    }
}

kotlin {
    jvmToolchain(17)
}

dependencies {
    implementation(project(":core"))
    implementation("com.google.zxing:core:3.5.3")
}
