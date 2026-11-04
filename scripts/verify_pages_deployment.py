#!/usr/bin/env python3
"""Verify a deployed GitHub Pages asset catalog over real HTTPS.

This is the check that would have caught the ``asset-studio.example.invalid``
mistake: it does not trust the configuration, it fetches the published files and
compares them with the digests the catalog advertises.

For every sampled asset it proves that:

* ``catalog/index.json`` is reachable, is https, and lists the assets;
* the absolute ``download_url`` of the asset answers with the advertised byte
  length and SHA-256 (so a truncated or stale Pages artefact fails);
* the published manifest is reachable and points at the same base URL;
* every glTF dependency (``.bin`` buffer, ``.png``/``.jpg`` texture) is reachable
  and matches its own digest, and the references inside the ``.gltf`` resolve to
  files that exist in the published tree.

Usage
-----
    python scripts/verify_pages_deployment.py --base-url https://owner.github.io/repo
    python scripts/verify_pages_deployment.py --catalog-url https://owner.github.io/repo/catalog/index.json
    python scripts/verify_pages_deployment.py --dry-run       # print the plan only

Exit codes: 0 verified, 1 verification failed, 2 bad arguments / unreachable site.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

PLACEHOLDER_HOSTS = ("example.invalid", "example.com")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0")
USER_AGENT = "asset-studio-pages-verifier/1.0"


class VerifyError(RuntimeError):
    pass


def fetch(url: str, timeout: float = 30.0) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(), {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        raise VerifyError(f"HTTP {exc.code} for {url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise VerifyError(f"cannot fetch {url}: {exc}") from exc


def check_url(url: str, base_url: str, *, what: str, allow_http: bool = False) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" and not allow_http:
        raise VerifyError(f"{what} must be https, got {url!r}")
    host = (parsed.netloc or "").lower()
    if any(placeholder in host for placeholder in PLACEHOLDER_HOSTS):
        raise VerifyError(f"{what} points at a placeholder host: {url!r}")
    if not allow_http and any(local in host for local in LOCAL_HOSTS):
        raise VerifyError(f"{what} points at a loopback host: {url!r}")
    base = base_url.rstrip("/")
    if url.rstrip("/") != base and not url.startswith(base + "/"):
        raise VerifyError(f"{what} is not under the Pages base URL {base_url}: {url!r}")


def verify_catalog(base_url: str, *, samples: int = 2, timeout: float = 30.0,
                   expect_gltf: bool = True,
                   allow_http: bool = False) -> dict[str, Any]:
    """Fetch and cross-check the published catalog. Returns a result document."""
    base_url = base_url.rstrip("/")
    index_url = f"{base_url}/catalog/index.json"
    raw, headers = fetch(index_url, timeout)
    try:
        catalog = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise VerifyError(f"catalog index is not JSON: {exc}") from exc

    if catalog.get("kind") != "asset-studio-static-catalog":
        raise VerifyError(f"unexpected catalog kind {catalog.get('kind')!r}")
    check_url(catalog.get("base_url", ""), base_url, what="catalog base_url",
              allow_http=allow_http)
    if catalog.get("base_url", "").rstrip("/") != base_url:
        raise VerifyError(f"catalog base_url {catalog.get('base_url')!r} is not {base_url!r}")

    counts = catalog.get("counts") or {}
    if not counts.get("models") and not counts.get("animations"):
        raise VerifyError("the published catalog is empty")

    result: dict[str, Any] = {
        "base_url": base_url,
        "index_url": index_url,
        "generated_at": catalog.get("generated_at"),
        "counts": {"models": counts.get("models", 0),
                   "animations": counts.get("animations", 0)},
        "content_type": headers.get("content-type", ""),
        "verified_assets": [],
        "verified_dependencies": 0,
        "bytes": 0,
    }

    for kind in ("models", "animations"):
        entries = catalog.get(kind) or []
        if not entries:
            continue
        # Prefer assets that actually publish glTF sidecars: that is the case a
        # single-file GLB download never exercises.
        ordered = sorted(entries, key=lambda item: 0 if item.get("dependencies") else 1)
        for entry in ordered[:samples]:
            check_url(entry.get("download_url", ""), base_url, what=f"{kind} download_url",
                      allow_http=allow_http)
            check_url(entry.get("manifest_url", ""), base_url, what=f"{kind} manifest_url",
                      allow_http=allow_http)

            payload, _ = fetch(entry["download_url"], timeout)
            if len(payload) != int(entry.get("bytes") or -1):
                raise VerifyError(f"{entry['id']}: served {len(payload)} bytes, "
                                  f"catalog says {entry.get('bytes')}")
            digest = hashlib.sha256(payload).hexdigest()
            if entry.get("sha256") and digest != entry["sha256"]:
                raise VerifyError(f"{entry['id']}: served SHA-256 does not match the catalog")

            manifest_raw, _ = fetch(entry["manifest_url"], timeout)
            manifest = json.loads(manifest_raw.decode("utf-8"))
            assets = manifest.get("assets") or {}
            glb = assets.get("glb") or {}
            if glb.get("url") != entry["download_url"]:
                raise VerifyError(f"{entry['id']}: manifest glb URL disagrees with the catalog")
            if glb.get("sha256") != digest:
                raise VerifyError(f"{entry['id']}: manifest glb digest disagrees with the served bytes")

            dependencies = assets.get("dependencies") or []
            gltf = assets.get("gltf") or {}
            if expect_gltf and entry.get("gltf_url"):
                gltf_raw, _ = fetch(gltf["url"], timeout)
                document = json.loads(gltf_raw.decode("utf-8"))
                declared = {d.get("path", "").split("/")[-1] for d in dependencies}
                for buffer in document.get("buffers") or []:
                    uri = buffer.get("uri")
                    if uri and not uri.startswith("data:") and uri.split("/")[-1] not in declared:
                        raise VerifyError(f"{entry['id']}: glTF buffer {uri} has no dependency entry")
                for image in document.get("images") or []:
                    uri = image.get("uri")
                    if uri and not uri.startswith("data:") and uri.split("/")[-1] not in declared:
                        raise VerifyError(f"{entry['id']}: glTF image {uri} has no dependency entry")

            for dependency in dependencies:
                check_url(dependency.get("url", ""), base_url,
                          what=f"{entry['id']} dependency", allow_http=allow_http)
                body, _ = fetch(dependency["url"], timeout)
                if len(body) != int(dependency.get("bytes") or -1):
                    raise VerifyError(f"{entry['id']}/{dependency['path']}: byte length mismatch")
                if hashlib.sha256(body).hexdigest() != dependency.get("sha256"):
                    raise VerifyError(f"{entry['id']}/{dependency['path']}: digest mismatch")
                result["verified_dependencies"] += 1

            result["bytes"] += len(payload)
            result["verified_assets"].append({
                "id": entry.get("id"), "kind": entry.get("kind"),
                "bytes": len(payload), "sha256": digest,
                "gltf": bool(entry.get("gltf_url")),
                "dependencies": len(dependencies),
            })

    if not result["verified_assets"]:
        raise VerifyError("no asset could be verified")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=None,
                        help="Pages site root, e.g. https://owner.github.io/repo")
    parser.add_argument("--catalog-url", default=None,
                        help="full URL of catalog/index.json (overrides --base-url)")
    parser.add_argument("--samples", type=int, default=2,
                        help="assets verified per kind (default 2)")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--report", default=None, help="write the JSON result here")
    parser.add_argument("--allow-http", action="store_true",
                        help="accept http:// (local smoke tests only; Pages is always https)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the URLs that would be verified, fetch nothing")
    args = parser.parse_args(argv)

    base_url = args.base_url
    if not base_url and args.catalog_url:
        parsed = urllib.parse.urlparse(args.catalog_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}{posixpath.dirname(posixpath.dirname(parsed.path))}"
    if not base_url:
        print("ERROR: pass --base-url or --catalog-url", file=sys.stderr)
        return 2
    base_url = base_url.rstrip("/")

    if args.dry_run:
        print(f"would verify {base_url}/catalog/index.json")
        print(f"  and up to {args.samples} model(s) + {args.samples} animation(s), "
              f"their manifests, GLBs and every .bin/.png sidecar")
        return 0

    try:
        result = verify_catalog(base_url, samples=args.samples, timeout=args.timeout,
                                allow_http=args.allow_http)
    except VerifyError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        if args.report:
            with open(args.report, "w", encoding="utf-8") as handle:
                json.dump({"ok": False, "base_url": base_url, "error": str(exc)}, handle, indent=2)
        return 1

    print(f"index               {result['index_url']}")
    print(f"catalog generated   {result['generated_at']}")
    print(f"counts              models={result['counts']['models']} "
          f"animations={result['counts']['animations']}")
    for asset in result["verified_assets"]:
        print(f"verified            {asset['kind']}/{asset['id']} {asset['bytes']} bytes "
              f"sha256={asset['sha256'][:12]}… gltf={asset['gltf']} "
              f"deps={asset['dependencies']}")
    print(f"dependencies        {result['verified_dependencies']} sidecar(s) verified")
    print("PASS: GitHub Pages catalog, manifests, GLBs and glTF sidecars all verified")

    if args.report:
        result["ok"] = True
        with open(args.report, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
