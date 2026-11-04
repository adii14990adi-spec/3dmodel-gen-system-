# Android viewer

## Exact version matrix (pinned)

| Component | Version |
|---|---|
| Gradle wrapper | 8.9 (`gradle-8.9-bin.zip`) |
| Android Gradle Plugin | 8.7.3 |
| Kotlin | 2.0.21 |
| KSP | 2.0.21-1.0.25 |
| Compose BOM | 2024.12.01 |
| Filament / gltfio / filament-utils | 1.75.1 |
| Room | 2.6.1 |
| DataStore Preferences | 1.1.1 |
| Retrofit | 2.11.0 · OkHttp 4.12.0 · kotlinx-serialization 1.7.3 |
| Navigation Compose | 2.8.5 · Activity Compose 1.9.3 · Lifecycle 2.8.7 |
| compileSdk / targetSdk / minSdk | 35 / 35 / 26 |
| JDK | 17 |
| NDK / OpenGL ES | Filament AARs; OpenGL ES 3.0 required |

Filament 1.75.1 is the newest Maven Central release built against `compileSdk 34`, so it is the highest version compatible with this pinned AGP 8.7.3 / `compileSdk 35` matrix. Filament 1.76.1+ requires `compileSdk 37` and a correspondingly newer AGP/Gradle/SDK platform.

All versions live in `android/gradle/libs.versions.toml`; `gradle/wrapper/gradle-wrapper.jar` and `gradlew` come from the Gradle v8.9.0 source tag. Dependencies are pinned (no dynamic `+` versions).

## Build

```bash
# Prerequisites: JDK 17, Android SDK with platform 35 + build-tools 35.0.0
export ANDROID_HOME=$HOME/Android/Sdk        # or ANDROID_SDK_ROOT
cd android
./gradlew :app:assembleDebug --stacktrace
./gradlew :app:testDebugUnitTest
./gradlew :app:assembleRelease               # needs keystore.properties for signing
adb install app/build/outputs/apk/debug/app-debug.apk
```

`keystore.properties` (git-ignored) contains `storeFile`, `storePassword`, `keyAlias`, `keyPassword`. CI writes it from `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`. Without it, `assembleRelease` still builds an **unsigned** APK. No keystore or credential is committed.

## Modules

`:core` domain models + metadata parser · `:data` Room/DataStore · `:asset-library` Retrofit API, streaming SHA-256 downloader, SAF import · `:renderer` Filament viewport + real glTF debug overlays · `:skeleton` hierarchy inspector · `:animation` playback state machine · `:inspector` geometry/material/validation inspectors · `:app` Compose UI.

## Twelve screens (all real)

1. **Home** — live counts from `/api/v1/health`, offline cache size, navigation to all screens.
2. **Asset Library** — search/sort, size/info/status, download with progress, GLB/glTF import via SAF, open actions.
3. **Model Browser** — models with humanoid flag, links to geometry/materials/skeleton/report inspectors.
4. **3D Viewport** — SurfaceView + `ModelViewer` (Filament), touch orbit/zoom/pan, reset, six axis views, perspective/orthographic, grid/axes/ground/skeleton overlays, HDR/shadows/exposure, PBR/textured/solid/wireframe/x-ray/normal modes, Performance/Balanced/High, FPS overlay.
5. **Skeleton Inspector** — only the loaded skeleton; real bone names, hierarchy tree, parent/child, transforms, rest head, root, skin links, influence counts, unweighted/invalid counts, highlight/select, overlay and skeleton-only. Static models show an honest empty state.
6. **Animation Library** — clips, retarget status, target id, download/open.
7. **Animation Player** — play/pause/stop/restart/loop/scrub, speeds `0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0`, current/total time, clip selection, download, playable-only list, incompatible reasons, "No animation clips available" with disabled controls.
8. **Material Inspector** — base colour, metallic, roughness, emissive, alpha, normal/AO/texture slots, texture dimensions/format, missing-texture warnings.
9. **Geometry Inspector** — real mesh/primitive/vertex/triangle/index/material/texture counts, UV sets, normals/tangents, morph targets, bbox/dimensions, embedded clips.
10. **Validation Report** — Khronos + pipeline checks with severity, description, asset, location, suggested fix.
11. **Downloads** — cached assets, progress, delete local, share original via FileProvider, offline note.
12. **Settings** — API URL/key, quality, render mode, exposure, all viewport toggles, loop/speed, thermal-aware pacing, large-file warnings, FPS overlay; every switch writes DataStore and applies to the viewport.

## Runtime behaviour

- Loading is async with progress; downloads stream to a `.part` file and are renamed atomically after SHA-256 verification; cancellation cleans up the part file.
- GPU resources are released explicitly in `FilamentViewport.destroy()` on lifecycle `onDestroy`.
- Quality caps Performance to ~30 fps under thermal pressure; Balanced is the default. No geometry reduction is applied.
- KTX2/Basis is disabled unless runtime decoding is available (`runtime.ktx2_enabled: false`).
- Debug overlays are genuine glTF LINE/TRIANGLES primitives built by `DebugGlbBuilder` and loaded through `gltfio`, not a 2D canvas mock.
- Optional Filament APIs (orthographic projection, exposure, HDR/shadows switches, MSAA) are wrapped in `runCatching` so a renderer backend that rejects one call degrades gracefully instead of crashing.

