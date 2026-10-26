plugins {
    id("com.android.application")
}

val ks = System.getenv("KEYSTORE_PATH")

android {
    namespace = "it.trinka.seriea"
    compileSdk = 34

    defaultConfig {
        applicationId = "it.trinka.seriea"
        minSdk = 26
        targetSdk = 34
        versionCode = (System.getenv("VERSION_CODE") ?: "1").toInt()
        versionName = "1." + (System.getenv("VERSION_CODE") ?: "0")
        buildConfigField("String", "APP_URL", "\"https://trinka90.github.io/seriew-predictor/\"")
    }

    buildFeatures { buildConfig = true }

    signingConfigs {
        create("release") {
            if (ks != null) {
                storeFile = file(ks)
                storePassword = System.getenv("KEYSTORE_PASSWORD")
                keyAlias = System.getenv("KEY_ALIAS")
                keyPassword = System.getenv("KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (ks != null) signingConfigs.getByName("release") else signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
