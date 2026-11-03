# 3D Asset Studio

A working, tested, documented asset pipeline and native Android viewer:

**GitHub asset repository → path-triggered Actions → headless processing/validation → versioned manifests + metadata API → persistent object storage → native Kotlin/Compose/Filament viewer.**

The Android app is a **viewer and inspection tool only**. It does not generate, edit, or upload 3D models, and it is not an asset-generation dashboard.

> Honest status: the Python processing pipeline, official Khronos validation, FastAPI/SQLite metadata API, signed downloads, operations console, native Android source tree and automated tests are implemented and executed in this workspace. **A compiled APK and physical-device tests are not available here** because this sandbox has no JDK, Android SDK or device. `docs/limitations.md` lists every unexecuted item and the exact remaining setup steps. No feature below is claimed as "done" unless its source, integration and tests exist.

---

## 1. What was built

| Area | Implementation | Verified here |
|---|---|---|
| Model pipeline | `processing/` — real glTF/GLB inspection, integrity checks, geometry/material/texture/skeleton/animation extraction, PASS/WARNING/ERROR reports, versioned manifests | ✅ 8 models processed |
| Animation pipeline | BVH parsing, real BVH→glTF retargeting, compatibility checks, single-clip runtime GLBs | ✅ 10 animations, 9 runtime GLBs, 1 deliberate incompatible quadruped |
| Validation | Official Khronos `gltf-validator@2.0.0-dev.3.10` through `tools/gltf-validator/validate.js` | ✅ all exported GLBs: 0 errors |
| Metadata API | FastAPI + SQLite, `ASSET_STUDIO_API_KEY` auth, rate limiting, signed URLs, local/S3 storage drivers | ✅ TestClient endpoint + security tests |
| Operations console | `backend/static/index.html` — read-only, real manifest/report data | ✅ served at `/` |
| Android viewer | 8 Gradle modules, Kotlin/Compose, Filament+gltfio, Room, DataStore, SAF, 12 real screens | ⚠️ source complete; APK **not built here** |
| CI | 5 workflows with path triggers, `workflow_dispatch`, changed-file detection, Blender, artifacts, optional S3 publication and signed release | ⚠️ not executed here |
| Tests | `pytest` suite covering GLB, BVH, compatibility, retargeting, reports, manifests, API, auth, signed URLs, end-to-end pipeline, Android source structure | ✅ 42 tests pass |

## 2. Architecture

```
assets/                 source assets (never modified)
   models/{characters,environments,props,modular}/
   animation/{locomotion,combat,other}/
   textures/
processing/            pure-Python pipeline + headless Blender scripts
   glb.py               real glTF 2.0 reader/inspector/writer
   bvh.py               real BVH parser
   compatibility.py     conservative humanoid role/hierarchy/spatial checks
   retarget.py          BVH → glTF animation retargeting
   report.py manifest.py validate.py publish.py cli.py
   blender/             scripts run by `blender -b -P ...` in CI
backend/               FastAPI app, SQLite sync, storage drivers, static console
manifests/ reports/ processed/   generated publication outputs (git-ignored)
android/               native viewer (Gradle 8.9, AGP 8.7.3, Kotlin 2.0.21, Filament 1.77.3)
.github/workflows/     asset-processing, animation-processing, android-build, validation, release
docs/                  technical documentation
```

GitHub Actions runners are **temporary processing environments**, not servers. Processed GLBs must live in persistent object storage (S3-compatible, or a host you control); the metadata API reads published manifests and serves signed download URLs. The APK contains **no** PATs, storage credentials or signing keys.

## 3. Quick start (local development)

Requires Python 3.11+, Node 22+, optionally Blender 4.2 LTS for FBX/OBJ/STL/PLY/DAE conversion.

