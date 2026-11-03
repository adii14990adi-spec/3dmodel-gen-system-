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
.venv/bin/python -m pytest -q                                 # 202 passed
.venv/bin/python scripts/check_android_sources.py             # passed
```

One nonfatal `StarletteDeprecationWarning` about `httpx` in `starlette.testclient` is emitted by the pinned FastAPI/Starlette combination; it does not affect results.

`pytest` covers: GLB write/parse/inspection, truncation rejection, empty-array stripping, untrusted file limits and traversal, BVH parsing/frame validation, humanoid role aliases and ambiguity, compatibility (compatible/incompatible), real retarget output (one clip, skin, triangles, duration), report diagnostic fields, manifest hashes/no credentials, all required API endpoints, downloads, signed URLs, auth, rate-limit configuration, playable semantics, job/report endpoints, end-to-end published outputs, Khronos validation of every exported GLB, quadruped rejection, index counts and Android source structure.

## Step 02 — asset ingestion tests (executed 2026-11-03)

`tests/unit/test_ingestion.py` — 65 focused tests, all executed in this workspace. Changed-file detection
runs against **real temporary git repositories** (created, committed, renamed and shallow-cloned inside the
test), never against mocks.

| Area | Cases covered |
|---|---|
| Changed-file detection | added (per kind), modified, deleted (never returned), rename (new path only), unrelated docs/code changes, empty diff, zero/absent SHA (→ `--all`), unknown ref, shallow history, multiple assets in one commit, spaces / quotes / `$` / unicode names, `--null` round-trip, unsupported + unsafe extensions filtered, uppercase extensions, kind prefixes not leaking, option-like refs rejected without side effects, `--all` flag preserved |
| Stable identity | same path+content stable, content change → new id, identical bytes at different paths do **not** collide, model vs animation distinct, OS-independent (backslashes, `./`, NFD vs NFC unicode), deterministic across processes, id shape/ASCII |
| Source validation | unsupported + unsafe suffixes, empty, oversize, missing, directory, traversal, symlink escape, dangling symlink, case-insensitive extensions, all 8 supported formats accepted per kind (`glb gltf fbx obj stl ply` / `glb gltf fbx bvh dae`), validation is read-only, embedded scripts never executed, malformed/truncated GLB → `ERROR` with no runtime GLB, CLI exits `1` on a rejected source |
| Immutability | whole `assets/` tree hashed before/after processing a model and an animation (identical), processed output written under `processed/` only, reprocessing idempotent |

Workflow wiring was verified by running the exact shell of both resolve/process steps against a scratch
repository: 3 tricky file names survived `--null` → `xargs -0` byte-exact, an empty change set produced
`count=0` and skipped processing, a zero SHA and `workflow_dispatch all=true` produced `--all`, and a
docs-only diff produced `count=0`.

Results: `202 passed` (`pytest -q`), `scripts/check_android_sources.py` passed, pipeline regenerated
8 model manifests (6 PASS / 2 ERROR) and 10 animation manifests (9 runtime GLBs, 1 incompatible quadruped).

### Workflow wiring tests (`tests/unit/test_workflows.py`, 39 tests)

These load the real `asset-processing.yml` / `animation-processing.yml`, render the `${{ … }}` expressions for
a given event and execute the resolve/process shell locally:

* every `steps.<id>.<output>` reference must resolve to a declared step `id` (an undefined id makes the
  expression null, and `null != '0'` is false — processing is then silently skipped);
* `workflow_dispatch all=true` writes exactly `b"--all\x00"` and `count=1`;
* an empty diff writes an empty list and `count=0`;
* a zero SHA requests `--all`;
* the step aborts when the list cannot be written (`set -euo pipefail`), so a broken resolve never looks like
  an empty change set;
* the process step consumes the list with `xargs -0 -r`, and spaces/quotes/unicode survive that exact call;
* dispatch inputs (`all`, `paths`) exist and checkout uses `fetch-depth: 0`.

**Regression this caught:** the Step 02 edit to the resolve step dropped `id: changes`. The workflow still ran
and reported success, but `steps.changes.outputs.count` evaluated to null, so `Process models` was skipped on a
real `workflow_dispatch all=true` run. Fixed and now covered by `test_every_referenced_step_id_is_defined`.

## Security fix — dispatch path input (executed 2026-11-03)

`workflow_dispatch` `inputs.paths` was interpolated straight into the resolve step's shell script
(`for p in ${{ inputs.paths }}; do …`), i.e. a dispatch input could execute commands on the runner. It is now
delivered through `env: INPUT_PATHS`, written to a file, and validated by `scripts/dispatch_paths.py`.

`tests/unit/test_dispatch_paths.py` (54 tests, real directories and real files, nothing mocked):

| Case | Assertion |
|---|---|
| normal relative path | accepted, one token |
| spaces / unicode / quotes / `$` | accepted as a single path, byte-exact in the NUL list |
| `;`, `$()`, backticks, newline-separated payloads | exit 2, empty stdout, marker file proving execution **not** created, directory tree unchanged |
| absolute path, `~/`, drive letter, UNC | rejected |
| `../` traversal, Windows `..\..` traversal, backslash separators | rejected (no silent rewriting) |
| other asset root, repository file, wrong kind | rejected |
| `.py`, `.sh`, `.blend`, `.zip`, `.txt`, no extension, `x.glb.exe` | rejected; the allowlist is asserted against `SUPPORTED_MODELS` / `SUPPORTED_ANIMATIONS` |
| symlink to a file outside the root, symlink to an outside directory, dangling symlink | rejected; a symlink staying inside the root is allowed |
| empty, whitespace-only, blank line, embedded NUL, CRLF, ANSI escape, invalid UTF-8 | rejected, exit 2 |
| duplicate entries | emitted once |
| CLI contract | `::error::` on stderr, non-zero exit, **no partial token list** on failure |

`tests/unit/test_workflows.py` additionally asserts, against the real YAML: no `run:` block in **any** of the
five workflows contains a `${{ … }}` expression, `INPUT_PATHS` is declared under `env:`, the resolve step calls
`scripts/dispatch_paths.py --kind <kind> --paths-file "$PATHS_FILE" --null`, and there is no `for p in` loop.
Hostile payloads are run through the real resolve shell (not a paraphrase of it): each fails the step, creates no
marker file and leaves an empty token list. Valid dispatch paths, multiple paths, `all=true`, empty diffs,
zero SHAs, push detection with spaces/unicode, and deletion-only pushes are executed through that same shell.

Results: `202 passed` (`pytest`, exit 0), `scripts/check_android_sources.py` passed, pipeline regenerated
8 model manifests (6 PASS / 2 ERROR) and 10 animation manifests (9 PASS / 1 WARNING), every exported GLB at
0 Khronos errors, and `assets/` byte-identical before/after (SHA-256 of all 19 files).

#### Verified defects fixed in Step 02

1. Non-ASCII source paths were silently dropped: `git diff` C-quoted them (`"assets/.../\303\274n..."`), so the
   `assets/models/` prefix never matched. Fixed with `-z` + `core.quotepath=false`.
2. A zero/absent `before` SHA diffed `HEAD~1`, so a multi-commit branch push only processed its tip commit and
   silently skipped new assets in earlier commits. Now it falls back to `--all`.
3. Identical bytes at two different paths produced the same asset id (`model_warrior_<hash8>`), so the second
   asset overwrote the first manifest. The digest now covers the canonical path.
4. Backslash paths (Windows) produced a different id than POSIX paths, and NFD/NFC unicode differed. Both are
   normalised.
5. File names with spaces broke the workflows (word-splitting) and were interpolated into shell text. Tokens are
   now NUL-separated and consumed with `xargs -0`.
6. Command-line option injection: a `--base` value such as `--output=/tmp/x` was passed to `git` as an option.
   Refs now have to match a ref grammar and may not start with `-`.

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
