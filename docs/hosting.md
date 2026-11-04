# Hosting: GitHub Pages static catalog

The product is an Android app plus a **static** asset catalog. GitHub Pages is the
host; there is no API server in the runtime path. This document replaces the older
"FastAPI + `/api/v1`" assumption: `backend/` still exists in the repository as a
development tool, but nothing in the app talks to it, and no build step points at a
dynamic host.

## Why the app stopped using an API

The shipped APK carried `https://asset-studio.example.invalid/api/v1/` as its base
URL. That host is a placeholder (`.invalid` is reserved by RFC 2606 and never
resolves), so the first network call failed with:

```
Unable to resolve host "asset-studio.example.invalid"
```

The fix is not a different server address — it is a different hosting model. All the
app needs is metadata JSON and asset bytes, both of which are files. Serving them
statically removes the deployment, the secrets and the failure mode.

## The published URL

GitHub Pages project sites are deterministic:

```
https://<owner>.github.io/<repo>
```

For this repository:

```
https://adii14990adi-spec.github.io/3dmodel-gen-system-
```

That value is stored in exactly one place — `android/gradle.properties`:

```properties
assetStudio.pagesBaseUrl=https://adii14990adi-spec.github.io/3dmodel-gen-system-
```

and is injected into `BuildConfig.CATALOG_BASE_URL` for both the `:app` and
`:asset-library` modules. `scripts/check_android_sources.py` fails the build if it is
missing or not an `https://*.github.io/...` URL, and `.github/workflows/pages.yml`
fails the deployment if the base URL GitHub reports differs from it.

To read the URL GitHub actually deployed (never guess it):

```bash
gh api repos/adii14990adi-spec/3dmodel-gen-system-/pages           # {"html_url": ...}
gh run list --workflow=pages.yml --limit 1                          # deploy job URL
gh run view <run-id> --json jobs -q '.jobs[] | .name, .url'          # environment URL
```

If that call returns `404 Not Found`, Pages is **not** enabled for the repository.
The Pages workflow requests creation (`actions/configure-pages` with
`enablement: true`); a repository admin can also enable it in
*Settings → Pages → Source: GitHub Actions*. Nothing is published until then, and the
app honestly reports the unreachable URL instead of pretending to have data.

## What the site contains

`python scripts/build_pages_site.py --output site` turns the pipeline output into:

```
site/
├── .nojekyll                     # serve files verbatim, no Jekyll processing
├── index.html                    # human-browsable listing (links to every asset)
├── catalog/
│   ├── index.json                # the whole listing: models[], animations[], counts
│   ├── models/<id>.json          # manifest + processed metadata + absolute asset URLs
│   └── animations/<id>.json
└── assets/
    ├── models/<id>/<id>.glb      # single-file GLB (what the viewport renders)
    ├── models/<id>/scene.gltf    # multi-file glTF variant
    ├── models/<id>/scene.bin     # …its BIN buffer
    ├── models/<id>/textures/0.png# …its PNG textures
    └── animations/<id>/…         # same layout
```

Every asset is published twice: as a **GLB** (one self-contained file) and as a
**glTF bundle** (`.gltf` + `.bin` + textures). The GLB is what the renderer opens; the
bundle is what proves that a client can resolve and fetch the dependencies, and it is
the form to use when you need editable/individual textures.

### `catalog/index.json`

```json
{
  "schema_version": "1.0.0",
  "kind": "asset-studio-static-catalog",
  "generated_at": "2026-11-04T12:10:31+00:00",
  "base_url": "https://adii14990adi-spec.github.io/3dmodel-gen-system-",
  "counts": {"models": 7, "animations": 83},
  "models": [{
    "id": "model_rigged_animated_982b5099",
    "kind": "model", "name": "…", "category": "…",
    "bytes": 6128, "sha256": "486c3dc6…",
    "validation_status": "PASS", "humanoid": true,
    "download_url": "https://…/assets/models/<id>/<id>.glb",
    "manifest_url": "https://…/catalog/models/<id>.json",
    "gltf_url":     "https://…/assets/models/<id>/scene.gltf",
    "dependencies": [
      {"role": "buffer",  "path": "assets/models/<id>/scene.bin",       "url": "…", "bytes": 5820, "sha256": "…"},
      {"role": "texture", "path": "assets/models/<id>/textures/0.png", "url": "…", "bytes": 220,  "sha256": "…"}
    ]
  }],
  "animations": []
}
```

