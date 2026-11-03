# Processing pipeline

## Commands

```bash
python -m processing.cli status
python -m processing.cli process-models --all
python -m processing.cli process-models assets/models/characters/hero.glb
python -m processing.cli process-animations --all
python -m processing.cli validate [file.glb ...]
python -m processing.cli build-index
```

`--job-id` records a job in `reports/jobs/`; `scripts/run_pipeline.py` wraps the CLI with a timeout for CI. `scripts/changed_files.py` resolves only changed source files between two git refs.

## Asset ingestion (Step 02)

### Changed-file detection (`scripts/changed_files.py`)

`git -c core.quotepath=false diff --name-only -z --diff-filter=ACMR <base> <head> --` is the only
source of truth. Contract:

| Situation | Result |
|---|---|
| Added / modified / renamed / copied source asset | path(s) returned; a **rename** yields the new path only |
| Deleted source asset | never returned (`--diff-filter=ACMR` drops `D`) |
| Unrelated docs/source change, or an empty diff | empty output → the workflow skips processing |
| Zero / absent `before` SHA (first push, `workflow_dispatch` without inputs) | `--all` — the whole tree is processed, never a partial `HEAD~1` diff |
| Unknown ref, force push, shallow history, missing git | `--all` (safe fallback; the job never silently skips new assets) |
| Ref that looks like a git option (`--output=…`, `-c`) or fails the ref grammar | rejected before argv → `--all`; the value is never passed to git |
| Non-ASCII / spaced / quoted file names | byte-exact via `-z` + `core.quotepath=false` (git would otherwise C-quote them) |

Paths are filtered by kind (`assets/models/` vs `assets/animation/`) **and** by the extension allowlist
(case-insensitive), so a model job never receives an animation file or an unsupported type.
`--null` emits NUL-separated tokens; the workflows pipe that into `xargs -0`, so names with spaces,
quotes or `$` survive without ever being interpolated into shell code.

`workflow_dispatch` also accepts `all=true` (full reprocess) or a space-separated `paths` list — that
input is split on whitespace, so file names containing spaces must use `all=true` or a push trigger.

### Stable asset identity (`processing.common.asset_id`)

```
asset_id = f"{model|anim}_{sanitised-path-stem}_{digest[:8]}"
digest   = sha256(f"{kind}\0{canonical-posix-NFC-path}\0{source_sha256}")
```

| Property | Guarantee |
|---|---|
| Same path + same bytes | identical id (reprocessing is idempotent) |
| Same path + new bytes | new id → a new version, never an overwrite |
| Identical bytes at two paths | **different** ids (regression-tested; the digest covers the path) |
| Model vs animation | separate prefixes and separate digest input |
| OS independence | backslashes normalised, `./` stripped, unicode NFC-normalised, ASCII-only id |
| Absolute/machine paths | never embedded: callers pass repo-relative paths, which `validate_source` enforces |

### Source validation (`processing.common.validate_source`)

Read-only checks, in order: extension allowlist for the kind, path containment inside the repository
root (resolves symlinks, so a link pointing outside is rejected), regular file (missing / directory /
dangling symlink rejected), non-empty, and the configured size limit (500 MiB, `ASSET_STUDIO_MAX_SOURCE_BYTES`).
Validation opens nothing and executes nothing; file contents are treated as data.

Two different failure classes are kept distinct on purpose:

* **Rejected input** (unsupported type, empty, oversize, escaping path) raises `UnsafeAssetError`, the CLI
  logs it, records the failed job and exits `1`, so the workflow fails.
* **Malformed/truncated content** with a supported extension is ingested and published as an `ERROR`
  report with no runtime GLB (`CONVERSION_FAILED` / `GLB_PARSE_FAILED`), so a broken source cannot become
  a playable asset while the rest of the batch still processes.

### Source immutability

Sources are only ever read (`sha256_file`, `read_bytes`, Blender import). Every processed copy is written
to `processed/…/<asset-id>/`, manifests to `manifests/`, reports to `reports/` — all git-ignored.
`tests/unit/test_ingestion.py` hashes the whole `assets/` tree before and after processing a model and an
animation and asserts the snapshots are identical.

## Model processing

1. `validate_source()` enforces suffix allowlist, non-empty file, 500 MiB limit and path containment. Uploaded files are untrusted data; no scripts or embedded code are executed.
2. `.glb` → byte-identical processed copy. `.gltf` → external buffers packed into a self-contained GLB. Other formats → `processing/blender/convert_model.py` via `blender -b -P`; without a Blender binary an explicit `BLENDER_UNAVAILABLE` WARNING is emitted (never a fake success).
3. `inspect_document()` decodes real accessors: geometry counts, bounding boxes, UV sets, normals/tangents, morph targets, materials/textures (including PNG/JPEG dimensions), skins, JOINTS_0/WEIGHTS_0, inverse bind matrices, influence histograms, unweighted/invalid vertices, missing joints, and embedded animation clips.
4. Diagnostics from inspection plus the official Khronos validator produce a PASS/WARNING/ERROR report. Each check carries description, asset, location, component, severity, code and suggested fix.
5. A versioned manifest is published and the metadata JSON/Markdown report are written atomically.

## Headless Blender (CI)

`asset-processing.yml` installs Blender 4.2.23 LTS from `download.blender.org` and sets `BLENDER_DISABLE_AUTOEXEC=1`. Scripts use `bpy` only to read mesh/armature/action data or to export GLB; `export_animations=True`, `export_skins=True` and `export_apply=False` preserve skeletons/animations. `processing/blender/_common.py::finish()` calls `os._exit(0)` because `bpy` 4.2 as a module can crash at interpreter teardown after a successful glTF export.

## Validation

`tools/gltf-validator` pins `gltf-validator@2.0.0-dev.3.10`. `validate.js` maps Khronos severities to `ERROR/WARNING/INFO` and exposes `report.issues.messages`. Verified locally:

- `invalid.glb` → `GLB_UNEXPECTED_END_OF_CHUNK_DATA` (ERROR)
- `missing_texture.glb` → `UNRESOLVED_REFERENCE` (ERROR)
- all 9 exported animation GLBs and 7 valid model GLBs → 0 errors, 0 warnings (only informational `UNUSED_OBJECT`)
- empty top-level arrays are stripped by `processing/glb.py::write_glb` to avoid `EMPTY_ENTITY`

If the validator is not installed, a `VALIDATOR_UNAVAILABLE` WARNING is recorded — the pipeline does not fabricate a PASS.
