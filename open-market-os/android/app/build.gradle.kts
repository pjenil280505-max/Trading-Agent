import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Release signing: a keystore described by android/signing.properties (never committed) or
// environment variables OMOS_KEYSTORE / OMOS_KEYSTORE_PASSWORD / OMOS_KEY_ALIAS / OMOS_KEY_PASSWORD.
val signing = Properties().apply {
    val f = rootProject.file("signing.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
fun signingValue(key: String, env: String): String? = signing.getProperty(key) ?: System.getenv(env)

val appVersion = rootProject.file("../src/omos/__init__.py").readText()
    .let { Regex("__version__ = \"([0-9.]+)\"").find(it)!!.groupValues[1] }

android {
    namespace = "org.openmarketos.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "org.openmarketos.app"
        minSdk = 24
        targetSdk = 35
        val (ma, mi, pa) = appVersion.split(".").map { it.toInt() }
        versionCode = ma * 10000 + mi * 100 + pa
        versionName = appVersion
    }

    signingConfigs {
        create("release") {
            val store = signingValue("storeFile", "OMOS_KEYSTORE")
            if (store != null) {
                storeFile = file(store)
                storePassword = signingValue("storePassword", "OMOS_KEYSTORE_PASSWORD")
                keyAlias = signingValue("keyAlias", "OMOS_KEY_ALIAS")
                keyPassword = signingValue("keyPassword", "OMOS_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = if (signingValue("storeFile", "OMOS_KEYSTORE") != null) signingConfigs.getByName("release") else null
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        buildConfig = true
    }
    lint {
        abortOnError = true
        checkReleaseBuilds = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.webkit:webkit:1.12.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
    testImplementation("junit:junit:4.13.2")
}