Rules the builder enforces:

* every URL is **absolute https** under `base_url` — a client never guesses a host;
* `bytes` and `sha256` describe the bytes that will actually be served (a manifest
  digest that disagrees with the file is reported in `skipped`, never published);
* an asset with no processed payload, or exceeding the per-file static-hosting limit,
  is listed in `skipped` with a reason instead of being advertised as downloadable;
* placeholder hosts (`example.invalid`, `example.com`) are refused outright.

### Per-asset document

`catalog/models/<id>.json` is the published manifest plus:

* `metadata` — the pipeline's inspection metadata (geometry, materials, textures,
  skeleton, clips, issues), so one request fills the whole detail screen;
* `assets.glb` / `assets.gltf` / `assets.dependencies` — absolute URLs, byte counts
  and digests for every file that belongs to the asset.

## How the app consumes it

| Step | Class | Behaviour |
| --- | --- | --- |
| metadata refresh | `CatalogClient.index()` | `GET <base>/catalog/index.json`; revalidates with `If-None-Match`; on failure returns the last cached copy and says so |
| listing | `AssetRepository.assets()` | reads the Room copy of the catalog, so search/sort/filtering work offline |
| detail | `AssetRepository.modelDetail()` / `animationDetail()` | `GET <base>/catalog/<kind>/<id>.json` (cached) |
| download | `AssetBundle.files()` + `AssetDownloader` | streams the GLB first, then `.gltf`, `.bin` and every texture at their published relative paths; each file is hashed while it is written and compared with the catalog digest |
| offline | Room + `filesDir/assets/<id>/` | the catalog listing and already-downloaded files need no network; a failed refresh never blanks the library |
| local files | `AssetRepository.importLocal()` | SAF import of a GLB/glTF stays on the device, is never overwritten by a refresh |

The three DataStore keys used to hold a server URL and an API key. They are replaced
by `catalog_base_url` (defaulting to the Pages site); the API key concept is gone
because every published file is public.

## Verifying a deployment

```bash
# local smoke test of the builder + verifier, no network
python -m pytest tests/unit/test_pages_site.py -q          # 14 tests

# against the live site (fails on any digest/size mismatch)
python scripts/verify_pages_deployment.py \
  --base-url https://adii14990adi-spec.github.io/3dmodel-gen-system- --samples 3
```

`verify_pages_deployment.py` fetches `catalog/index.json`, the sampled manifests, the
GLBs and **every `.bin`/`.png` sidecar**, and compares each response with the digest
the catalog advertises. It also refuses `http://`, loopback and placeholder hosts.
`.github/workflows/pages.yml` runs exactly this against
`steps.deployment.outputs.page_url` after deploying, so a broken deploy fails CI.

## Limitations (stated plainly)

* **Pages must be enabled** for the repository (admin action, or the workflow's
  `enablement: true` request). Until then the catalog URL returns 404 and the app
  shows "Catalog unreachable at …".
* **The viewport renders GLB.** The glTF bundle is published, downloaded and
  Khronos-validated, but the Filament viewport opens the self-contained GLB; a
  multi-file `.gltf` import is stored and inspectable but is not rendered.
* **No emulator evidence.** Nothing here was verified on a device or emulator; the
  JVM tests exercise the HTTP client, the bundle plan and the digest checks, not
  pixels.
* Static hosting cannot run the API features that used to exist (`/processing-jobs`,
  `/signed-url`, `/releases/latest`). The app no longer asks for them; `backend/`
  remains available as an optional local tool.
