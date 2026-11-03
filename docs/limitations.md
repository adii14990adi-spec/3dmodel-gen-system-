# Honest limitations and remaining setup

This file distinguishes **implemented + executed** from **implemented but unverified** and **not implemented**.

## Implemented and executed in this workspace

- Real glTF/GLB inspection/writing, BVH parsing, conservative compatibility checks, BVH→glTF retargeting, PASS/WARNING/ERROR reports, versioned manifests, atomic publication and index build.
- Official Khronos glTF Validator (`2.0.0-dev.3.10`) over every processed GLB: 0 errors on 16 valid outputs; deliberate ERROR results for the invalid GLB and missing-texture GLB.
- FastAPI/SQLite API with all required endpoints, auth, rate limiting, signed URLs, storage traversal protection, local storage driver and read-only operations console (exercised with `TestClient`).
- 42 automated pytest tests plus the Android source-structure check — all passing.
- Real A–L verification dataset generated from actual glTF bytes (not hardcoded metadata).

## Implemented but NOT executed here (blockers)

| Item | Blocker | Exact remaining steps |
|---|---|---|
| Compiled Android APK, `assembleDebug`/`assembleRelease`, JVM unit tests | no JDK, Gradle installation, Android SDK, emulator or device in the sandbox | Install JDK 17 + Android SDK platform 35/build-tools 35, `cd android && ./gradlew :app:assembleDebug`, then `adb install` on a device; `android-build.yml` performs this in CI |
| Physical-device tests (rendering, lifecycle, thermal, offline cache) | no device | follow the 9-step plan in `docs/testing.md` |
| Headless Blender processing for FBX/OBJ/STL/PLY/DAE | no `blender` binary locally (CI downloads it) | install Blender 4.2.23 LTS or push to GitHub so `asset-processing.yml` runs; scripts are in `processing/blender/` |
| S3/R2 publication and signed GitHub release | no storage/keystore/release secrets | configure the secrets in `docs/actions.md`, then run the workflows / `release.yml` |
| Long-lived hosted API | backend was exercised via `TestClient`, not deployed | `uvicorn backend.app:app --host 0.0.0.0 --port 8000` behind HTTPS, set `ASSET_STUDIO_PUBLIC_BASE_URL` and `ASSET_STUDIO_SIGNING_SECRET` |
| GitHub Actions workflows | no runner in this sandbox | push to the branch; `validation.yml` mirrors the exact local commands |

## Deliberate design limits

- **First-compatible-target export**: one runtime GLB per animation, for the first compatible target by sorted manifest id. Other compatible characters are reported but not exported/playable. Change `processing/cli.py::process_animation` to iterate all compatible records if you need per-character exports.
- **Foot sliding / IK**: retargeting handles rest/orientation/scale differences, root motion, frame timing and hierarchy, but does not solve foot sliding with IK. Inspect retargeted playback before shipping production animations.
- **Embedded glTF clip retargeting**: only identical skeleton names are passed through; other cases require an explicit manual bone map. This avoids false playability claims.
- **Local SAF imports**: render correctly but have no pipeline metadata, so inspectors honestly show "not available for local imports — process through the pipeline".
- **Wireframe/x-ray/normal modes**: implemented by overriding the glTF material instance base colour and by real overlay geometry; they are not a full per-triangle wireframe shader. Colours are honest visualisation modes, not fabricated geometry statistics.
- **KTX2/Basis**: disabled unless runtime decoding is provided.
- **Filament runtime calls**: orthographic projection, exposure, HDR/post-processing, shadowing and MSAA call the pinned Filament 1.75.1 Java APIs directly inside `runCatching`, so an unsupported renderer backend degrades gracefully rather than crashing. Filament 1.76.1+ requires `compileSdk 37` and is intentionally not used with AGP 8.7.3 / `compileSdk 35`.
- **bpy wheel in Python**: an alternative to the Blender binary, not included here; CI uses the official Blender tarball.

## Not implemented (scope decisions)

- No asset-generation/3D-modelling editor, no animation upload dashboard in the Android app (explicitly out of scope).
- No GPU-side Draco/KTX2 decoding pipeline, no automatic LOD/reduction (spec: no unnecessary geometry reduction).
- No PostgreSQL by default: SQLite is used locally; the storage abstraction and schema are small enough to port to PostgreSQL by replacing `backend/database/db.py` (documented in `docs/api.md`).