```bash
git clone <this-repo> && cd 3dmodel-gen-system-
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
npm ci --prefix tools/gltf-validator     # official Khronos validator

# 1. Generate the real A–L verification dataset (source files, git-ignored)
.venv/bin/python scripts/generate_sample_assets.py

# 2. Process models, then animations (models must exist for compatibility checks)
.venv/bin/python -m processing.cli process-models --all
.venv/bin/python -m processing.cli process-animations --all
.venv/bin/python -m processing.cli build-index

# 3. Validate every processed GLB with Khronos
.venv/bin/python -m processing.cli validate

# 4. Run the metadata API (live preview / operations console)
ASSET_STUDIO_API_KEY=change-me .venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 8000
#    http://localhost:8000/            console
#    http://localhost:8000/docs        OpenAPI
#    http://localhost:8000/api/v1/health

# 5. Tests
.venv/bin/python -m pytest -q
```

`processing.cli status` reports which optional tools are available (Blender binary, `bpy` module, Khronos validator, Java/Gradle/Android SDK).

## 4. Asset conventions

| Kind | Directory | Formats |
|---|---|---|
| Models | `assets/models/{characters,environments,props,modular}/` | `.glb` `.gltf` `.fbx` `.obj` `.stl` `.ply` |
| Animations | `assets/animation/<category>/` | `.glb` `.gltf` `.fbx` `.bvh` `.dae` |
| Textures | `assets/textures/` | `.png` `.jpg` `.jpeg` `.webp` |

The 18 A–L verification fixtures under `assets/` are real generated GLB/BVH bytes (~840 KiB total), tracked intentionally so tests and workflows are reproducible; `scripts/generate_sample_assets.py` regenerates them. Large **processed** outputs (`processed/`, `manifests/`, `reports/`, `local/`) are git-ignored.

Folder names are **organisational metadata only** — the pipeline reads actual bytes, never infers properties from a folder name. Originals are never modified. GLB/glTF is the primary runtime delivery format; Filament does **not** load arbitrary FBX/BVH files. Normalisation/repair is applied only to generated processed copies. Skeletons, animations, UV maps and materials are never silently discarded; unsupported/lost features appear as report diagnostics.

Supported model inspection: meshes, primitives, vertices, triangles, indices, materials, textures (including dimensions and missing references), UV sets, normals, tangents, morph targets, bounding boxes, skins, joints/weights, inverse bind matrices, influence counts, unweighted/invalid vertices, skinned-mesh links, and embedded animation clips (name, duration, fps, frame range, channels, animated bones, root motion, interpolation).

## 5. GitHub upload and processing

```bash
# Add only SOURCE assets; generated GLBs/DB/reports are git-ignored on purpose.
git add assets/models/characters/hero.glb assets/animation/locomotion/run.bvh
git commit -m "asset: add hero and run"
git push origin arena/b5e3033c-3dmodel-gen-system
```

Pushing under `assets/models/**` triggers `asset-processing.yml`; `assets/animation/**` (or a character change) triggers `animation-processing.yml`. Both support `workflow_dispatch` with `all=true` or an explicit path list. `scripts/changed_files.py` limits processing to changed files. Reports/manifests are uploaded as CI artifacts; with `ASSET_STUDIO_S3_*` secrets configured, `scripts/publish_storage.py` uploads to a staging prefix, verifies sizes, then atomically copies objects into the final prefix and publishes `manifests/releases/latest.json`. See `docs/actions.md` and `docs/storage.md`.

## 6. Backend API

Base path `/api/v1`. Set `ASSET_STUDIO_API_KEY` to require header `X-API-Key`; rate limiting defaults to 120 requests/minute/IP.

| Endpoint | Purpose |
|---|---|
| `GET /health` | status, counts, auth mode, storage driver, validator |
| `GET /assets` | list (`kind`, `search`, `sort`, `limit`) |
| `GET /assets/{asset_id}` | versioned manifest |
| `GET /assets/{asset_id}/metadata` | full inspection metadata |
| `GET /assets/{asset_id}/download` | processed GLB (integrity header, signed URL supported) |
| `POST /assets/{asset_id}/signed-url` | short-lived signed URL |
| `GET /models` · `GET /models/{model_id}` | model list/detail |
| `GET /animations` · `GET /animations/{animation_id}` | animation list/detail |
| `GET /models/{model_id}/compatible-animations` | compatibility + **playable** status |
| `GET /processing-jobs/{job_id}` | job status |
| `GET /releases/latest` | latest publication |
| `GET /reports/{kind}/{asset_id}` | generated report |

