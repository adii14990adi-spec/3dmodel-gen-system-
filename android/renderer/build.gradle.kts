plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.android)
}

android {
    namespace = "com.assetstudio.renderer"
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
}

dependencies {
    implementation(project(":core"))
    implementation(libs.filament.android)
    implementation(libs.filament.gltfio)
    implementation(libs.filament.utils)
    implementation(libs.kotlinx.coroutines)
    implementation(libs.androidx.core.ktx)
    testImplementation(libs.junit)
}
