package com.assetstudio.animation

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

val PLAYBACK_SPEEDS = listOf(0.25f, 0.5f, 0.75f, 1.0f, 1.25f, 1.5f, 2.0f)

/**
 * Playback state machine for one loaded GLB.
 *
 * It owns *time*, not rendering: every state change that needs a pose calls
 * [apply], which the app binds to the Filament animator. That split is what makes
 * the transport (play/pause/stop/seek/loop/speed) unit-testable on the JVM while
 * the renderer stays responsible for really posing the skeleton.
 */
class AnimationController(private val apply: (Int, Float) -> Unit) {
    data class State(
        val clipCount: Int = 0,
        val clipIndex: Int = 0,
        val clipNames: List<String> = emptyList(),
        val clipDurations: List<Float> = emptyList(),
        val duration: Float = 0f,
        val currentTime: Float = 0f,
        val playing: Boolean = false,
        val looping: Boolean = true,
        val speed: Float = 1f,
        val assetName: String = "",
        val message: String = "",
        val error: String = ""
    ) {
        val enabled: Boolean get() = clipCount > 0 && duration > 0f
        val progress: Float
            get() = if (duration <= 0f) 0f else (currentTime / duration).coerceIn(0f, 1f)
        val clipName: String get() = clipNames.getOrElse(clipIndex) { "" }
    }

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    /** Fill the track list (used by tests and callers that already know the clips). */
    fun bind(clipCount: Int, names: List<String>, durations: List<Float>) {
        bindAsset("", names, durations, clipCount)
    }

    /**
     * Bind the clips of a freshly loaded asset.
     *
     * [clipCount] may be smaller than the list length (extra names are ignored);
     * durations default to 0, which disables playback instead of inventing a
     * length for a clip the animator could not measure.
     */
    fun bindAsset(
        assetName: String,
        names: List<String>,
        durations: List<Float>,
        clipCount: Int = names.size
    ) {
        val count = clipCount.coerceIn(0, names.size)
        val safeNames = names.take(count)
        val safeDurations = (0 until count).map { durations.getOrElse(it) { 0f } }
        _state.value = State(
            clipCount = count,
            clipIndex = 0,
            clipNames = safeNames,
            clipDurations = safeDurations,
            duration = safeDurations.firstOrNull() ?: 0f,
            assetName = assetName,
            message = when {
                count == 0 -> "This GLB contains no animation clips"
                safeDurations.any { it <= 0f } -> "Some clips report zero duration and cannot be played"
                else -> "Loaded $count clip(s) from ${assetName.ifBlank { "the GLB" }}"
            }
        )
        if (count > 0) apply(0, 0f)
    }

    /** Record a load failure without pretending something is playable. */
    fun fail(message: String) {
        _state.value = State(message = message, error = message)
    }

    fun selectClip(index: Int, duration: Float) {
        val s = _state.value
        if (index !in 0 until s.clipCount) return
        val d = if (duration > 0f) duration else s.clipDurations.getOrElse(index) { 0f }
        _state.value = s.copy(clipIndex = index, duration = d, currentTime = 0f, playing = false)
        apply(index, 0f)
    }

    fun play() {
        val s = _state.value
        if (!s.enabled) return
        _state.value = s.copy(playing = true, error = "")
    }

    fun pause() {
        _state.value = _state.value.copy(playing = false)
    }

    fun stop() {
        val s = _state.value
        _state.value = s.copy(playing = false, currentTime = 0f)
        apply(s.clipIndex, 0f)
    }

    fun restart() {
        val s = _state.value
        if (!s.enabled) return
        _state.value = s.copy(currentTime = 0f, playing = true, error = "")
        apply(s.clipIndex, 0f)
    }

    fun setLooping(value: Boolean) {
        _state.value = _state.value.copy(looping = value)
    }

    fun setSpeed(value: Float) {
        if (value in PLAYBACK_SPEEDS) _state.value = _state.value.copy(speed = value)
    }

    fun seek(seconds: Float) {
        val s = _state.value
        if (!s.enabled) return
        val t = seconds.coerceIn(0f, s.duration)
        _state.value = s.copy(currentTime = t)
        apply(s.clipIndex, t)
    }

    /**
     * Advance the clock by [deltaNanos] and pose the skeleton at the new time.
     *
     * Negative or zero deltas are ignored so a paused/resumed render loop cannot
     * jump backwards, and a clip that reached its end either wraps (looping) or
     * stops exactly on the final pose.
     */
    fun tick(deltaNanos: Long) {
        val s = _state.value
        if (!s.playing || !s.enabled || deltaNanos <= 0L) return
        var t = s.currentTime + (deltaNanos / 1_000_000_000f) * s.speed
        if (t >= s.duration) {
            if (s.looping) {
                t %= s.duration
            } else {
                _state.value = s.copy(currentTime = s.duration, playing = false)
                apply(s.clipIndex, s.duration)
                return
            }
        }
        _state.value = s.copy(currentTime = t)
        apply(s.clipIndex, t)
    }
}
