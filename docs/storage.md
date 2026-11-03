# Persistent storage and publication

## Why

GitHub Actions runners are destroyed after a job. Processed GLBs must therefore live in object storage; manifests and reports are small JSON/Markdown and are published as release artifacts plus ingested into the API database. The APK never contains storage credentials — it requests a short-lived signed URL from the API.

## Local driver (default)

`ASSET_STUDIO_STORAGE=local` (default). Downloads resolve `processed/...` relative to the repository root via `backend/security.safe_resolve()`, which rejects `../` traversal. Suitable for local development and a single self-hosted API host.

## S3-compatible driver

Set:

```bash
export ASSET_STUDIO_STORAGE=s3
export ASSET_STUDIO_S3_BUCKET=my-assets
export ASSET_STUDIO_S3_ENDPOINT=https://s3.example.com   # optional; AWS default otherwise
export ASSET_STUDIO_S3_REGION=us-east-1
export ASSET_STUDIO_S3_ACCESS_KEY=...
export ASSET_STUDIO_S3_SECRET_KEY=...
export ASSET_STUDIO_SIGNING_SECRET=...                   # for signed URLs
export ASSET_STUDIO_PUBLIC_BASE_URL=https://api.example.com
```

`backend/storage.py::S3Storage` uses `boto3` (`pip install boto3`, not pinned in the core requirements because it is optional). `POST /api/v1/assets/{id}/signed-url` returns a presigned `get_object` URL when S3 is active; otherwise an HMAC-signed API URL.

## Atomic publication (`scripts/publish_storage.py`)

1. Collect every `processed/**/*.glb` (source hashes already recorded in manifests).
2. Upload to `staging/<run-id>/<relative path>`.
3. `head_object` verifies each uploaded byte count.
4. With `--then-promote`, `copy_object` moves staging objects to their final `assets/` keys.
5. Write `manifests/releases/latest.json` locally and upload it as `manifests/releases/latest.json`.
6. Without credentials the script prints a clear no-op message and exits 0, so CI does not fail in a fork.

`manifests/index.json` (built by `asset-studio build-index`) contains counts and the full manifest list; `GET /api/v1/releases/latest` serves the release manifest or an honest local status.

## Integrity

- Every manifest records `source.sha256` and `processed.sha256`.
- Downloads set `X-Asset-Sha256`.
- The Android downloader recomputes SHA-256 while streaming and deletes the partial file on mismatch.
- No credentials, tokens or PATs are written into manifests, reports or the APK.
