# Android runtime animation playback

This milestone turns the existing Filament viewport into a **validated animation
player**: a GLB's clips are enumerated from its own animator, the transport
(play / pause / stop / seek / loop / speed / clip selection) drives real skeleton
poses, and the app records what the animator actually did instead of assuming it.

It does not add a second renderer and it does not touch the generated assets.

## What existed before (audit)

| Component | State before | Verdict |
| --- | --- | --- |
| `renderer/FilamentViewport.kt` | `ModelViewer` + gltfio `Animator`; `applyAnimation()` called `animator.applyAnimation` + `updateBoneMatrices` inside `runCatching` | Real animator path, but failures were swallowed and success was unobservable |
| `animation/AnimationController.kt` | Transport state machine (play/pause/stop/restart/seek/loop/speed), `apply` callback into the renderer | Correct design, untested, no clip durations in state, no failure state |
| `app/StudioViewModel.kt` | `animationController` bound to `viewport?.applyAnimation` | Wired, but clip binding was duplicated in the screen and read the animator three times per call |
| `app/screens/ScreensB.kt` (`AnimationPlayerScreen`) | Play / Pause / Stop / Restart buttons, seek slider, loop toggle, speed chips, clip chips | UI existed; no evidence that anything actually played |
| `android/animation/src/test` | empty (JUnit already declared) | No tests |
| `scripts/check_android_sources.py` | module/screen/version/secret/lexical checks | No playback contract |

So the gap was not "make it play" — it was **make playback observable, testable and
honest**, and close the places where a missing clip could still look playable.

## Filament API support (1.75.1)

The playback path uses the gltfio animator that ships with
`com.google.android.filament:gltfio-android:1.75.1`:

| API | Used for | Status |
| --- | --- | --- |
| `ModelViewer.animator` (`Animator?`) | entry point; `null` when the GLB has no animation | available |
| `Animator.getAnimationCount()` | clip enumeration from the file | available |
| `Animator.getAnimationName(int)` | clip labels (never taken from catalog metadata) | available |
| `Animator.getAnimationDuration(int)` | clip length in seconds | available |
| `Animator.applyAnimation(int, float)` | pose at time *t* | available |
| `Animator.updateBoneMatrices()` | push the pose into the skin | available |

**Verification limit:** the sandbox has no JDK, no Android SDK and no network to
Maven, so the AAR could not be resolved and the module could not be compiled here.
The API list above is the surface already used by `FilamentViewport` before this
milestone; the milestone kept every call and only added return values and
counters. `android-build.yml` compiles it on a runner with the SDK.

## What was implemented

1. **`PlaybackRenderer`** (`:animation`) — the four methods a renderer must expose
   (`hasAsset`, `clipCount`, `clipName`, `clipDuration`, `apply`), with `apply`
   returning `true` only when the pose reached the skeleton. `NoPlaybackRenderer`
   never claims success. This keeps the module free of Filament so it can be unit
   tested on the JVM.
2. **`PlaybackValidator` / `PlaybackValidation.kt`** — poses every available clip at
   0 %, 50 % and 99 % of its duration and records: start/mid/end accepted, distinct
   poses, frames applied. A clip that reports zero duration, that the animator
   refuses, or that yields fewer than two distinct poses is a **FAIL**, never a
   silent pass. Categories: `idle`, `walk`, `run`, `jump`, `attack`, `death`.
3. **`PlaybackSession`** — binds a loaded GLB to the transport, using the animator's
   clip names and durations (not the catalog), and accumulates the categories that
   were really validated in this process. `detach()` clears the evidence.
4. **`AnimationController`** — added `bindAsset`, `fail`, per-clip durations,
   `progress`, `clipName`, a message/error state, negative-delta and non-loop end
   guards. The transport logic (wrapping, clamping, speed) is unchanged in spirit
   and now specified by tests.
5. **`FilamentViewport`** — `applyAnimation` returns a real `Boolean`, increments
   `poseFrames`, and stores `lastAnimationError`; `hasAsset()` reports whether a GLB
   is loaded. The Choreographer loop still feeds `animationTick` → `controller.tick`.
6. **`StudioViewModel`** — a `PlaybackRenderer` adapter over the live viewport,
   `loadPlaybackAsset(path, label)`, `validatePlayback()` and the session's evidence.
7. **`AnimationPlayerScreen`** — loads a cached GLB through the session, shows the
   "Runtime playback validation" card with per-category PASS/SKIP/FAIL rows, the
   frame count and the honest note, plus the six category chips for the current
   session.

## How to validate on a device

```bash
# from the repo root, with a device/emulator attached
cd android && ./gradlew :app:installDebug
adb shell am start -n com.assetstudio.app/.MainActivity
# In the app: Animation Player -> pick the rigged character -> tap a playable clip
# -> "Validate loaded clip". Repeat for idle/walk/run/jump/attack/death.
# The chips fill in as each category is really posed.
adb logcat -s Filament Animator   # animator/engine diagnostics
```

The GLBs to load are the exported ones (`processed/animations/<asset_id>/<asset_id>.glb`);
`scripts/validate_playback_assets.py` lists one per category and validates its data.

## Automated tests

| Test | Where | Runs here? |
| --- | --- | --- |
| `AnimationControllerTest` (12 cases: bind, tick, pause, loop, non-loop end, seek clamp, stop, clip select, speed, zero duration, empty asset, failure, progress) | `android/animation/src/test` (JVM) | **No** — needs JDK/Android SDK; CI runs `:animation:testDebugUnitTest` |
| `PlaybackValidatorTest` (6 cases incl. "renderer cannot pose" and "zero duration") | same | **No** — same reason |
| `PlaybackSessionTest` (5 cases: attach, load failure, evidence merge, failing clip, detach) | same | **No** — same reason |
| `tests/unit/test_android_playback.py` — static contract + proof the contract check fails when broken | pytest | **Yes** |
| `scripts/validate_playback_assets.py` — real GLB clip data for the six categories | pytest + workflow | **Yes** |

## Honest results and limitations

Executed in this environment:

* `scripts/check_android_sources.py` → **passed** (8 modules, 12 screens, 26 Kotlin
  files lexically balanced, playback contract satisfied).
* `scripts/validate_playback_assets.py --strict` → **6/6 PASS** (idle 2.10 s,
  walk 1.03 s, run 0.73 s, jump 0.97 s, attack 1.13 s, death 1.90 s; 21 animated
  joints each; all channels target skin joints; times strictly increasing).

Not executed, and therefore **not claimed**:

* No Gradle build, no APK, no instrumented test and no rendering: there is no JDK,
  Android SDK or emulator in this sandbox. `android-build.yml` is the place where
  compilation and the JVM unit tests happen.
* No pixel-level verification. A `PASS` from the app means "the animator accepted
  three distinct times and the bone matrices were refreshed" — it does not mean the
  frame looked correct.
* The exported GLBs contain **one clip each** (one retargeted animation per file),
  so a single load validates exactly one category; the session chips fill in as the
  user loads the others. A combined multi-clip character GLB would make the six-way
  sweep automatic — that is a pipeline follow-up, not a renderer gap.
* Root motion is baked into the hips channel: the character will translate inside
  the model viewer, but the app does not yet compensate the camera or drive gameplay
  from it.
