# Security

## Untrusted 3D assets

- `processing/common.validate_source()` enforces an allowlist of model/animation suffixes, rejects `.py/.js/.sh/.exe/.dll/.so/.blend/.php/.bat/.ps1`, rejects empty files, enforces a 500 MiB maximum and resolves paths strictly inside the repository root.
- `BLENDER_DISABLE_AUTOEXEC=1` in CI and `processing/blender/_common.guard()` prevent embedded script execution.
- glTF JSON/BIN parsing bounds-checks chunk lengths and bufferView ranges; malformed GLBs produce an ERROR report rather than a crash.
- Processing has a 900 s timeout (`processing_timeout_seconds`), vertex-count and texture-size limits in `config/studio.yaml`, and a 20 000-frame animation cap.
- Original assets are never modified; repairs/normalisations apply only to generated processed copies.

## Dependency pinning

`requirements.txt`, `requirements-dev.txt`, `requirements-blender.txt`, `tools/gltf-validator/package.json` (`gltf-validator@2.0.0-dev.3.10`) and `android/gradle/libs.versions.toml` pin exact versions. The Gradle wrapper jar comes from the official Gradle v8.9.0 tag. `android-build.yml` runs `gradle/actions/wrapper-validation@v3`.

## API

- Optional `X-API-Key` auth with constant-time comparison; demo mode is explicitly reported.
- Per-IP rate limiting (120/min default).
- HMAC-SHA256 signed URLs with TTL; expired/tampered URLs return 403.
- Path traversal blocked in downloads, metadata and reports.
- Uniform JSON error responses; no stack traces to clients.
- Security headers: `Cache-Control: private` and `X-Asset-Sha256` on downloads.
- Android network security config: HTTPS-only in release, cleartext only for debug builds targeting `10.0.2.2`/`localhost`.

## No secrets in the APK

The app contains only a placeholder API base URL and an empty API key. The user supplies both in Settings; they are stored in DataStore, which is excluded from backups (`backup_rules.xml`, `data_extraction_rules.xml`). No GitHub PAT, S3 key, keystore or signing secret is compiled into the app. Signing material is written by CI from secrets and is git-ignored.

## Logging

The pipeline logs per asset without dumping binary content; reports include actionable diagnostics. The operations console is read-only. Android logs only OkHttp BASIC headers in debug builds.
