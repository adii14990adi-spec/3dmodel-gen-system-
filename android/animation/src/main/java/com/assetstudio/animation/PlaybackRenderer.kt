package com.assetstudio.animation

/**
 * The minimum a renderer must expose for real playback validation.
 *
 * The Filament implementation lives in the app module (it wraps `FilamentViewport`
 * + the gltfio `Animator`), so this module stays free of rendering dependencies and
 * can be unit-tested on the JVM. A renderer that cannot really move the skeleton
 * must return `false` from [apply] — a static pose is never reported as playback.
 */
interface PlaybackRenderer {
    /** True once a GLB has been loaded into the renderer. */
    val hasAsset: Boolean

    /** Number of animation clips the loaded GLB actually contains. */
    val clipCount: Int

    fun clipName(index: Int): String

    /** Clip duration in seconds as reported by the animator (0 when unknown). */
    fun clipDuration(index: Int): Float

    /**
     * Apply [seconds] of clip [index] to the skeleton.
     *
     * @return true only when the animator applied the pose *and* the bone matrices
     * were refreshed; false when the clip/skeleton could not be posed.
     */
    fun apply(index: Int, seconds: Float): Boolean
}

/** Renderer used before a GLB is loaded; it never claims to have applied a pose. */
object NoPlaybackRenderer : PlaybackRenderer {
    override val hasAsset: Boolean get() = false
    override val clipCount: Int get() = 0
    override fun clipName(index: Int): String = ""
    override fun clipDuration(index: Int): Float = 0f
    override fun apply(index: Int, seconds: Float): Boolean = false
}
