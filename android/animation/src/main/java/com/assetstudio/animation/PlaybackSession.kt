package com.assetstudio.animation

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Ties the playback state machine to a concrete renderer.
 *
 * The session keeps the per-category evidence of everything validated *in this
 * process lifetime*, so the player screen can show "validated on this device" for
 * idle/walk/run/jump/attack/death as the user loads each clip. Nothing is
 * remembered across launches and nothing is inferred from the library metadata:
 * a category is only marked validated after the animator really posed the clip.
 */
class PlaybackSession(private val controller: AnimationController) {

    private val _report = MutableStateFlow<PlaybackReport?>(null)
    val report: StateFlow<PlaybackReport?> = _report.asStateFlow()

    private val _validated = MutableStateFlow<Map<PlaybackCategory, ClipProbe>>(emptyMap())
    val validated: StateFlow<Map<PlaybackCategory, ClipProbe>> = _validated.asStateFlow()

    var renderer: PlaybackRenderer = NoPlaybackRenderer
        private set

    var assetName: String = ""
        private set

    /**
     * Bind a freshly loaded GLB. Clip names and durations are read from the
     * animator, never from the catalog metadata, so the UI cannot show a clip that
     * the file does not contain.
     */
    fun attach(renderer: PlaybackRenderer, assetName: String) {
        this.renderer = renderer
        this.assetName = assetName
        if (!renderer.hasAsset) {
            controller.fail("The GLB failed to load; no clip can be played.")
            _report.value = null
            return
        }
        val count = renderer.clipCount
        val names = (0 until count).map { renderer.clipName(it) }
        val durations = (0 until count).map { renderer.clipDuration(it) }
        controller.bindAsset(assetName, names, durations)
        _report.value = null
    }

    fun detach() {
        attach(NoPlaybackRenderer, "")
        _report.value = null
    }

    /** Validate the currently loaded asset and merge the evidence into the session. */
    fun validate(categories: List<PlaybackCategory> = PlaybackCategory.entries): PlaybackReport {
        val result = PlaybackValidator.validate(assetName, renderer, categories)
        _report.value = result
        val passed = result.categories.mapNotNull { outcome ->
            outcome.probe?.takeIf { it.status == ProbeStatus.PASS }?.let { outcome.category to it }
        }.toMap()
        if (passed.isNotEmpty()) _validated.value = _validated.value + passed
        return result
    }

    /** Categories validated on this device in this session. */
    fun validatedCategories(): Set<PlaybackCategory> = _validated.value.keys
}
