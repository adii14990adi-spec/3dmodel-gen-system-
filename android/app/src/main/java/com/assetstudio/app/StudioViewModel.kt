package com.assetstudio.app

import android.app.Application
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.assetstudio.animation.AnimationController
import com.assetstudio.animation.PlaybackCategory
import com.assetstudio.animation.PlaybackRenderer
import com.assetstudio.animation.PlaybackReport
import com.assetstudio.animation.PlaybackSession
import com.assetstudio.core.model.*
import com.assetstudio.data.db.CachedAssetEntity
import com.assetstudio.data.prefs.StudioSettings
import com.assetstudio.library.catalog.CatalogDefaults
import com.assetstudio.library.catalog.HealthDto
import com.assetstudio.library.download.DownloadState
import com.assetstudio.skeleton.SkeletonInspector
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch

data class StudioUiState(
    val health: HealthDto? = null,
    val assets: List<AssetSummary> = emptyList(),
    val cached: List<CachedAssetEntity> = emptyList(),
    val cacheBytes: Long = 0,
    val selectedModel: ModelDetail? = null,
    val selectedAnimation: AnimationDetail? = null,
    val compatible: List<CompatibleAnimation> = emptyList(),
    val settings: StudioSettings? = null,
    val search: String = "",
    val sort: String = "name",
    val download: DownloadState? = null,
    val report: com.assetstudio.core.model.ValidationInfo? = null,
    val busy: Boolean = false,
    val message: String = ""
)

class StudioViewModel(app: Application) : AndroidViewModel(app) {
    private val container = (app as AssetStudioApp).container
    private val repo get() = container.repository()

    private val _ui = MutableStateFlow(StudioUiState())
    val ui: StateFlow<StudioUiState> = _ui.asStateFlow()

    val skeletonInspector = SkeletonInspector()
    lateinit var animationController: AnimationController
        private set

    /** Playback transport + runtime validation evidence for the loaded GLB. */
    val playbackSession: PlaybackSession get() = _playbackSession
    private lateinit var _playbackSession: PlaybackSession

    /**
     * Adapter that exposes the live Filament viewport to the playback layer.
     *
     * It resolves the viewport lazily because the surface is created by Compose, and
     * it reports failure (false) instead of faking a pose when nothing is bound.
     */
    private val playbackRenderer = object : PlaybackRenderer {
        override val hasAsset: Boolean get() = viewport?.hasAsset() == true
        override val clipCount: Int get() = viewport?.animationCount() ?: 0
        override fun clipName(index: Int): String = viewport?.animationName(index) ?: ""
        override fun clipDuration(index: Int): Float = viewport?.animationDuration(index) ?: 0f
        override fun apply(index: Int, seconds: Float): Boolean =
            viewport?.applyAnimation(index, seconds) == true
    }

    private var refreshJob: Job? = null

    init {
        animationController = AnimationController { index, seconds ->
            // Bound to the active FilamentViewport by the Animation Player screen.
            viewport?.applyAnimation(index, seconds)
        }
        _playbackSession = PlaybackSession(animationController)
        viewModelScope.launch {
            container.settings.settings.collect { s ->
                container.configure(s.apiBaseUrl)
                _ui.update { it.copy(settings = s) }
                refresh()
            }
        }
        viewModelScope.launch { repo.cachedAssets().collect { list -> _ui.update { it.copy(cached = list) } } }
        viewModelScope.launch { repo.totalCacheBytes().collect { b -> _ui.update { it.copy(cacheBytes = b) } } }
    }

    /** Set by the 3D Viewport screen while it is on screen. */
    @Volatile var viewport: com.assetstudio.renderer.FilamentViewport? = null

    /**
     * Re-read the GitHub Pages catalog and republish it to the UI.
     *
     * Failure is not fatal: the Room copy written by the previous refresh keeps the
     * library, the detail screens and downloaded GLBs usable offline, and the
     * message names the exact URL that could not be reached instead of hiding it.
     */
    fun refresh() {
        refreshJob?.cancel()
        refreshJob = viewModelScope.launch {
            _ui.update { it.copy(busy = true, message = "") }
            val base = _ui.value.settings?.apiBaseUrl ?: CatalogDefaults.DEFAULT_BASE_URL
            runCatching {
                val refresh = repo.refreshCatalog(force = true)
                val health = repo.health()
                val assets = repo.assets(_ui.value.search, _ui.value.sort)
                _ui.update {
                    it.copy(
                        health = health, assets = assets, busy = false,
                        message = "Catalog ${refresh.counts.models} model(s), " +
                            "${refresh.counts.animations} animation(s) from " +
                            "${refresh.baseUrl}${if (refresh.fromCache) " (cached copy)" else ""}"
                    )
                }
            }.onFailure { e ->
                runCatching { repo.assets(_ui.value.search, _ui.value.sort) }.onSuccess { cached ->
                    _ui.update { it.copy(assets = cached) }
                }
                _ui.update {
                    it.copy(busy = false, message = "Catalog unreachable at $base: " +
                        "${e.message}. Downloaded assets remain usable offline.")
                }
            }
        }
    }

