package com.assetstudio.animation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PlaybackValidatorTest {

    private class FakeRenderer(
        private val names: List<String>,
        private val durations: List<Float> = names.map { 1.0f },
        private val loaded: Boolean = true,
        private val accepts: Boolean = true
    ) : PlaybackRenderer {
        val applied = mutableListOf<Pair<Int, Float>>()
        override val hasAsset: Boolean get() = loaded
        override val clipCount: Int get() = if (loaded) names.size else 0
        override fun clipName(index: Int): String = names.getOrElse(index) { "" }
        override fun clipDuration(index: Int): Float = durations.getOrElse(index) { 0f }
        override fun apply(index: Int, seconds: Float): Boolean {
            if (!accepts) return false
            applied.add(index to seconds)
            return true
        }
    }

    private val sixClips = listOf("Retarget_idle", "Retarget_walk", "Retarget_run",
        "Retarget_jump", "Retarget_attack", "Retarget_death")

    @Test
    fun `all six categories pass when the animator poses three distinct times`() {
        val renderer = FakeRenderer(sixClips)
        val report = PlaybackValidator.validate("character.glb", renderer)
        assertEquals(6, report.clipCount)
        assertEquals(6, report.passed)
        assertEquals(0, report.failed)
        assertTrue(report.anyPlayback)
        assertEquals(18, renderer.applied.size) // 3 probes x 6 clips
        val attack = report.categories.first { it.category == PlaybackCategory.ATTACK }
        assertEquals("Retarget_attack", attack.probe!!.clipName)
        assertEquals(1.0f, attack.probe!!.durationSeconds, 0.0001f)
        assertEquals(ProbeStatus.PASS, attack.status)
    }

    @Test
    fun `a renderer that cannot pose the skeleton fails the probe`() {
        val report = PlaybackValidator.validate("x.glb", FakeRenderer(listOf("idle"), accepts = false))
        assertEquals(1, report.failed)
        assertEquals(ProbeStatus.FAIL, report.categories.first().status)
        assertFalse(report.anyPlayback)
        assertTrue(report.categories.first().probe!!.summary.contains("could not be posed"))
    }

    @Test
    fun `zero duration fails instead of showing a frozen pose`() {
        val report = PlaybackValidator.validate("x.glb", FakeRenderer(listOf("walk"), listOf(0f)))
        assertEquals(ProbeStatus.FAIL, report.categories.first().status)
        assertTrue(report.categories.first().probe!!.message.contains("zero duration"))
    }

    @Test
    fun `no loaded asset skips every category with an honest note`() {
        val report = PlaybackValidator.validate("x.glb", NoPlaybackRenderer)
        assertEquals(0, report.clipCount)
        assertEquals(0, report.passed)
        assertEquals(6, report.skipped)
        assertFalse(report.anyPlayback)
        assertTrue(report.note.contains("No animation clip"))
    }

    @Test
    fun `category matching understands the exported clip names`() {
        assertEquals(PlaybackCategory.WALK, PlaybackCategory.ofClip("Retarget_walk crouching forward"))
        assertEquals(PlaybackCategory.RUN, PlaybackCategory.ofClip("Retarget_rifle run"))
        assertEquals(PlaybackCategory.DEATH, PlaybackCategory.ofClip("Retarget_dying"))
        assertEquals(PlaybackCategory.ATTACK, PlaybackCategory.ofClip("Retarget_melee"))
        assertEquals(null, PlaybackCategory.ofClip("Retarget_swim"))
    }

    @Test
    fun `clips that do not cover a category are skipped not failed`() {
        val report = PlaybackValidator.validate("only-idle.glb", FakeRenderer(listOf("Retarget_idle")))
        assertEquals(1, report.passed)
        assertEquals(5, report.skipped)
        assertEquals(0, report.failed)
    }
}
