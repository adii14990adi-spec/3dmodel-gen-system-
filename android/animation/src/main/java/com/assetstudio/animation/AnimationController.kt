package com.assetstudio.animation

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

val PLAYBACK_SPEEDS = listOf(0.25f, 0.5f, 0.75f, 1.0f, 1.25f, 1.5f, 2.0f)

/** Real playback state machine driven by the Filament render loop. */
class AnimationController(private val apply: (Int, Float) -> Unit) {
    data class State(
        val clipCount: Int = 0, val clipIndex: Int = 0, val clipNames: List<String> = emptyList(),
        val duration: Float = 0f, val currentTime: Float = 0f,
        val playing: Boolean = false, val looping: Boolean = true, val speed: Float = 1f,
        val message: String = ""
    ) { val enabled: Boolean get() = clipCount > 0 && duration > 0f }

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    fun bind(clipCount: Int, names: List<String>, durations: List<Float>) {
        val d = durations.getOrElse(0) { 0f }
        _state.value = State(clipCount = clipCount, clipNames = names, duration = d,
            message = if (clipCount == 0) "No animation clips available" else "")
        if (clipCount > 0) apply(0, 0f)
    }
    fun selectClip(index: Int, duration: Float) {
        if (index !in 0 until _state.value.clipCount) return
        _state.value = _state.value.copy(clipIndex = index, duration = duration, currentTime = 0f)
        apply(index, 0f)
    }
    fun play() { if (_state.value.enabled) _state.value = _state.value.copy(playing = true) }
    fun pause() { _state.value = _state.value.copy(playing = false) }
    fun stop() { _state.value = _state.value.copy(playing = false, currentTime = 0f); apply(_state.value.clipIndex, 0f) }
    fun restart() { _state.value = _state.value.copy(currentTime = 0f, playing = true); apply(_state.value.clipIndex, 0f) }
    fun setLooping(v: Boolean) { _state.value = _state.value.copy(looping = v) }
    fun setSpeed(v: Float) { if (v in PLAYBACK_SPEEDS) _state.value = _state.value.copy(speed = v) }
    fun seek(seconds: Float) {
        val s = _state.value
        val t = seconds.coerceIn(0f, s.duration)
        _state.value = s.copy(currentTime = t); apply(s.clipIndex, t)
    }
    fun tick(deltaNanos: Long) {
        val s = _state.value
        if (!s.playing || !s.enabled) return
        var t = s.currentTime + (deltaNanos / 1_000_000_000f) * s.speed
        if (t >= s.duration) {
            if (s.looping) t %= s.duration
            else { _state.value = s.copy(currentTime = s.duration, playing = false)
                   apply(s.clipIndex, s.duration); return }
        }
        _state.value = s.copy(currentTime = t); apply(s.clipIndex, t)
    }
}
