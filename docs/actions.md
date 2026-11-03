# GitHub Actions

| Workflow | Triggers | Work |
|---|---|---|
| `asset-processing.yml` | push `assets/models/**`, `processing/**`, `config/**`, validator; `workflow_dispatch` | changed-file detection, pinned Python/Node, Blender 4.2.23, model pipeline, index sync, artifacts, optional S3 publication |
| `animation-processing.yml` | push `assets/animation/**`, character changes, `processing/**`, bone mappings; `workflow_dispatch` | ensures model manifests, animation pipeline, retargeting, index, artifacts, optional publication |
| `android-build.yml` | push/PR `android/**`; `workflow_dispatch` | JDK 17, Android SDK 35, wrapper validation, `assembleDebug`, JVM unit tests, optional signed release, APK artifact |
| `validation.yml` | push/PR, `workflow_dispatch` | compileall, dataset generation, both pipelines, Khronos validation, pytest, Android source checks, report artifacts |
| `release.yml` | tag `v*`, `workflow_dispatch(tag)` | builds publication + APK, signs only with configured secrets, creates/updates release via `gh` |

## Changed-file detection

`scripts/changed_files.py --base <before> --head <sha> --kind model|animation --null` runs `git -c core.quotepath=false diff --name-only -z --diff-filter=ACMR <base> <head> --` and filters the result to the kind's prefix and extension allowlist. Deleted files never reach the pipeline, a rename yields only the new path, and an empty change set makes the workflow skip processing cleanly (`count=0`).

The workflow writes NUL-separated tokens to `$RUNNER_TEMP` and runs `xargs -0 -r python scripts/run_pipeline.py process-…`, so paths with spaces, quotes or unicode stay intact and are never interpolated into shell code. When the diff cannot be trusted (zero/absent `before` SHA on a new branch, force push, shallow history, unknown or option-like ref) the script prints `--all` and the job processes everything instead of silently skipping assets. The resolve step uses `set -euo pipefail`, so a genuine resolution failure fails the workflow.

`workflow_dispatch` accepts `all=true` for a full reprocess or a space-separated `paths` list (`all=true` is required for file names containing spaces, because that input is whitespace-split). Both processing workflows use `concurrency` groups so
overlapping runs do not publish conflicting manifests.

## Runners are temporary

Each job installs its own Python/Node/Blender; nothing persists on the runner. Outputs are uploaded as CI artifacts and, when configured, copied atomically to object storage. Metadata is re-ingested by `build-index` on the API host.

## Secrets (all optional; never in the APK)

| Secret | Purpose |
|---|---|
| `ASSET_STUDIO_S3_BUCKET`, `ASSET_STUDIO_S3_ENDPOINT`, `ASSET_STUDIO_S3_ACCESS_KEY`, `ASSET_STUDIO_S3_SECRET_KEY` | persistent object storage |
| `ASSET_STUDIO_SIGNING_SECRET` | HMAC signed download URLs |
| `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD` | signed APK |
| `GITHUB_TOKEN` | release creation (provided by Actions) |

## Recovery

Every CLI invocation records a job in `reports/jobs/<job-id>.json` with status, message and asset ids (`GET /api/v1/processing-jobs/{job_id}`). Publication uses a staging prefix so a failed run never leaves half-written final objects. Re-running the same workflow is idempotent because asset ids hash the canonical source path together with the source SHA-256 (`processing.common.asset_id`) and manifests are written atomically (`.tmp` then `replace`).

**Not executed here:** these workflows are untested in this sandbox (no GitHub Actions runner); `validation.yml` mirrors the exact commands that were run locally.
