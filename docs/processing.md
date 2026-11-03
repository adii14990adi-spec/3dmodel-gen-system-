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
