# Metadata API

> **Not part of the app's hosting model.** The Android app reads the static GitHub Pages
> catalog instead (see [hosting.md](hosting.md)); nothing in `android/` calls these
> endpoints, and Retrofit was removed from the build. This FastAPI service remains as an
> optional local tool for browsing the pipeline output.

FastAPI, SQLite, versioned at `/api/v1`. Start with:

```bash
ASSET_STUDIO_API_KEY=change-me \
ASSET_STUDIO_PUBLIC_BASE_URL=https://api.example.com \
uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/health` | version, counts, auth mode, storage driver, validator |
| GET | `/api/v1/assets` | `kind`, `search`, `sort=name\|recent\|size\|status`, `limit` |
| GET | `/api/v1/assets/{id}` | versioned manifest |
| GET | `/api/v1/assets/{id}/metadata` | full inspection metadata |
| GET | `/api/v1/assets/{id}/download` | GLB; `X-Asset-Sha256`; accepts `expires`+`signature` |
| POST | `/api/v1/assets/{id}/signed-url` | `{url, expires_at, external}` |
| GET | `/api/v1/models` | `{count, models[]}` |
| GET | `/api/v1/models/{model_id}` | manifest |
| GET | `/api/v1/animations` | `{count, animations[]}` |
| GET | `/api/v1/animations/{animation_id}` | manifest |
| GET | `/api/v1/models/{model_id}/compatible-animations` | compatibility + playable + reasons |
| GET | `/api/v1/processing-jobs/{job_id}` | job status, asset ids, timestamps |
| GET | `/api/v1/releases/latest` | signed release manifest or honest local status |
| GET | `/api/v1/reports/{kind}/{asset_id}` | generated report (`kind=models\|animations`) |

## Security behaviour

- `ASSET_STUDIO_API_KEY` set → every endpoint requires `X-API-Key` (constant-time compare). Unset → explicitly reported "local demo mode".
- Rate limiting: 120 req/min/IP by default (`ASSET_STUDIO_RATE_LIMIT`).
- Signed URLs: HMAC-SHA256 over `asset_id:expires` with `ASSET_STUDIO_SIGNING_SECRET`; TTL 900 s. Without a configured secret a demo key is derived from the asset id — production must set the secret.
- Downloads resolve paths through `safe_resolve()`; `../` traversal is rejected.
- Storage driver `local` (default) or `s3`; S3 credentials are read only from environment variables, never from the APK or manifests.
- Errors are JSON `{ "error": { "status": ..., "detail": ... } }` with correct HTTP codes (400/401/403/404/429).
- `GET /` serves the read-only operations console; `/docs` and `/redoc` expose OpenAPI.

## Database

`backend/database/db.py` creates `assets`, `compatibility`, `jobs`, `reports` tables. `sync_from_manifests()` is idempotent and is called by `asset-studio build-index` and at app startup. Metadata always originates from published manifests — the API never guesses asset properties.
