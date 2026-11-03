# 3D Asset Studio

**An AI-assisted 3D asset processing, animation validation, retargeting and Android inspection platform.**

```
Generate  →  Upload  →  Detect  →  Process  →  Validate  →  Retarget  →  Export  →  Inspect
```

3D Asset Studio is a modular platform for importing, processing, validating, versioning and inspecting 3D models and
animations. It combines a path-triggered GitHub Actions pipeline, a real pure-Python glTF/BVH processing engine,
headless Blender conversion, a metadata API with signed downloads and persistent object storage, and a native
Kotlin/Compose/**Google Filament** viewer for Android.

> **Honest status.** The Python pipeline, Khronos validation, FastAPI/SQLite metadata API, signed downloads,
> operations console, native Android source tree and automated tests are **implemented and executed in this
> workspace** — re-verified on 2026-11-03 (42 tests green, 8 models and 10 animations processed, 16 exported GLBs at
> 0 Khronos errors). **A compiled APK, physical-device tests and live S3/CI runs are not available here**, because this
> sandbox has no JDK, Android SDK or device and no cloud credentials. Every unexecuted item and its exact remaining
> setup steps are listed in [`docs/limitations.md`](docs/limitations.md). Nothing below is marked "done" unless its
> source, integration and tests exist in this repository.

---

## 1. Project vision

A single, reliable workflow where a user can:

- Generate 3D assets with an AI agent, or hand-author and upload them.
- Commit assets to GitHub (or push them through automation) and have them detected automatically.
- Process them with Blender and custom Python tooling — inspection, conversion, optimisation, reporting.
- Validate mesh, material, texture, skeleton, skin-weight and animation integrity.
- Check whether an animation is actually compatible with a character rig, and retarget it when it is.
- Export optimised GLB/glTF runtime assets with versioned manifests.
- Inspect models, skeletons, materials and animations inside a native Android application.

**Core principle:** asset generation and processing happen *outside* the Android app. Android renders, inspects,
previews and manages already-processed assets. The APK contains no generation logic and **no secrets**.

### Where AI generation fits today

The AI-generation stage is a **separate concern and is not implemented in this repository**. This codebase owns
everything downstream of it: whatever produces an asset writes real bytes into `assets/`, and the pipeline treats
them exactly like a manual upload. The ingestion, processing, validation and inspection layers are deliberately
model-agnostic so a future generator can be plugged in without touching them.

---

## 2. System architecture

```
                     USER  /  AI AGENT  /  MANUAL COMMIT
                                    │
                       assets/models/**  assets/animation/**
                                    │
                        GitHub Actions (path triggers)
                     asset-processing.yml   animation-processing.yml
                                    │
                     scripts/changed_files.py  (changed-file detection)
                                    │
        ┌───────────────────────────┴────────────────────────────┐
        │                    processing/                         │
        │  glb.py  bvh.py  compatibility.py  retarget.py          │
        │  report.py  manifest.py  validate.py  publish.py        │
        │  blender/*.py  (headless, for FBX/OBJ/STL/PLY/DAE)      │
        └───────────────────────────┬────────────────────────────┘
                                    │
             PASS / WARNING / ERROR validation + Khronos glTF validator
                                    │
        ┌───────────────────────────┼────────────────────────────┐
        │                           │                            │
  processed/*.glb            manifests/*.json              reports/*.json
  (runtime assets)           (versioned index)             (diagnostics)
        │                           │                            │
  object storage  ◄────────  backend/ (FastAPI + SQLite)  ──────►│
  (S3-compatible)            /api/v1 · auth · signed URLs        │
                                    │                            │
                          Kotlin / Compose / Filament            │
                               Android viewer  ◄─────────────────┘
```

**Key architectural rules**

1. Source assets under `assets/` are **read-only**; only copies in `processed/` are ever written or repaired.
2. GitHub Actions runners are **temporary processing environments, not servers**. Persistent runtime assets belong in
   object storage; metadata belongs in the API database; manifests and reports are release artifacts.
3. The metadata API never guesses asset properties — it reads published manifests only.
4. GLB/glTF is the only runtime delivery format. Filament does **not** load arbitrary FBX/BVH files.
5. Folder names are organisational metadata only; the pipeline reads actual bytes.

---

## 3. Major components

### A. Asset ingestion

| Capability | Status |
|---|---|
| Added / modified detection via Git diff (`scripts/changed_files.py`) | ✅ implemented |
| Path-triggered workflows + `workflow_dispatch` (all / explicit paths) | ✅ implemented, not run in CI here |
| Format identification from bytes, not extensions alone | ✅ implemented |
| Stable asset IDs derived from source path + content hash | ✅ implemented |
| Originals preserved; processed copies generated separately | ✅ implemented |
| Animation → intended character association through runtime targets | ✅ implemented |
| Overwrite prevention via content-addressed IDs | ✅ implemented |

Supported inputs: models `.glb .gltf .fbx .obj .stl .ply`; animations `.glb .gltf .fbx .bvh .dae`.
Pure Python handles GLB/glTF/BVH; FBX/OBJ/STL/PLY/DAE require the Blender path in CI.

### B. Processing engine (`processing/`)

Real implementations, no stubs:

- `glb.py` — glTF 2.0 reader/writer, accessor decoding, full inspector.
- `bvh.py` — hierarchy, channels, offsets, frame/fps/duration validation.
- `compatibility.py` — canonical humanoid role mapping with hierarchy, spatial and ambiguity checks.
- `retarget.py` — BVH → glTF animation samplers written onto a copied target character.
- `report.py` / `manifest.py` / `publish.py` — diagnostic reports, versioned manifests, atomic publication.
- `validate.py` — official Khronos `gltf-validator@2.0.0-dev.3.10` wrapper, with honest "unavailable" fallbacks.
- `blender/*.py` — headless inspection, conversion and retargeting for formats `bpy` must read.

Inspection covers meshes, primitives, vertices, triangles, indices, materials, textures (dimensions and missing
references), UV sets, normals, tangents, morph targets, bounding boxes, skins, joints/weights, inverse bind matrices,
influence counts, unweighted vertices, skinned-mesh links, and embedded clips (name, duration, fps, frame range,
channels, animated bones, root motion, interpolation).

Limits are enforced from [`config/studio.yaml`](config/studio.yaml): 500 MiB source, 8 M vertices, 128 MiB textures,
900 s timeout, 20 000 animation frames.

### C. Skeleton and animation system

Skeleton hierarchy extraction, bone-name/hierarchy analysis, humanoid rig detection, bone mapping, animation
compatibility checks, BVH parsing, retargeting, runtime export and clip validation.

A skeleton alone never guarantees correct deformation, and matching bone names never prove compatibility. Retargeting
requires role coverage (≥ 0.75), ≥ 12 mapped bones, unambiguous roles, hierarchy and rest-transform agreement.
Foot sliding and IK correction are explicitly **not** solved.

### D. Validation and quality control

| Status | Meaning |
|---|---|
| `PASS` | All required checks passed |
| `WARNING` | Usable with known, reported limitations |
| `ERROR` | A required check failed; not published as a successful runtime asset |

Categories: file integrity, format compatibility, mesh validity, material validity, texture references, skeleton
integrity, skin-weight validity, animation integrity, retargeting compatibility, runtime GLB/glTF validity.

Every report records asset ID, original filename, timestamp, tool versions, status, warnings, errors, exported file
references and duration. Invalid assets are never published as playable outputs.

### E. Automation (`.github/workflows/`)

| Workflow | Trigger | Purpose | Executed here |
|---|---|---|---|
| `asset-processing.yml` | push to `assets/models/**`, `processing/**`, `config/**` | model inspection, conversion, manifests, artifacts | ⚠️ no runner available |
| `animation-processing.yml` | push to `assets/animation/**`, characters, mappings | compatibility, retarget, per-clip GLBs | ⚠️ no runner available |
| `validation.yml` | push/PR (asset binaries ignored) | full pipeline + Khronos + pytest, mirrors local commands | ⚠️ no runner available |
| `android-build.yml` | push/PR on `android/**`, manual | `assembleDebug`, optional signed release | ⚠️ no runner available |
| `release.yml` | tag `v*`, manual | signed APK + publication bundle + GitHub release | ⚠️ no runner available |

With `ASSET_STUDIO_S3_*` secrets set, `scripts/publish_storage.py` uploads to a staging prefix, verifies sizes, then
atomically copies into the final prefix and publishes `manifests/releases/latest.json`. See
[`docs/actions.md`](docs/actions.md) and [`docs/storage.md`](docs/storage.md).

### F. Native Android application (`android/`)

Eight Gradle modules — `app`, `core`, `data`, `asset-library`, `renderer`, `skeleton`, `animation`, `inspector` —
built with Kotlin 2.0.21, Jetpack Compose, Filament 1.75.1 (`gltfio`), Room, DataStore, Retrofit/OkHttp and SAF.

Twelve real screens, every control performs a real action:

Home · Asset Library · Model Browser · 3D Viewport · Skeleton Inspector · Animation Library · Animation Player ·
Material Inspector · Geometry Inspector · Validation Report · Downloads · Settings

- **Viewport** — orbit/zoom/pan/reset, perspective/orthographic, six axis views, grid/axes/ground overlays (real glTF
  line geometry), skeleton overlay, exposure/lighting/shadows, PBR/textured/solid/wireframe/x-ray/normal modes,
  FPS overlay and thermal-aware quality tiers.
- **Inspectors** — real counts and names from pipeline metadata; static models honestly report "no skeleton" and
  "no clips" instead of fabricating data.
- **Animation player** — play/pause/stop/restart/loop/scrub, speeds 0.25–2.0, clip selection, current/total time,
  download-on-demand and explicit incompatibility reasons.

---

## 4. Repository structure

Actual layout (differs from early planning documents — `processing/` is the pipeline package, and the Android app is
split into eight modules):

```
3dmodel-gen-system-/
├── assets/                      source assets (read-only, small A–L fixtures tracked)
│   ├── models/{characters,environments,props,modular}/
│   ├── animation/{locomotion,combat,other}/
│   └── textures/
├── processing/                  pure-Python pipeline + headless Blender scripts
│   ├── glb.py bvh.py compatibility.py retarget.py
│   ├── report.py manifest.py validate.py publish.py
│   ├── common.py cli.py
│   └── blender/{inspect_model,inspect_animation,convert_model,retarget}.py
├── backend/                     FastAPI app, SQLite sync, storage drivers, security, static console
├── android/                     app, core, data, asset-library, renderer, skeleton, animation, inspector
├── config/                      studio.yaml, bone_mappings/humanoid.json
├── scripts/                     generate_sample_assets, changed_files, publish_storage,
│                                check_android_sources, run_pipeline
├── tests/                       unit, integration, pipeline
├── tools/gltf-validator/        pinned official Khronos validator
├── docs/                        architecture, processing, animation, api, android, storage,
│                                actions, security, testing, troubleshooting, limitations
├── .github/workflows/           5 workflows
└── processed/ manifests/ reports/ local/    generated outputs (git-ignored)
```

---

## 5. Asset organisation rules

```
assets/models/characters/player.glb
assets/models/environments/forest.glb
assets/models/props/crate.glb
assets/animation/locomotion/idle.bvh
assets/animation/locomotion/run.fbx
assets/animation/combat/attack.bvh
```

Models are grouped by category, animations by animation type. A manifest associates each animation with its source
character, target rig, processing version and exported runtime asset. These are organisational conventions only — no
behaviour is inferred from a folder or file name.

The 18 A–L fixtures under `assets/` are real generated GLB/BVH bytes (~840 KiB total), committed intentionally so
tests and workflows are reproducible; `scripts/generate_sample_assets.py` regenerates them. Large processed outputs
(`processed/`, `manifests/`, `reports/`, `local/`) are git-ignored.

---

## 6. Quick start (local development, verified)

Requires Python 3.11+, Node 22+, and optionally Blender 4.2 LTS for FBX/OBJ/STL/PLY/DAE conversion.

```bash
git clone <this-repo> && cd 3dmodel-gen-system-
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
npm ci --prefix tools/gltf-validator          # official Khronos validator

# 1. Generate the real A–L verification dataset (source files, git-ignored)
.venv/bin/python scripts/generate_sample_assets.py

# 2. Process models, then animations (models must exist for compatibility checks)
.venv/bin/python -m processing.cli process-models --all       # 8 models: 6 PASS, 2 ERROR (deliberate)
.venv/bin/python -m processing.cli process-animations --all   # 10 animations: 9 runtime GLBs, 1 incompatible
.venv/bin/python -m processing.cli build-index                # SQLite + manifests

# 3. Validate every processed GLB with the official Khronos validator
.venv/bin/python -m processing.cli validate                   # 16 valid GLBs: 0 errors

# 4. Run the metadata API and operations console
ASSET_STUDIO_API_KEY=change-me .venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 8000
#    http://localhost:8000/          read-only console
#    http://localhost:8000/docs      OpenAPI
#    http://localhost:8000/api/v1/health

# 5. Tests
.venv/bin/python -m pytest -q                                 # 42 passed
.venv/bin/python scripts/check_android_sources.py             # no JDK required
```

`processing.cli status` reports which optional tools are available (Blender binary, `bpy`, Khronos validator,
Java/Gradle/Android SDK).

### GitHub upload and processing

```bash
# Commit SOURCE assets only; generated GLBs/DB/reports are git-ignored on purpose.
git add assets/models/characters/hero.glb assets/animation/locomotion/run.bvh
git commit -m "asset: add hero and run"
git push origin <your-branch>
```

---

## 7. Metadata API

Base path `/api/v1`. Set `ASSET_STUDIO_API_KEY` to require the `X-API-Key` header; rate limiting defaults to
120 requests/minute/IP.

| Endpoint | Purpose |
|---|---|
| `GET /health` | status, counts, auth mode, storage driver, validator |
| `GET /assets` | list (`kind`, `search`, `sort`, `limit`) |
| `GET /assets/{asset_id}` | versioned manifest |
| `GET /assets/{asset_id}/metadata` | full inspection metadata |
| `GET /assets/{asset_id}/download` | processed GLB (integrity header, signed URL supported) |
| `POST /assets/{asset_id}/signed-url` | short-lived HMAC-signed URL |
| `GET /models` · `GET /models/{model_id}` | model list/detail |
| `GET /animations` · `GET /animations/{animation_id}` | animation list/detail |
| `GET /models/{model_id}/compatible-animations` | compatibility + **playable** status + reasons |
| `GET /processing-jobs/{job_id}` | job status |
| `GET /releases/latest` | latest publication |
| `GET /reports/{kind}/{asset_id}` | generated report |

**Playability is stricter than compatibility:** a clip is playable only when skeleton compatibility passed *and* a
validated runtime GLB exists for that exact `target_character_id`. Merely "compatible" clips are never offered for
playback. See [`docs/api.md`](docs/api.md).

---

## 8. Android build and device target

```bash
# Requires JDK 17 + Android SDK 35 (NOT available in this sandbox)
cd android
./gradlew :app:assembleDebug           # debug APK
./gradlew :app:testDebugUnitTest       # JVM unit tests
# Signed release: create keystore.properties from CI secrets (see docs/android.md)
```

| Component | Version |
|---|---|
| Gradle / AGP / Kotlin | 8.9 / 8.7.3 / 2.0.21 |
| KSP / Compose BOM | 2.0.21-1.0.25 / 2024.12.01 |
| Filament | 1.75.1 (`filament-android`, `gltfio-android`, `filament-utils-android`) |
| Room / DataStore | 2.6.1 / 1.1.1 |
| Retrofit / OkHttp | 2.11.0 / 4.12.0 |
| compileSdk / minSdk / JDK | 35 / 26 / 17 |

**Reference device for performance work:** Redmi 15 5G — Android 16, Snapdragon 6s Gen 3, 8 GB RAM. Optimisation
priorities: efficient GLB loading, texture resolution and compression where the runtime supports it, GPU-friendly
materials, bounded mesh complexity, memory-conscious loading with explicit Filament resource release, no background
work while idle, responsive UI during load, and thermal-aware frame pacing. Performance must be measured on real
hardware, not inferred from desktop runs.

---

## 9. Security and storage

- No API secrets, storage credentials or signing keys inside the APK.
- Backend credentials come from environment variables only (`.env.example` documents every key).
- `X-API-Key` auth with constant-time comparison; 120 req/min/IP rate limiting; JSON errors with correct codes.
- HMAC-SHA256 signed URLs with a 900 s TTL and a mandatory production signing secret.
- Uploaded assets are untrusted: suffix/size/path limits, traversal-proof `safe_resolve()`, statically disabled
  Blender auto-execution (`BLENDER_DISABLE_AUTOEXEC=1`), truncated/corrupt file rejection.
- Originals preserved; logs kept free of secrets; pinned dependency versions.
- Persistent outputs belong in access-controlled object storage — S3-compatible (R2, Supabase, AWS S3) via the
  `local` / `s3` storage drivers. Provider choice remains a deployment decision.

---

## 10. Development roadmap

| Step | Milestone | Status |
|---|---|---|
| 01 | Project foundation and architecture | ✅ done — pipeline, API, Android tree, docs, 5 workflows |
| 02 | Asset ingestion (change detection, IDs, immutability) | ✅ done |
| 03 | Blender + Python processing pipeline | ✅ GLB/glTF/BVH verified · ⚠️ Blender/FBX path unexecuted locally |
| 04 | Skeleton analysis and validation | ✅ done |
| 05 | Animation compatibility and retargeting | ✅ done — first-compatible-target policy |
| 06 | GitHub Actions automation | ⚠️ implemented, not executed (no runner/credentials here) |
| 07 | Android Filament viewer | ⚠️ source complete, APK not built here |
| 08 | Advanced model and animation inspectors | ⚠️ source complete, APK not built here |
| 09 | Backend and persistent storage | ✅ API + SQLite verified · ⚠️ S3 publication unexecuted |
| 10 | Full integration and device testing | ⬜ not started — needs physical hardware |
| 11 | Signed APK and production release | ⚠️ workflow ready, not executed |

Each milestone must pass its acceptance tests before the next is considered complete.

---

## 11. Testing strategy

- **Python unit/integration** — GLB write/parse/inspection, truncation rejection, limits and traversal, BVH frames,
  role aliases and ambiguity, compatibility both ways, real retarget output, report fields, manifest hashes,
  every API endpoint, downloads, signed URLs, auth, playability semantics, job/report endpoints.
- **Pipeline end-to-end** — published outputs, Khronos validation of every exported GLB, quadruped rejection,
  index counts.
- **Android** — source-structure check runs without a JDK; `:app:testDebugUnitTest` runs in CI; the nine-step
  physical-device plan (rendering, lifecycle, thermal, offline cache, memory) is in [`docs/testing.md`](docs/testing.md).
- **CI rules** — tests run automatically, failures stay visible, checks are never disabled to force a green build,
  releases require verified signing configuration.

Last local run (2026-11-03): `42 passed`, 8 model manifests (6 PASS / 2 ERROR), 10 animation manifests (9 runtime
GLBs, 1 deliberate incompatible quadruped), 16 valid GLBs at Khronos `E0 W0`.

---

## 12. Definition of done and contribution guidelines

A feature is complete only when its implementation is committed, relevant automated tests pass, integration behaviour
is verified, no known regression is introduced, documentation is updated, CI checks pass, and the result is
reproducible. Code that only works in one developer's local environment is not production-ready.

Keep modules independent · prefer reusable Python components · never modify source assets · no hardcoded
machine-specific paths · keep Android rendering separate from processing · use clear error messages · maintain tests ·
document new formats and their limitations · never claim compatibility without testing · never delete a validation
check to hide a failure.

---

## 13. Documentation

- [`docs/architecture.md`](docs/architecture.md) — data flow and module responsibilities
- [`docs/processing.md`](docs/processing.md) — formats, Blender pipeline, validation, reports
- [`docs/animation.md`](docs/animation.md) — skeleton mapping, compatibility, retargeting, first-target policy
- [`docs/api.md`](docs/api.md) — endpoints, DTOs, auth, errors, database
- [`docs/android.md`](docs/android.md) — version matrix, build/install, 12 screens, Filament notes
- [`docs/storage.md`](docs/storage.md) — persistent object storage, signed URLs, atomic publication
- [`docs/actions.md`](docs/actions.md) — workflows, secrets, changed-file detection
- [`docs/security.md`](docs/security.md) — untrusted assets, dependency pinning, no secrets in the APK
- [`docs/testing.md`](docs/testing.md) — executed tests, dataset A–L, device test plan
- [`docs/troubleshooting.md`](docs/troubleshooting.md) — common failures and exact fixes
- [`docs/limitations.md`](docs/limitations.md) — honest blockers and remaining setup steps

---

## 14. Honest limitations

- **No APK was compiled here** — no JDK, Gradle installation, Android SDK or emulator/device. The complete source tree,
  pinned version matrix and CI build workflow are provided; `android-build.yml` performs the build on a hosted runner.
- **No physical-device tests** were run; the plan is in `docs/testing.md`.
- **Headless Blender is not installed in this sandbox.** GLB/glTF/BVH are processed by the real pure-Python engine;
  FBX/OBJ/STL/PLY/DAE emit a `BLENDER_UNAVAILABLE` warning locally. CI installs Blender 4.2.23 LTS and runs
  `processing/blender/*.py`. No fake FBX parsing is claimed.
- **S3 publication and signed GitHub releases** are implemented but unexecuted: no storage, keystore or release
  secrets were provided.
- **First compatible target only** — one runtime GLB per animation, for the first compatible character by sorted
  manifest ID; others are reported but not exported. See `docs/animation.md`.
- **No IK / foot-sliding correction** in retargeted clips; inspect playback before shipping.
- **Local SAF imports** render, but full inspector metadata is only available for pipeline-processed assets.
- **KTX2/Basis** compression is disabled unless runtime decoding is supplied; Draco and automatic LOD are out of scope.
- **No AI asset-generation service** exists in this repository — assets arrive as files.

---

## 15. Long-term vision

One ecosystem connecting AI-assisted generation, automated processing, animation retargeting, asset versioning and
professional 3D inspection — where every claim about an asset is backed by a validation report and every runtime
asset is reproducible from its source.

---

**Project:** 3D Asset Studio
**Platform:** Python + Blender + GitHub Actions + Kotlin + Jetpack Compose + Google Filament
**Runtime asset format:** GLB / glTF
**Approach:** modular, automated, test-driven, milestone-based
**License:** MIT — see [`LICENSE`](LICENSE)
