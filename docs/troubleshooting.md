# Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `VALIDATOR_UNAVAILABLE` WARNING | Node modules missing | `npm ci --prefix tools/gltf-validator` (Node 22) |
| `BLENDER_UNAVAILABLE` WARNING for FBX/OBJ/STL/PLY/DAE | no `blender` binary | Install Blender 4.2 LTS and put it on `PATH`; CI does this in both processing workflows |
| `bpy` import fails with `libXrender.so.1` | headless system libs missing | Use the CI Blender tarball; with `bpy` pip wheels generate the repo-local shims (`scripts/setup/build_headless_stubs.py` if present) or install the X11 runtime libs |
| Blender exits with code `-11` after exporting | `bpy` teardown crash | `processing/blender/_common.py::finish()` already calls `os._exit(0)`; do not remove it |
| `EMPTY_ENTITY` from Khronos | empty glTF arrays | `write_glb()` strips empty arrays recursively; regenerate processed outputs |
| `NO_TARGET_CHARACTERS` WARNING | animations processed before models | `python -m processing.cli process-models --all` first |
| `INCOMPATIBLE_SKELETON` for a valid humanoid | aliases/hierarchy/scale mismatch | add real aliases or a manual bone map in `config/bone_mappings/humanoid.json`; check unit scale and rest offsets |
| `MANUAL_BONE_MAP_REQUIRED` for embedded glTF clips | source and target bone names differ | supply an explicit mapping; the pipeline will not guess |
| `401` on API | `ASSET_STUDIO_API_KEY` set | send `X-API-Key`, or unset the variable for local demo mode |
| `429` | rate limit | raise `ASSET_STUDIO_RATE_LIMIT` or retry after 60 s |
| `403` on signed URL | expired/tampered signature | request a new URL from `POST /api/v1/assets/{id}/signed-url` |
| Android build: "SDK location not found" | `ANDROID_HOME` unset | `export ANDROID_HOME=$HOME/Android/Sdk` and install platform 35 + build-tools 35.0.0 |
| Android build: `requires libraries and applications that depend on it to compile against version 37` | Filament 1.76+ is built against compileSdk 37 | keep `filament = "1.75.1"` (built against compileSdk 34) with AGP 8.7.3 / compileSdk 35; or upgrade AGP, Gradle and the SDK platform together |
| Android cleartext blocked | release network policy | use HTTPS; debug builds allow `10.0.2.2`/`localhost` only |
| Download fails integrity check | corrupted object/storage | republish from `processed/`; compare `X-Asset-Sha256` with the manifest |
| `pytest` fixture errors | generated outputs absent | run `generate_sample_assets.py` and both pipelines (the session fixture does this automatically) |

For any unresolved error, attach the generated `reports/<kind>/<asset_id>.json` — it contains the exact code, location and suggested fix.