Runtime animation playback (transport, validation, limits) is documented separately in
[`android-playback.md`](android-playback.md).

**Not verified here:** no JDK/Android SDK/device in the sandbox, so no APK was compiled and no device tests were run. `scripts/check_android_sources.py` validates module structure, all 12 screen destinations, version pins, absence of hardcoded secrets and Kotlin lexical balance.

## Step 1 build fix and foundation audit (2026-11-03)

### Defect 1 — confirmed CI failure (`:inspector:compileDebugKotlin`)

`android/inspector/src/main/java/com/assetstudio/inspector/Inspectors.kt` line 22 closed
`Row("Dimensions", …)` with an extra `)`, so the list-closing `)` on line 23 was orphaned. Kotlin reported it on the
*following* line:

```
Inspectors.kt:23:5 Syntax error: Expecting member declaration.
```

Fix: removed the surplus parenthesis (one character). `GeometryInspector.rows()` behaviour is unchanged — 14 rows,
same labels, same values.

### Defect 2 — latent compile error that the first failure masked (`:app`)

`:app` never compiled in CI because it depends on `:inspector`. After rebuilding the dependency graph by inspection,
`StudioViewModel.importLocal()` contained a shadowed implicit lambda parameter:

```kotlin
repo.importLocal(uri, name).onSuccess { _ui.update { it.copy(message = "Imported ${it.name}") } }
```

The inner `it` is a `StudioUiState` (no `name` property), so `${it.name}` is an unresolved reference. Fix: the outer
lambda parameter is now named (`entity`) and the message uses `entity.name`. No behaviour change.

### Static guard added

`scripts/check_android_sources.py` now lexes every `android/**/*.kt` file and fails on unbalanced brackets, unclosed
strings/block comments and unterminated `${…}` templates, skipping string bodies, raw strings, char literals and
nested comments. It reproduces the original failure exactly when the extra `)` is reintroduced, so this class of
syntax error is caught without a JDK. It runs in `validation.yml`, in the new pre-Gradle step of `android-build.yml`,
and as a pytest case (`tests/unit/test_extras.py`).

### Audit results

| Area | Finding | Status |
|---|---|---|
| Gradle wrapper | Pinned to `gradle-8.9-bin.zip`, `distributionSha256Sum` not set, wrapper jar and `gradlew` (mode 755) tracked, wrapper-validation step in CI | OK |
| Plugin compatibility | AGP 8.7.3 requires Gradle ≥ 8.9 and JDK 17 — both pinned; Kotlin 2.0.21 with the Compose compiler plugin 2.0.21; KSP 2.0.21-1.0.25 matches Kotlin | OK |
| Toolchain | `compileOptions` 17 + `kotlinOptions.jvmTarget = "17"` in all eight modules; CI uses Temurin 17 | OK |
| Module graph | `:app` → all seven modules; `:asset-library` → `:core`, `:data` (+ Room runtime, added previously because `AssetDatabase` appears in repository signatures); no cycles | OK |
| API leaking across `implementation` | `SkeletonInspector.overlayLines(): List<DebugLine>` returns a `:renderer` type while `:skeleton` declares `:renderer` as `implementation`. It compiles only because `:app` also depends on `:renderer` directly. Latent fragility — noted, not changed in Step 1 | Documented |
| Namespaces / IDs | Unique namespace per module; `applicationId com.assetstudio.app` with `.debug` suffix; FileProvider authority `${applicationId}.fileprovider` matches the runtime `"${ctx.packageName}.fileprovider"` | OK |
| SDK configuration | `compileSdk`/`targetSdk` 35, `minSdk` 26, `buildConfig` + `compose` features on; CI installs `platforms;android-35` and `build-tools;35.0.0` | OK |
| Hardcoded paths | None; `keystore.properties` is optional and git-ignored; API base URL defaults to the `example.invalid` placeholder and is user-configurable at runtime | OK |
| Secrets | No keys, keystores or tokens in the tree; secret-pattern scan is part of the static checks; R8/`BuildConfig` carry no credentials | OK |
| GitHub Actions | `android-build.yml`: JDK 17 + SDK 35 + wrapper validation + optional signing + artifact upload; `validation.yml` mirrors the local pipeline; `release.yml` gated on secrets | OK (added pre-Gradle static step) |
| Unit tests in `android/` | There are **no** JVM unit test sources yet (`./gradlew test` runs an empty suite) | Gap, documented |

### Unexecuted in this environment

`./gradlew :inspector:compileDebugKotlin`, `./gradlew test` and `./gradlew assembleDebug` all stop before any
compilation with `javax.net.ssl.SSLHandshakeException` → `java.io.EOFException: SSL peer shut down incorrectly` while
the wrapper fetches `gradle-8.9-bin.zip`; `ANDROID_HOME` is empty and no Android SDK/`sdkmanager` is installed. A JDK
was provisioned locally to reach that point. The authoritative build runs in `android-build.yml` on a hosted runner.
