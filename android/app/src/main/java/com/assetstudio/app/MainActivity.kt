package com.assetstudio.app

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.assetstudio.app.screens.*
import com.assetstudio.app.theme.StudioTheme

class MainActivity : ComponentActivity() {
    private val vm: StudioViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        intent?.takeIf { it.action == Intent.ACTION_VIEW }?.data?.let { uri ->
            val name = uri.lastPathSegment ?: "imported.glb"
            vm.importLocal(uri, name)
        }
        setContent { StudioTheme { StudioNav(vm) } }
    }
}

/** Twelve real destinations; every control in each screen performs a real action. */
object Routes {
    const val HOME = "home"
    const val LIBRARY = "library"
    const val MODELS = "models"
    const val VIEWPORT = "viewport"
    const val SKELETON = "skeleton"
    const val ANIMATIONS = "animations"
    const val PLAYER = "player"
    const val MATERIALS = "materials"
    const val GEOMETRY = "geometry"
    const val VALIDATION = "validation"
    const val DOWNLOADS = "downloads"
    const val SETTINGS = "settings"
    val ALL = listOf(HOME, LIBRARY, MODELS, VIEWPORT, SKELETON, ANIMATIONS, PLAYER,
        MATERIALS, GEOMETRY, VALIDATION, DOWNLOADS, SETTINGS)
}

@Composable
fun StudioNav(vm: StudioViewModel) {
    val nav = rememberNavController()
    NavHost(nav, startDestination = Routes.HOME) {
        composable(Routes.HOME) { HomeScreen(vm, nav) }
        composable(Routes.LIBRARY) { AssetLibraryScreen(vm, nav) }
        composable(Routes.MODELS) { ModelBrowserScreen(vm, nav) }
        composable(Routes.VIEWPORT) { ViewportScreen(vm, nav) }
        composable(Routes.SKELETON) { SkeletonInspectorScreen(vm, nav) }
        composable(Routes.ANIMATIONS) { AnimationLibraryScreen(vm, nav) }
        composable(Routes.PLAYER) { AnimationPlayerScreen(vm, nav) }
        composable(Routes.MATERIALS) { MaterialInspectorScreen(vm, nav) }
        composable(Routes.GEOMETRY) { GeometryInspectorScreen(vm, nav) }
        composable(Routes.VALIDATION) { ValidationReportScreen(vm, nav) }
        composable(Routes.DOWNLOADS) { DownloadsScreen(vm, nav) }
        composable(Routes.SETTINGS) { SettingsScreen(vm, nav) }
    }
}
