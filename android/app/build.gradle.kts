import java.util.Properties
import java.io.FileInputStream

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.ksp)
}

// Single source of truth for the static hosting base URL: android/gradle.properties.
val pagesBaseUrl: String = (project.findProperty("assetStudio.pagesBaseUrl") as String?)
    ?: error("assetStudio.pagesBaseUrl is missing from android/gradle.properties")

val keystorePropertiesFile = rootProject.file("keystore.properties")
val keystoreProperties = Properties().apply {
    if (keystorePropertiesFile.isFile) load(FileInputStream(keystorePropertiesFile))
}

android {
    namespace = "com.assetstudio.app"
    compileSdk = 35
    defaultConfig {
        applicationId = "com.assetstudio.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        vectorDrawables { useSupportLibrary = true }
        // GitHub Pages static catalog. No API server is contacted anywhere in the app.
        buildConfigField("String", "CATALOG_BASE_URL", "\"$pagesBaseUrl\"")
    }
    signingConfigs {
        if (keystorePropertiesFile.isFile) {
            create("release") {
                storeFile = file(keystoreProperties.getProperty("storeFile"))
                storePassword = keystoreProperties.getProperty("storePassword")
                keyAlias = keystoreProperties.getProperty("keyAlias")
                keyPassword = keystoreProperties.getProperty("keyPassword")
            }
        }
    }
    buildTypes {
        debug { isMinifyEnabled = false; applicationIdSuffix = ".debug" }
        release {
            isMinifyEnabled = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            if (keystorePropertiesFile.isFile) signingConfig = signingConfigs.getByName("release")
        }
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true; buildConfig = true }
    packaging { resources.excludes += setOf("/META-INF/{AL2.0,LGPL2.1}") }
    testOptions { unitTests.isIncludeAndroidResources = true; animationsDisabled = true }
}

dependencies {
    implementation(project(":core")); implementation(project(":data"))
    implementation(project(":asset-library")); implementation(project(":renderer"))
    implementation(project(":skeleton")); implementation(project(":animation"))
    implementation(project(":inspector"))
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.appcompat)
    implementation(libs.androidx.lifecycle.runtime)
    implementation(libs.androidx.lifecycle.viewmodel)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.compose.material3)
    implementation(libs.compose.material.icons)
    implementation(libs.compose.navigation)
    implementation(libs.androidx.documentfile)
    implementation(libs.coil)
    implementation(libs.filament.android)
    implementation(libs.filament.gltfio)
    implementation(libs.filament.utils)
    implementation(libs.kotlinx.serialization)
    implementation(libs.kotlinx.coroutines)
    // The app module owns the HTTP client that the static catalog uses (AppContainer).
    implementation(libs.okhttp)
    implementation(libs.okhttp.logging)
    implementation(libs.room.runtime)
    implementation(libs.room.ktx)
    ksp(libs.room.compiler)
    implementation(libs.datastore.preferences)
    debugImplementation(libs.compose.ui.tooling)
    testImplementation(libs.junit)
    testImplementation(libs.robolectric)
    testImplementation(libs.androidx.test.junit)
    androidTestImplementation(libs.androidx.test.junit)
    androidTestImplementation(libs.espresso)
}
