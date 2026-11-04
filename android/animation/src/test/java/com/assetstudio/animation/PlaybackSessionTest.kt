package com.assetstudio.animation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PlaybackSessionTest {

    private class FakeRenderer(
        private val names: List<String>,
        private val durations: List<Float> = names.map { 0.8f },
        private val loaded: Boolean = true
    ) : PlaybackRenderer {
        override val hasAsset: Boolean get() = loaded
        override val clipCount: Int get() = if (loaded) names.size else 0
        override fun clipName(index: Int): String = names.getOrElse(index) { "" }
        override fun clipDuration(index: Int): Float = durations.getOrElse(index) { 0f }
        override fun apply(index: Int, seconds: Float): Boolean = loaded
    }

    private fun newSession(): PlaybackSession =
        PlaybackSession(AnimationController { _, _ -> })

    @Test
    fun `attach binds the clips reported by the renderer`() {
        val session = newSession()
        session.attach(FakeRenderer(listOf("Retarget_walk")), "walk.glb")
        val state = session.renderer.let { session.assetName to it.clipCount }
        assertEquals("walk.glb", state.first)
        assertEquals(1, state.second)
    }

    @Test
    fun `attach without a loaded asset reports a load failure`() {
        val session = newSession()
        session.attach(FakeRenderer(emptyList(), loaded = false), "broken.glb")
        val report = session.validate()
        assertEquals(0, report.clipCount)
        assertFalse(report.anyPlayback)
        assertTrue(report.note.contains("No animation clip"))
    }

    @Test
    fun `validation records the categories that really played`() {
        val session = newSession()
        session.attach(FakeRenderer(listOf("Retarget_idle")), "idle.glb")
        val report = session.validate()
        assertEquals(1, report.passed)
        assertEquals(setOf(PlaybackCategory.IDLE), session.validatedCategories())

        session.attach(FakeRenderer(listOf("Retarget_death")), "death.glb")
        session.validate()
        assertEquals(setOf(PlaybackCategory.IDLE, PlaybackCategory.DEATH), session.validatedCategories())
    }

    @Test
    fun `a failing clip is not recorded as validated`() {
        val session = newSession()
        session.attach(FakeRenderer(listOf("Retarget_run"), loaded = false), "run.glb")
        session.validate()
        assertTrue(session.validatedCategories().isEmpty())
    }

    @Test
    fun `detach clears the session evidence`() {
        val session = newSession()
        session.attach(FakeRenderer(listOf("Retarget_jump")), "jump.glb")
        session.validate()
        session.detach()
        assertEquals("", session.assetName)
        assertFalse(session.renderer.hasAsset)
        assertTrue(session.validatedCategories().isEmpty())
    }
}