**Playability is stricter than compatibility**: a clip is playable only when skeleton compatibility passed *and* a validated runtime GLB was exported for that exact `target_character_id`. An animation that is merely "compatible" is not falsely offered for playback.

## 7. Android viewer

Native Kotlin + Jetpack Compose, Google Filament 1.77.3 (`gltfio` loads GLB/glTF), Room (offline cache), DataStore (settings), coroutines/Flow, OkHttp/Retrofit, SAF (local import), FileProvider (share).

Twelve real screens: Home, Asset Library, Model Browser, 3D Viewport, Skeleton Inspector, Animation Library, Animation Player, Material Inspector, Geometry Inspector, Validation Report, Downloads, Settings. Every control performs a real action; there are no placeholder buttons.

The viewport renders cached GLBs with orbit/zoom/pan/reset, perspective/orthographic, six axis views, grid/axes/ground overlays (real glTF LINE geometry), skeleton overlay, lighting/HDR/shadows/exposure, PBR/textured/solid/wireframe/x-ray/normal modes, FPS/thermal-aware frame pacing and Performance/Balanced/High quality (Balanced default). The animation player offers play/pause/stop/restart/loop/scrub, speeds `0.25 … 2.0`, current/total time, clip selection, download and honest compatibility reasons. Static models show "no skeleton" — nothing is fabricated.

```bash
# Requires JDK 17 + Android SDK 35 (NOT available in this sandbox)
cd android
./gradlew :app:assembleDebug           # debug APK
./gradlew :app:testDebugUnitTest       # JVM unit tests
# For a signed release, create keystore.properties from CI secrets (see docs/android.md)
```

Exact version matrix: Gradle 8.9, AGP 8.7.3, Kotlin 2.0.21, KSP 2.0.21-1.0.25, Compose BOM 2024.12.01, Room 2.6.1, DataStore 1.1.1, Retrofit 2.11.0, OkHttp 4.12.0, Filament 1.77.3, compileSdk 35, minSdk 26, JDK 17. See `docs/android.md`.

## 8. Documentation

- `docs/architecture.md` — data flow and module responsibilities
- `docs/processing.md` — formats, Blender pipeline, validation, reports
- `docs/animation.md` — skeleton mapping, compatibility, retargeting, first-target policy
- `docs/api.md` — endpoints, DTOs, auth, errors
- `docs/android.md` — version matrix, build/install, 12 screens, Filament notes
- `docs/storage.md` — persistent object storage, signed URLs, atomic publication
- `docs/actions.md` — workflows, secrets, changed-file detection
- `docs/security.md` — untrusted assets, dependency pinning, no secrets in APK
- `docs/testing.md` — executed tests, dataset A–L, device test plan
- `docs/troubleshooting.md` — common failures and exact fixes
- `docs/limitations.md` — honest blockers and remaining setup steps

## 9. Honest limitations

- **No APK was compiled here**: no JDK, Gradle installation, Android SDK or emulator/device. The complete Gradle/Kotlin source tree, pinned version matrix and CI build workflow are provided; `scripts/check_android_sources.py` validates structure without a JDK. Exact build steps are in `docs/android.md` and `docs/limitations.md`.
- **No physical-device tests** were run; the device test plan is in `docs/testing.md`.
- **Headless Blender binary is not installed in this sandbox.** The pipeline therefore processes GLB/glTF/BVH with its real pure-Python implementation locally and emits a `BLENDER_UNAVAILABLE` WARNING for FBX/OBJ/STL/PLY/DAE. CI installs Blender 4.2.23 LTS and runs `processing/blender/*.py`. No fake FBX parsing is claimed.
- **S3 publication and signed GitHub releases** are implemented but unexecuted: no storage credentials, keystore or release secrets were provided.
- The **first compatible target** (sorted by manifest id) is exported per animation; other compatible characters are reported but not exported. See `docs/animation.md`.
- Local SAF imports render, but full inspector metadata is only available for pipeline-processed assets (documented in the app).

## 10. License

MIT — see `LICENSE`.
