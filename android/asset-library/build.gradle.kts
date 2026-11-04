plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.serialization)
}

android {
    namespace = "com.assetstudio.library"
    compileSdk = 35
    defaultConfig {
        minSdk = 26
        consumerProguardFiles("consumer-rules.pro")
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { buildConfig = true }
    defaultConfig {
        // Same property the app module uses, so the catalog URL has one source.
        val pagesBaseUrl = (project.findProperty("assetStudio.pagesBaseUrl") as String?)
            ?: error("assetStudio.pagesBaseUrl is missing from android/gradle.properties")
        buildConfigField("String", "CATALOG_BASE_URL", "\"$pagesBaseUrl\"")
    }
}

dependencies {
    implementation(project(":core"))
    implementation(project(":data"))
    implementation(libs.okhttp)
    implementation(libs.okhttp.logging)
    implementation(libs.kotlinx.serialization)
    implementation(libs.kotlinx.coroutines)
    implementation(libs.androidx.documentfile)
    implementation(libs.androidx.core.ktx)
    // :data exposes Room-backed database classes; consumers need RoomDatabase on the classpath.
    implementation(libs.room.runtime)
    implementation(libs.room.ktx)
    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.kotlinx.coroutines.test)
}
