package com.assetstudio.animation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** JVM tests for the transport. No Android/Filament dependency: `apply` is a fake. */

/**
 * JUnit 4 only exposes double-based delta overloads, and Kotlin does not widen Float to Double
 * implicitly, so float comparisons go through this helper instead of assertEquals.
 */
private fun assertClose(expected: Float, actual: Float, tolerance: Float = 1e-4f) =
    org.junit.Assert.assertEquals(expected.toDouble(), actual.toDouble(), tolerance.toDouble())

class AnimationControllerTest {

    private class Recorder {
        val calls = mutableListOf<Pair<Int, Float>>()
        fun apply(index: Int, seconds: Float) { calls.add(index to seconds) }
        fun last(): Pair<Int, Float>? = calls.lastOrNull()
    }

    private fun controller(vararg durations: Float): Pair<AnimationController, Recorder> {
        val recorder = Recorder()
        val c = AnimationController { i, s -> recorder.apply(i, s) }
        c.bindAsset("walk.glb", durations.mapIndexed { i, _ -> "clip$i" }, durations.toList())
        return c to recorder
    }

    @Test
    fun `bind exposes clips and poses the first frame`() {
        val (c, rec) = controller(1.5f, 2.0f)
        val s = c.state.value
        assertEquals(2, s.clipCount)
        assertEquals(listOf("clip0", "clip1"), s.clipNames)
        assertClose(1.5f, s.duration)
        assertTrue(s.enabled)
        assertTrue(s.message.contains("2 clip"))
        assertEquals(0 to 0f, rec.last())
    }

    @Test
    fun `play then tick advances time and poses the skeleton`() {
        val (c, rec) = controller(1.0f)
        c.play()
        rec.calls.clear()
        c.tick(100_000_000L) // 0.1 s
        c.tick(100_000_000L)
        assertEquals(2, rec.calls.size)
        assertClose(0.2f, c.state.value.currentTime)
        assertTrue(rec.calls[0].second < rec.calls[1].second)
        assertEquals(0, rec.calls[1].first)
    }

    @Test
    fun `tick is ignored while paused or with a non-positive delta`() {
        val (c, rec) = controller(1.0f)
        rec.calls.clear()
        c.tick(50_000_000L)          // not playing yet
        c.play()
        c.pause()
        c.tick(50_000_000L)          // paused
        c.play()
        c.tick(0L)                   // zero delta
        c.tick(-10L)                 // negative delta
        assertClose(0f, c.state.value.currentTime)
        assertTrue(rec.calls.isEmpty())
    }

    @Test
    fun `looping wraps to the start of the clip`() {
        val (c, _) = controller(0.5f)
        c.play()
        c.tick(700_000_000L) // 0.7 s over a 0.5 s clip
        assertTrue(c.state.value.playing)
        assertTrue(c.state.value.currentTime < 0.5f)
        assertTrue(c.state.value.currentTime >= 0f)
    }

    @Test
    fun `non-looping stops exactly on the last pose`() {
        val (c, rec) = controller(0.5f)
        c.setLooping(false)
        c.play()
        rec.calls.clear()
        c.tick(900_000_000L)
        assertFalse(c.state.value.playing)
        assertClose(0.5f, c.state.value.currentTime)
        assertEquals(0 to 0.5f, rec.last())
    }

    @Test
    fun `seek clamps into the clip and stop rewinds`() {
        val (c, rec) = controller(2.0f)
        c.seek(1.25f)
        assertClose(1.25f, c.state.value.currentTime)
        c.seek(99f)
        assertClose(2.0f, c.state.value.currentTime)
        c.seek(-5f)
        assertClose(0f, c.state.value.currentTime)
        c.seek(1f)
        rec.calls.clear()
        c.stop()
        assertClose(0f, c.state.value.currentTime)
        assertEquals(0 to 0f, rec.last())
    }

    @Test
    fun `clip selection changes index and duration and rejects out-of-range`() {
        val (c, rec) = controller(1.0f, 3.0f)
        c.selectClip(1, 3.0f)
        assertEquals(1, c.state.value.clipIndex)
        assertClose(3.0f, c.state.value.duration)
        assertEquals(1 to 0f, rec.last())
        c.selectClip(5, 9f)
        assertEquals(1, c.state.value.clipIndex)
        assertClose(3.0f, c.state.value.duration)
    }

    @Test
    fun `speed scales the advanced time`() {
        val (c, _) = controller(10f)
        c.setSpeed(2.0f)
        c.play()
        c.tick(500_000_000L)
        assertClose(1.0f, c.state.value.currentTime)
        c.setSpeed(4f) // unsupported speed is rejected
        assertClose(2.0f, c.state.value.speed)
    }

    @Test
    fun `a clip without duration cannot be played`() {
        val (c, rec) = controller(0f)
        assertFalse(c.state.value.enabled)
        rec.calls.clear()
        c.play()
        c.tick(100_000_000L)
        assertFalse(c.state.value.playing)
        assertTrue(rec.calls.isEmpty())
        assertTrue(c.state.value.message.contains("zero duration"))
    }

    @Test
    fun `an asset without clips reports that instead of playable state`() {
        val c = AnimationController { _, _ -> }
        c.bindAsset("empty.glb", emptyList(), emptyList())
        assertEquals(0, c.state.value.clipCount)
        assertFalse(c.state.value.enabled)
        assertTrue(c.state.value.message.contains("no animation clips"))
        c.play()
        assertFalse(c.state.value.playing)
    }

    @Test
    fun `a load failure is surfaced and never faked as playback`() {
        val c = AnimationController { _, _ -> }
        c.fail("Could not load clip.glb")
        assertEquals(0, c.state.value.clipCount)
        assertEquals("Could not load clip.glb", c.state.value.error)
        assertFalse(c.state.value.enabled)
    }

    @Test
    fun `progress reflects the current time`() {
        val (c, _) = controller(4f)
        c.seek(1f)
        assertClose(0.25f, c.state.value.progress)
        assertEquals("clip0", c.state.value.clipName)
    }
}