    /** Force a metadata refresh without touching the search field. */
    fun refreshCatalog() = refresh()

    fun search(query: String) { _ui.update { it.copy(search = query) }; refresh() }
    fun sort(value: String) { _ui.update { it.copy(sort = value) }; refresh() }

    fun selectModel(id: String) = viewModelScope.launch {
        _ui.update { it.copy(busy = true) }
        runCatching { repo.modelDetail(id) }.onSuccess { detail ->
            skeletonInspector.load(detail.skeleton)
            _ui.update { it.copy(selectedModel = detail, busy = false, message = "") }
            runCatching { repo.compatibleAnimations(id) }.onSuccess { c ->
                _ui.update { it.copy(compatible = c) }
            }
        }.onFailure { e -> _ui.update { it.copy(busy = false, message = e.message ?: "Failed to load model") } }
    }

    fun selectAnimation(id: String) = viewModelScope.launch {
        _ui.update { it.copy(busy = true) }
        runCatching { repo.animationDetail(id) }.onSuccess { detail ->
            _ui.update { it.copy(selectedAnimation = detail, busy = false, message = "") }
        }.onFailure { e -> _ui.update { it.copy(busy = false, message = e.message ?: "Failed to load animation") } }
    }

    fun download(asset: AssetSummary) = viewModelScope.launch {
        _ui.update { it.copy(download = DownloadState.Progress(0, 0), message = "") }
        runCatching { repo.download(asset).collect { s -> _ui.update { it.copy(download = s) } } }
            .onFailure { e -> _ui.update { it.copy(download = DownloadState.Failed(e.message ?: "Download failed")) } }
    }

    fun importLocal(uri: Uri, name: String) = viewModelScope.launch {
        repo.importLocal(uri, name).onSuccess { entity ->
            // The inner `it` is StudioUiState; the imported entity needs its own name.
            _ui.update { it.copy(message = "Imported ${entity.name}") }
        }
            .onFailure { e -> _ui.update { it.copy(message = "Import failed: ${e.message}") } }
    }

    fun deleteLocal(id: String) = viewModelScope.launch { repo.deleteLocal(id) }

    fun loadValidation(kind: String, id: String) = viewModelScope.launch {
        runCatching { repo.report(if (kind == "model") "models" else "animations", id) }
            .onSuccess { json ->
                val obj = org.json.JSONObject(json.toString())
                val s = obj.optJSONObject("summary") ?: org.json.JSONObject()
                val checks = obj.optJSONArray("checks")
                val list = (0 until (checks?.length() ?: 0)).map { i ->
                    val c = checks!!.optJSONObject(i) ?: org.json.JSONObject()
                    com.assetstudio.core.model.ValidationCheck(c.optString("severity"), c.optString("code"),
                        c.optString("description"), c.optString("asset"), c.optString("location"),
                        c.optString("suggested_fix"))
                }
                _ui.update { it.copy(report = com.assetstudio.core.model.ValidationInfo(
                    obj.optString("status"), s.optInt("PASS"), s.optInt("WARNING"),
                    s.optInt("ERROR"), obj.optString("report"), list)) }
            }.onFailure { _ui.update { it.copy(report = com.assetstudio.core.model.ValidationInfo()) } }
    }

    /**
     * Load an exported GLB into the viewport and bind the clips the animator
     * reports. The catalog's clip list is never used for playback: if the file does
     * not contain the clip, the player must not show it.
     */
    fun loadPlaybackAsset(path: String, label: String) {
        val target = viewport
        if (target == null) {
            animationController.fail("The 3D viewport is not ready yet.")
            return
        }
        runCatching { target.loadFile(path) }
            .onFailure { animationController.fail("Could not load $label: ${it.message}") }
            .onSuccess { _playbackSession.attach(playbackRenderer, label) }
    }

    /** Pose every available clip three times and record what the animator did. */
    fun validatePlayback(): PlaybackReport? {
        if (viewport?.hasAsset() != true) {
            animationController.fail("Load a GLB before validating playback.")
            return null
        }
        return _playbackSession.validate()
    }

    /** Categories already validated on this device in this session. */
    fun validatedPlaybackCategories(): Set<PlaybackCategory> =
        _playbackSession.validatedCategories()

    fun bindAnimationClips() {
        val a = _ui.value.selectedAnimation ?: return
        animationController.bind(a.clips.size, a.clips.map { it.name }, a.clips.map { it.durationSeconds })
    }

    fun clearMessage() { _ui.update { it.copy(message = "") } }

    suspend fun <T> setSetting(key: androidx.datastore.preferences.core.Preferences.Key<T>, value: T) =
        container.settings.set(key, value)
}
