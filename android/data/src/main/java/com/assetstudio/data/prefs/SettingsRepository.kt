package com.assetstudio.data.prefs

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.*
import androidx.datastore.preferences.preferencesDataStore
import com.assetstudio.core.model.Quality
import com.assetstudio.core.model.RenderMode
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore("asset_studio_settings")

class SettingsRepository(
    private val context: Context,
    private val defaultCatalogBaseUrl: String
) {
    companion object {
        /** GitHub Pages catalog root; overridable for forks and local previews. */
        val CATALOG_BASE_URL = stringPreferencesKey("catalog_base_url")
        /** Legacy keys, read once for migration; never used to contact a server. */
        val LEGACY_API_BASE_URL = stringPreferencesKey("api_base_url")
        val QUALITY = stringPreferencesKey("quality")
        val RENDER_MODE = stringPreferencesKey("render_mode")
        val SHOW_GRID = booleanPreferencesKey("show_grid")
        val SHOW_AXES = booleanPreferencesKey("show_axes")
        val SHOW_GROUND = booleanPreferencesKey("show_ground")
        val SHOW_SKELETON = booleanPreferencesKey("show_skeleton")
        val SKELETON_ONLY = booleanPreferencesKey("skeleton_only")
        val HDR_ENABLED = booleanPreferencesKey("hdr_enabled")
        val SHADOWS_ENABLED = booleanPreferencesKey("shadows_enabled")
        val ORTHOGRAPHIC = booleanPreferencesKey("orthographic")
        val EXPOSURE = floatPreferencesKey("exposure")
        val THERMAL_AWARE = booleanPreferencesKey("thermal_aware")
        val LARGE_FILE_WARNING = booleanPreferencesKey("large_file_warning")
        val LOOP = booleanPreferencesKey("loop")
        val SPEED = floatPreferencesKey("speed")
        val FPS_OVERLAY = booleanPreferencesKey("fps_overlay")
    }

    val settings: Flow<StudioSettings> = context.dataStore.data.map { p ->
        StudioSettings(
            // Only the Pages base URL is ever used; the legacy key is migrated once.
            apiBaseUrl = p[CATALOG_BASE_URL] ?: defaultCatalogBaseUrl,
            apiKey = "", // the static catalog is public: there is no API key any more
            quality = runCatching { Quality.valueOf(p[QUALITY] ?: "BALANCED") }.getOrDefault(Quality.BALANCED),
            renderMode = runCatching { RenderMode.valueOf(p[RENDER_MODE] ?: "PBR") }.getOrDefault(RenderMode.PBR),
            showGrid = p[SHOW_GRID] ?: true, showAxes = p[SHOW_AXES] ?: true,
            showGround = p[SHOW_GROUND] ?: false, showSkeleton = p[SHOW_SKELETON] ?: false,
            skeletonOnly = p[SKELETON_ONLY] ?: false, hdrEnabled = p[HDR_ENABLED] ?: true,
            shadowsEnabled = p[SHADOWS_ENABLED] ?: true, orthographic = p[ORTHOGRAPHIC] ?: false,
            exposure = p[EXPOSURE] ?: 1f, thermalAware = p[THERMAL_AWARE] ?: true,
            largeFileWarning = p[LARGE_FILE_WARNING] ?: true, loop = p[LOOP] ?: true,
            speed = p[SPEED] ?: 1f, frameRateOverlay = p[FPS_OVERLAY] ?: false
        )
    }

    suspend fun <T> set(key: Preferences.Key<T>, value: T) { context.dataStore.edit { it[key] = value } }
}

data class StudioSettings(
    val apiBaseUrl: String, val apiKey: String,
    val quality: Quality = Quality.BALANCED, val renderMode: RenderMode = RenderMode.PBR,
    val showGrid: Boolean = true, val showAxes: Boolean = true, val showGround: Boolean = false,
    val showSkeleton: Boolean = false, val skeletonOnly: Boolean = false,
    val hdrEnabled: Boolean = true, val shadowsEnabled: Boolean = true,
    val orthographic: Boolean = false, val exposure: Float = 1f,
    val thermalAware: Boolean = true, val largeFileWarning: Boolean = true,
    val loop: Boolean = true, val speed: Float = 1f, val frameRateOverlay: Boolean = false
)
