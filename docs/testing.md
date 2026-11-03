# Tests and verification dataset

## Executed here (2026-11-03)

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
npm ci --prefix tools/gltf-validator
.venv/bin/python scripts/generate_sample_assets.py
.venv/bin/python -m processing.cli process-models --all       # 8 models, 0 failures
.venv/bin/python -m processing.cli process-animations --all   # 10 animations, 0 failures
.venv/bin/python -m processing.cli build-index
.venv/bin/python -m processing.cli validate                   # all exported GLBs: 0 errors
.venv/bin/python -m pytest -q                                 # 42 passed
.venv/bin/python scripts/check_android_sources.py             # passed
```

One nonfatal `StarletteDeprecationWarning` about `httpx` in `starlette.testclient` is emitted by the pinned FastAPI/Starlette combination; it does not affect results.

`pytest` covers: GLB write/parse/inspection, truncation rejection, empty-array stripping, untrusted file limits and traversal, BVH parsing/frame validation, humanoid role aliases and ambiguity, compatibility (compatible/incompatible), real retarget output (one clip, skin, triangles, duration), report diagnostic fields, manifest hashes/no credentials, all required API endpoints, downloads, signed URLs, auth, rate-limit configuration, playable semantics, job/report endpoints, end-to-end published outputs, Khronos validation of every exported GLB, quadruped rejection, index counts and Android source structure.

## Dataset A–L (real generated GLB/BVH bytes, tracked as small test fixtures)

| # | Case | File | Expected |
|---|---|---|---|
| A | static GLB | `models/props/crate.glb` | PASS, no skeleton |
| B | rigged, no animation | `models/characters/warrior.glb` | PASS, 21 bones, 1 skin, 0 clips |
| C | rigged with embedded animation | `models/characters/rigged_animated.glb` | PASS, 1 clip |
| D | external FBX | CI-only Blender fixture | requires Blender; WARNING locally |
| E | external BVH | `animation/locomotion/walk.bvh` | retargeted runtime GLB |
| F | compatible skeleton | `walk/run/idle/jump/walk_fast/attack/death.bvh` | 7 exported GLBs |
| G | different naming | `other/different_naming.bvh` (`mixamorig:` prefixes) | retargeted via aliases |
| H | incompatible skeleton | `other/quadruped.bvh` | WARNING, `incompatible`, no GLB |
| I | multiple PBR materials/textures | `models/props/multi_material_pbr.glb` | PASS, real texture dimensions |
| J | missing texture | `models/props/missing_texture.glb` | ERROR `UNRESOLVED_REFERENCE` |
| K | invalid GLB | `models/props/invalid.glb` | ERROR truncated BIN chunk |
| L | large character/terrain | `models/environments/large_terrain.glb` | 19 998 indices, PASS |

The 18 fixture files total ~840 KiB and are committed so the workflows and tests are reproducible; `scripts/generate_sample_assets.py` regenerates them byte-for-byte. Large *processed* outputs remain git-ignored.

Local results: 8 model manifests (6 PASS, 0 WARNING, 2 ERROR), 10 animation manifests (9 with runtime GLBs, 1 incompatible quadruped), 9 exported animation GLBs each with exactly one `Retarget_*`/`IdleWave` clip, 1 skin and real geometry. Khronos reported E0/W0 for all 16 valid processed GLBs.

## Android / device test plan (not executed here)

1. `./gradlew :app:testDebugUnitTest` on a runner with JDK 17 (CI `android-build.yml`).
2. Install debug APK on a physical device (`adb install`), grant no special permissions (INTERNET only).
3. Point Settings at a reachable API; verify Home counts match `/api/v1/health`.
4. Download a rigged character + a retargeted animation GLB; verify SHA-256, offline render after airplane mode, cache size and delete/share.
5. Viewport: orbit/zoom/pan/reset, six axis views, orthographic toggle, grid/axes/ground/skeleton, render modes, quality, exposure, FPS overlay; verify GPU cleanup after repeated open/close.
6. Skeleton Inspector: 21 real bone names, hierarchy, transforms, skinned-mesh links, no fabricated skeleton on a static model.
7. Animation Player: all speeds, scrub, loop, clip selection, current/total time; verify incompatible clips are disabled with a reason and a no-clip asset shows "No animation clips available".
8. Material/Geometry/Validation screens: compare counts with the manifest; verify missing-texture warnings and report diagnostics.
9. Large-file warning, background download cancellation, error recovery on network loss, lifecycle (rotate/background/foreground), thermal throttling.

Document any deviation from this plan in `docs/limitations.md` after running on real hardware.
