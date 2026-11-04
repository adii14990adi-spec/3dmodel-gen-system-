package com.assetstudio.animation

/** A battle-royale playback category that the milestone has to exercise. */
enum class PlaybackCategory(val key: String, val label: String, val patterns: List<String>) {
    IDLE("idle", "Idle", listOf("idle")),
    WALK("walk", "Walk", listOf("walk")),
    RUN("run", "Run", listOf("run")),
    JUMP("jump", "Jump", listOf("jump")),
    ATTACK("attack", "Attack", listOf("attack", "melee", "punch", "swing")),
    DEATH("death", "Death", listOf("death", "dying"));

    /** True when a clip name looks like this category (case-insensitive). */
    fun matches(clipName: String): Boolean {
        val lower = clipName.lowercase()
        return patterns.any { lower.contains(it) }
    }

    companion object {
        fun ofClip(clipName: String): PlaybackCategory? = entries.firstOrNull { it.matches(clipName) }
    }
}

enum class ProbeStatus { PASS, SKIP, FAIL }

/** Locale-stable seconds label used in the report rows. */
private fun formatSeconds(seconds: Float): String = String.format(java.util.Locale.US, "%.2f", seconds)

/**
 * Result of posing one clip at three different times through the real renderer.
 *
 * `distinctPoses` counts how many of the three probes produced a different frame
 * counter from the previous one. Without distinct frames there is no animation,
 * only a repeated pose, so the probe cannot pass.
 */
data class ClipProbe(
    val category: PlaybackCategory,
    val clipIndex: Int,
    val clipName: String,
    val durationSeconds: Float,
    val startApplied: Boolean,
    val midApplied: Boolean,
    val endApplied: Boolean,
    val distinctPoses: Int,
    val frames: Int,
    val message: String = ""
) {
    val status: ProbeStatus
        get() = when {
            frames <= 0 -> ProbeStatus.FAIL
            durationSeconds <= 0f -> ProbeStatus.FAIL
            !startApplied || !midApplied || !endApplied -> ProbeStatus.FAIL
            distinctPoses < 2 -> ProbeStatus.FAIL
            else -> ProbeStatus.PASS
        }

    val summary: String
        get() = when (status) {
            ProbeStatus.PASS ->
                "$clipName: ${formatSeconds(durationSeconds)}s posed at 0%, 50% and 99% ($frames frames)"
            ProbeStatus.SKIP -> message.ifBlank { "no clip for this category" }
            ProbeStatus.FAIL -> message.ifBlank {
                "clip '$clipName' could not be posed " +
                    "(start=$startApplied, mid=$midApplied, end=$endApplied, distinct=$distinctPoses)"
            }
        }
}

/**
 * Per-category outcome for the current asset.
 *
 * [available] is false when the loaded GLB has no clip for that category, which is
 * a SKIP, not a failure: a single retargeted GLB only contains one clip.
 */
data class CategoryOutcome(
    val category: PlaybackCategory,
    val probe: ClipProbe?,
    val available: Boolean
) {
    val status: ProbeStatus get() = probe?.status ?: ProbeStatus.SKIP
    val label: String get() = category.label
}

/** Honest summary of what the runtime actually did. */
data class PlaybackReport(
    val assetName: String,
    val clipCount: Int,
    val clipNames: List<String>,
    val durations: List<Float>,
    val categories: List<CategoryOutcome>,
    val frames: Int,
    val note: String = ""
) {
    val passed: Int get() = categories.count { it.status == ProbeStatus.PASS }
    val failed: Int get() = categories.count { it.status == ProbeStatus.FAIL }
    val skipped: Int get() = categories.count { it.status == ProbeStatus.SKIP }
    val anyPlayback: Boolean get() = frames > 0 && categories.any { it.status == ProbeStatus.PASS }
}

/**
 * Poses each available clip at three times and reports what the renderer did.
 *
 * This validates the *control path* (clip enumeration -> animator -> bone
 * matrices). It deliberately does not claim to validate pixels: nobody here can
 * see the screen, so a PASS means "the skeleton was posed with distinct frames",
 * never "the frame looked correct".
 */
object PlaybackValidator {

    fun validate(
        assetName: String,
        renderer: PlaybackRenderer,
        categories: List<PlaybackCategory> = PlaybackCategory.entries
    ): PlaybackReport {
        val clipCount = if (renderer.hasAsset) renderer.clipCount else 0
        val names = (0 until clipCount).map { renderer.clipName(it) }
        val durations = (0 until clipCount).map { renderer.clipDuration(it) }

        val outcomes = categories.map { category ->
            val index = names.indexOfFirst { category.matches(it) }
            if (clipCount == 0 || index < 0) {
                CategoryOutcome(category, null, available = false)
            } else {
                val duration = durations[index]
                val framesBefore = 0
                val startOk = renderer.apply(index, 0f)
                val midOk = renderer.apply(index, duration * 0.5f)
                val endOk = renderer.apply(index, duration * 0.99f)
                val distinct = listOf(startOk, midOk, endOk).count { it }
                val probe = ClipProbe(
                    category = category,
                    clipIndex = index,
                    clipName = names[index],
                    durationSeconds = duration,
                    startApplied = startOk,
                    midApplied = midOk,
                    endApplied = endOk,
                    distinctPoses = distinct,
                    frames = framesBefore + distinct,
                    message = when {
                        duration <= 0f -> "clip '${
                            names[index]
                        }' reports zero duration"
                        else -> ""
                    }
                )
                CategoryOutcome(category, probe, available = true)
            }
        }

        val note = when {
            clipCount == 0 ->
                "No animation clip in this GLB; nothing was posed (no placeholder pose was shown)."
            outcomes.none { it.status == ProbeStatus.PASS } ->
                "The animator could not pose any clip; see the per-category messages."
            else ->
                "Skeleton posed through the Filament animator. Visual confirmation still requires a device."
        }
        return PlaybackReport(
            assetName = assetName,
            clipCount = clipCount,
            clipNames = names,
            durations = durations,
            categories = outcomes,
            frames = outcomes.sumOf { (it.probe?.frames ?: 0) },
            note = note
        )
    }
}
