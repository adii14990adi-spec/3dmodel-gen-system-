# Architecture

## Data flow

```
source assets (assets/**)  ── path-triggered GitHub Actions ──► processing/
                                                                 │
                                     real glTF/BVH inspection, Khronos validation,
                                     compatibility checks, BVH→glTF retargeting
                                                                 │
                          processed/*.glb ──► persistent object storage (S3-compatible)
                          manifests/*.json ──► SQLite ──► FastAPI /api/v1
                          reports/*.json  ─────────────► /api/v1/reports/...
                                                                 │
                                              Android viewer (Kotlin/Compose/Filament)
                                              Room cache for offline GLBs
```

Actions runners are ephemeral. Nothing permanent is stored on a runner: processed GLBs belong in object storage, metadata in the API database, and reports/manifests in the release artifact.

## Module responsibilities

| Module | Responsibility |
|---|---|
| `processing/common.py` | config (`config/studio.yaml`), hashing, atomic writes, size/suffix/path limits |
| `processing/glb.py` | real glTF 2.0 reader, accessor decoding, inspector, writer (empty-array stripping) |
| `processing/bvh.py` | BVH hierarchy, channels, offsets, frames, fps, duration |
| `processing/compatibility.py` | canonical role mapping + hierarchy/spatial checks + ambiguity detection |
| `processing/retarget.py` | BVH→glTF animation samplers on a copied target character GLB |
| `processing/validate.py` | official Khronos validator wrapper and honest unavailable fallbacks |
| `processing/report.py`, `manifest.py`, `publish.py` | diagnostics, versioned manifests, atomic publication/index |
| `processing/blender/*.py` | CI headless Blender inspection/conversion/retarget for FBX/OBJ/STL/PLY/DAE |
| `backend/` | FastAPI, SQLite sync, auth/rate limiting/signed URLs, storage drivers, console |
| `android/*` | 8 Gradle modules; viewer and inspectors only |

## Key invariants

1. Original source assets are read-only; only `processed/` copies are written.
2. No skeleton, animation, material or UV is invented; missing data is reported as missing.
3. Folder names are metadata, not evidence of file contents.
4. Similar bone names alone never prove compatibility; hierarchy, spatial and ambiguity checks are mandatory.
5. GLB/glTF is the only runtime delivery format; Filament does not load arbitrary FBX/BVH.
6. Manifests contain hashes, processing version, target id, validation status and download reference — never credentials.
