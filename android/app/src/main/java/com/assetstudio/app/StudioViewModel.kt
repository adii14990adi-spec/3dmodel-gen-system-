package com.assetstudio.app

import android.app.Application
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.assetstudio.animation.AnimationController
import com.assetstudio.core.model.*
import com.assetstudio.data.db.CachedAssetEntity
import com.assetstudio.data.prefs.StudioSettings
import com.assetstudio.library.api.HealthDto
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

    private var refreshJob: Job? = null

    init {
        animationController = AnimationController { index, seconds ->
            // Bound to the active FilamentViewport by the Animation Player screen.
            viewport?.applyAnimation(index, seconds)
        }
        viewModelScope.launch {
            container.settings.settings.collect { s ->
                container.configure(s.apiBaseUrl, s.apiKey)
                _ui.update { it.copy(settings = s) }
                refresh()
            }
        }
        viewModelScope.launch { repo.cachedAssets().collect { list -> _ui.update { it.copy(cached = list) } } }
        viewModelScope.launch { repo.totalCacheBytes().collect { b -> _ui.update { it.copy(cacheBytes = b) } } }
    }

    /** Set by the 3D Viewport screen while it is on screen. */
    @Volatile var viewport: com.assetstudio.renderer.FilamentViewport? = null

    fun refresh() {
        refreshJob?.cancel()
        refreshJob = viewModelScope.launch {
            _ui.update { it.copy(busy = true, message = "") }
            runCatching {
                val health = repo.health()
                val assets = repo.assets(_ui.value.search, _ui.value.sort)
                _ui.update { it.copy(health = health, assets = assets, busy = false) }
            }.onFailure { e ->
                _ui.update { it.copy(busy = false, message = "Backend unavailable: ${e.message}. Cached assets remain usable offline.") }
            }
        }
    }

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

    fun bindAnimationClips() {
        val a = _ui.value.selectedAnimation ?: return
        animationController.bind(a.clips.size, a.clips.map { it.name }, a.clips.map { it.durationSeconds })
    }

    fun clearMessage() { _ui.update { it.copy(message = "") } }

    suspend fun <T> setSetting(key: androidx.datastore.preferences.core.Preferences.Key<T>, value: T) =
        container.settings.set(key, value)
}
