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

`workflow_dispatch` accepts `all=true` for a full reprocess, or a `paths` list with **one source path per line** (so file names containing spaces work). Both processing workflows use `concurrency` groups so overlapping runs do not publish conflicting manifests.

### Dispatch input is data, never shell

`inputs.paths` is attacker-controlled. Neither workflow interpolates it into a `run:` block: the expression is declared under `env:` as `INPUT_PATHS`, the shell writes that variable byte-for-byte into `$RUNNER_TEMP/*.input`, and `scripts/dispatch_paths.py` parses the file. There is no `eval`, no `bash -c`, no generated command line and no word-splitting, so `$(…)`, backticks, `;`, quotes and newlines stay text, and `my prop.glb` stays one path.

`scripts/dispatch_paths.py --kind model|animation --paths-file <file> --null` accepts only what the pipeline can actually process:

| Rule | Rejected example |
|---|---|
| one repo-relative POSIX path per line, UTF-8, no blank lines | `assets/models/a.glb\n\n`, `\r\n` line endings |
| no absolute, drive-letter or UNC paths | `/etc/passwd`, `C:/Windows/…`, `\\server\share\x.glb` |
| no backslashes (canonical form only, nothing is silently rewritten) | `assets\models\..\..\etc\passwd` |
| no `.`, `..` or empty segments, no `./` prefix or `//` | `assets/models/../../etc/passwd`, `./assets/models/a.glb` |
| must live under the kind's asset root | `assets/animation/…` in a model job |
| extension must be in the pipeline allowlist (case-insensitive) | `x.py`, `x.sh`, `x.exe`, `x.blend`, no extension |
| must resolve (symlinks followed) to an existing regular file **inside** that root | `assets/models/evil.glb -> /etc/passwd` |

Every rejection exits with status 2, prints a `::error::` annotation and writes **no** tokens, so a partly valid request is never half-processed. `set -euo pipefail` turns that into a failed job.

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
