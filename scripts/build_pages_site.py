#!/usr/bin/env python3
"""Build the static asset catalog that GitHub Pages serves.

The app used to talk to the FastAPI service in ``backend/``. That is not the
intended hosting model: GitHub Pages is a static host, so everything the client
needs has to exist as files inside the site:

* ``catalog/index.json`` — the whole asset listing (models + animations) with
  absolute HTTPS URLs, sizes and SHA-256 digests;
* ``catalog/models/<id>.json`` and ``catalog/animations/<id>.json`` — the
  published manifest merged with the processed metadata, so one request is
  enough to render a detail screen;
* ``assets/<kind>/<id>/…`` — the downloadable geometry. A GLB is a single file,
  but a glTF carries its payload in sidecars (``.bin`` buffers, ``.png``/``.jpg``
  textures); those are published too and listed as ``dependencies`` on the asset
  entry, with their own URLs, byte counts and digests.

Every URL in the catalog is absolute and https, built from ``--base-url`` (or
``GITHUB_REPOSITORY``), so a client never has to guess a host and never has to
resolve a placeholder such as ``asset-studio.example.invalid``.

Usage
-----
    python scripts/build_pages_site.py --output site
    python scripts/build_pages_site.py --output site --base-url https://owner.github.io/repo
    python scripts/build_pages_site.py --output site --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from processing.glb import GLBError, buffer_view_bytes, parse_glb  # noqa: E402

CATALOG_SCHEMA_VERSION = "1.0.0"
CATALOG_KIND = "asset-studio-static-catalog"
IMAGE_SUFFIX = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
PLACEHOLDER_HOSTS = ("example.invalid", "example.com")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0")
# GitHub Pages refuses files over 100 MiB and caps a site at 1 GiB; stay below both.
MAX_FILE_BYTES = 95 * 1024 * 1024
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class SiteError(RuntimeError):
    """Raised when the catalog cannot be built honestly."""


# --------------------------------------------------------------------------- #
# URLs
# --------------------------------------------------------------------------- #
def pages_base_url(repository: str | None = None, *, allow_http: bool = False) -> str:
    """Return the GitHub Pages base URL of this repository.

    ``https://<owner>.github.io/<repo>`` is deterministic for a project site, so it
    is derived from the repository name instead of being typed by hand. Anything
    that looks like a placeholder is rejected: the client must never again be
    pointed at a host that cannot resolve.
    """
    slug = (repository or os.environ.get("GITHUB_REPOSITORY") or "").strip()
    if not slug:
        raise SiteError(
            "no repository: pass --base-url or set GITHUB_REPOSITORY "
            "(e.g. GITHUB_REPOSITORY=owner/repo)")
    slug = slug.removesuffix(".git").strip("/")
    if not REPO_PATTERN.match(slug):
        raise SiteError(f"cannot derive a Pages URL from repository {slug!r}")
    owner, repo = slug.split("/", 1)
    scheme = "http" if allow_http else "https"
    return f"{scheme}://{owner.lower()}.github.io/{repo}"


def normalise_base_url(base_url: str, *, allow_http: bool = False) -> str:
    """Validate a caller-supplied base URL and strip trailing slashes."""
    base = base_url.strip().rstrip("/")
    if not base:
        raise SiteError("base URL is empty")
    if not allow_http and not base.startswith("https://"):
        raise SiteError(f"Pages URL must be https, got {base!r}")
    lowered = base.lower()
    for placeholder in PLACEHOLDER_HOSTS:
        if placeholder in lowered:
            raise SiteError(f"refusing placeholder host {placeholder!r} in {base!r}")
    if allow_http and not any(host in lowered for host in LOCAL_HOSTS):
        raise SiteError("--allow-http is a local smoke test: point it at localhost")
    return base


# --------------------------------------------------------------------------- #
# GLB -> glTF + BIN + PNG
# --------------------------------------------------------------------------- #
def glb_to_gltf(glb: bytes) -> dict[str, Any]:
    """Split a GLB into a glTF document, its BIN buffer and its image files.

    Filament and every other glTF loader can consume a self-contained GLB, but the
    site also has to prove that the multi-file form works: a ``.gltf`` that points
    at a ``.bin`` and at PNG/JPEG textures is the shape that breaks naive static
    hosting (relative URLs, missing sidecars). Returns ``{}`` when the payload is
    not a GLB or has nothing to split.
    """
    try:
        parsed = parse_glb(glb)
    except GLBError:
        return {}
    doc = json.loads(json.dumps(parsed["json"]))  # deep copy: we mutate below
    binary: bytes = parsed["bin"]
    textures: dict[str, bytes] = {}

    buffers = doc.get("buffers") or []
    if not buffers:
        return {}
    first = buffers[0]
    if binary:
        first["uri"] = "scene.bin"
    elif first.get("uri"):
        # Already a data: URI or an external file — cannot be split safely.
        return {}

    for index, image in enumerate(doc.get("images") or []):
        uri = image.get("uri")
        if uri and not uri.startswith("data:"):
            continue  # already an external file; nothing to extract
        view_index = image.get("bufferView")
        if view_index is None:
            continue
        try:
            data = buffer_view_bytes(doc["bufferViews"][view_index], binary)
        except (KeyError, IndexError, ValueError):
            continue
        suffix = IMAGE_SUFFIX.get(str(image.get("mimeType", "")).lower(), ".png")
        name = f"textures/{index}{suffix}"
        textures[name] = data
        image.pop("bufferView", None)
        image["uri"] = name

    return {"gltf": doc, "bin": binary, "textures": textures}


# --------------------------------------------------------------------------- #
# Catalog documents
# --------------------------------------------------------------------------- #
def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rel(path: Path) -> str:
    """Path relative to the repo when possible; absolute paths (tests) untouched."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SiteError(f"cannot read {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise SiteError(f"{path} is not a JSON object")
    return document


def _asset_entry(manifest: dict[str, Any], kind: str, base_url: str) -> dict[str, Any]:
    processed = manifest.get("processed") or {}
    clips = (manifest.get("animations") or {}).get("clips") or []
    duration = max((float(c.get("duration_seconds") or 0.0) for c in clips), default=0.0)
    skeleton = manifest.get("skeleton") or {}
    return {
        "id": manifest.get("id", ""),
        "kind": "model" if kind == "models" else "animation",
        "name": manifest.get("name", ""),
        "category": manifest.get("category", ""),
        "source_format": (manifest.get("source") or {}).get("format", ""),
        "format": "GLB",
        "bytes": int(processed.get("bytes") or 0),
        "sha256": processed.get("sha256") or "",
        "validation_status": (manifest.get("validation") or {}).get("status", ""),
        "humanoid": bool(skeleton.get("humanoid")),
        "target_character_id": manifest.get("target_character_id", ""),
        "retarget_status": manifest.get("retarget_status", ""),
        "clip_count": len(clips),
        "duration_seconds": round(duration, 6),
        "updated_at": manifest.get("timestamp", ""),
        "download_url": f"{base_url}/assets/{kind}/{manifest.get('id')}/{manifest.get('id')}.glb",
        "manifest_url": f"{base_url}/catalog/{kind}/{manifest.get('id')}.json",
    }


def build_catalog(manifests_dir: Path, processed_dir: Path, base_url: str, *,
                  emit_gltf: bool = True,
                  max_file_bytes: int = MAX_FILE_BYTES,
                  allow_http: bool = False) -> dict[str, Any]:
    """Build the whole site in memory.

    Returns ``{"catalog": {...}, "files": {relative path: bytes}, "skipped": [...]}``.
    Pure apart from reading the pipeline outputs, which makes it testable without
    touching the filesystem output.
    """
    base_url = normalise_base_url(base_url, allow_http=allow_http)
    files: dict[str, bytes] = {}
    skipped: list[dict[str, str]] = []
    catalog: dict[str, Any] = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "kind": CATALOG_KIND,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "counts": {"models": 0, "animations": 0},
        "models": [],
        "animations": [],
    }

    for kind in ("models", "animations"):
        manifest_paths = sorted((manifests_dir / kind).glob("*.json"))
        catalog["counts"]["models" if kind == "models" else "animations"] = len(manifest_paths)
        for manifest_path in manifest_paths:
            manifest = _read_json(manifest_path)
            asset_id = str(manifest.get("id") or manifest_path.stem)
            kind_singular = "model" if kind == "models" else "animation"
            glb_path = processed_dir / kind / asset_id / f"{asset_id}.glb"
            if not glb_path.is_file():
                skipped.append({"id": asset_id, "kind": kind_singular,
                                "reason": f"no processed payload at {_rel(glb_path)}"})
                continue
            glb = glb_path.read_bytes()
            if len(glb) > max_file_bytes:
                skipped.append({"id": asset_id, "kind": kind_singular,
                                "reason": f"{len(glb)} bytes exceeds the {max_file_bytes} byte "
                                          f"static-hosting limit"})
                continue

            entry = _asset_entry(manifest, kind, base_url)
            # The digest published in the manifest must match the bytes we serve.
            if entry["sha256"] and entry["sha256"] != _sha256(glb):
                skipped.append({"id": asset_id, "kind": kind_singular,
                                "reason": "processed bytes do not match the manifest sha256"})
                continue
            entry["bytes"] = len(glb)
            entry["sha256"] = _sha256(glb)

            asset_root = f"assets/{kind}/{asset_id}"
            files[f"{asset_root}/{asset_id}.glb"] = glb
            document: dict[str, Any] = dict(manifest)
            document["assets"] = {
                "format": "GLB",
                "glb": {"path": f"{asset_root}/{asset_id}.glb",
                        "url": entry["download_url"], "bytes": entry["bytes"],
                        "sha256": entry["sha256"]},
                "gltf": None,
                "dependencies": [],
            }

            metadata_path = processed_dir / kind / asset_id / f"{asset_id}.metadata.json"
            if metadata_path.is_file():
                document["metadata"] = _read_json(metadata_path)

            if emit_gltf:
                split = glb_to_gltf(glb)
                if split:
                    dependencies: list[dict[str, Any]] = []
                    gltf_bytes = json.dumps(split["gltf"], separators=(",", ":")).encode("utf-8")
                    files[f"{asset_root}/scene.gltf"] = gltf_bytes
                    gltf_entry = {"path": f"{asset_root}/scene.gltf",
                                  "url": f"{base_url}/{asset_root}/scene.gltf",
                                  "bytes": len(gltf_bytes), "sha256": _sha256(gltf_bytes)}
                    if split["bin"]:
                        files[f"{asset_root}/scene.bin"] = split["bin"]
                        dependencies.append({
                            "role": "buffer", "path": f"{asset_root}/scene.bin",
                            "url": f"{base_url}/{asset_root}/scene.bin",
                            "bytes": len(split["bin"]), "sha256": _sha256(split["bin"])})
                    for name, data in sorted(split["textures"].items()):
                        files[f"{asset_root}/{name}"] = data
                        dependencies.append({
                            "role": "texture", "path": f"{asset_root}/{name}",
                            "url": f"{base_url}/{asset_root}/{name}",
                            "bytes": len(data), "sha256": _sha256(data)})
                    document["assets"]["gltf"] = gltf_entry
                    document["assets"]["dependencies"] = dependencies
                    entry["gltf_url"] = gltf_entry["url"]
                    entry["dependencies"] = dependencies

            files[f"catalog/{kind}/{asset_id}.json"] = (
                json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
            catalog["models" if kind == "models" else "animations"].append(entry)

    for bucket in ("models", "animations"):
        catalog[bucket].sort(key=lambda item: (str(item.get("category", "")), str(item.get("name", ""))))
    catalog["counts"]["models"] = len(catalog["models"])
    catalog["counts"]["animations"] = len(catalog["animations"])
    catalog["skipped"] = skipped
    catalog["total_bytes"] = sum(len(data) for name, data in files.items()
                                 if name.startswith("assets/"))
    files["catalog/index.json"] = (json.dumps(catalog, indent=2, sort_keys=True) + "\n").encode("utf-8")
    files[".nojekyll"] = b""
    files["index.html"] = _index_html(catalog).encode("utf-8")
    return {"catalog": catalog, "files": files, "skipped": skipped}


def _index_html(catalog: dict[str, Any]) -> str:
    rows = []
    for kind in ("models", "animations"):
        if not catalog[kind]:
            continue
        rows.append(f"<h2>{kind.title()} ({len(catalog[kind])})</h2>")
        rows.append("<table><tr><th>name</th><th>category</th><th>bytes</th>"
                    "<th>validation</th><th>asset</th></tr>")
        for item in catalog[kind]:
            gltf = (f' · <a href="{item["gltf_url"]}">glTF</a>' if item.get("gltf_url") else "")
            rows.append(
                f"<tr><td>{item['name']}</td><td>{item['category']}</td>"
                f"<td>{item['bytes']}</td><td>{item['validation_status']}</td>"
                f'<td><a href="{item["download_url"]}">GLB</a>{gltf}</td></tr>')
        rows.append("</table>")
    counts = catalog["counts"]
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>3D Asset Studio — static catalog</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:2rem;}"
        "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:.25rem .5rem}"
        "th{background:#f2f2f2;text-align:left}</style></head><body>"
        "<h1>3D Asset Studio — static catalog</h1>"
        f"<p>{counts['models']} model(s), {counts['animations']} animation(s). "
        "Served by GitHub Pages; there is no API server. "
        f"Machine-readable index: <a href=\"catalog/index.json\">catalog/index.json</a>.</p>"
        + "".join(rows) + f"<p>Generated {catalog['generated_at']}.</p></body></html>\n")


def write_site(built: dict[str, Any], output: Path) -> dict[str, int]:
    """Write the in-memory site to disk, replacing any previous build."""
    output.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, data in built["files"].items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written += 1
    return {"files": written, "bytes": sum(len(d) for d in built["files"].values())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", default="site", help="directory to write the site into")
    parser.add_argument("--base-url", default=None,
                        help="absolute Pages base URL (default: derived from GITHUB_REPOSITORY)")
    parser.add_argument("--repository", default=None,
                        help="owner/repo to derive the base URL from")
    parser.add_argument("--manifests", default="manifests")
    parser.add_argument("--processed", default="processed")
    parser.add_argument("--no-emit-gltf", action="store_true",
                        help="publish only the GLB, no glTF/BIN/texture sidecars")
    parser.add_argument("--max-file-bytes", type=int, default=MAX_FILE_BYTES)
    parser.add_argument("--allow-http", action="store_true",
                        help="local smoke tests only: build against http://localhost")
    parser.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = parser.parse_args(argv)

    try:
        base_url = (normalise_base_url(args.base_url, allow_http=args.allow_http)
                    if args.base_url else pages_base_url(args.repository))
        built = build_catalog(ROOT / args.manifests, ROOT / args.processed, base_url,
                              emit_gltf=not args.no_emit_gltf,
                              max_file_bytes=args.max_file_bytes,
                              allow_http=args.allow_http)
    except SiteError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    catalog = built["catalog"]
    dependencies = sum(len(item.get("dependencies", [])) for item in
                       catalog["models"] + catalog["animations"])
    gltf_assets = sum(1 for item in catalog["models"] + catalog["animations"]
                      if item.get("gltf_url"))
    print(f"base_url            {catalog['base_url']}")
    print(f"models              {catalog['counts']['models']}")
    print(f"animations          {catalog['counts']['animations']}")
    print(f"glTF variants       {gltf_assets} ({dependencies} .bin/.png sidecar(s))")
    print(f"payload bytes       {catalog['total_bytes']}")
    print(f"skipped             {len(built['skipped'])}")
    for entry in built["skipped"][:20]:
        print(f"  - {entry['kind']}/{entry['id']}: {entry['reason']}")

    if args.dry_run:
        print("dry run: nothing written")
        return 0 if catalog["counts"]["models"] + catalog["counts"]["animations"] else 1

    stats = write_site(built, ROOT / args.output)
    print(f"written             {stats['files']} file(s), {stats['bytes']} bytes -> {args.output}")
    print(f"catalog             {catalog['base_url']}/catalog/index.json")
    return 0 if catalog["counts"]["models"] + catalog["counts"]["animations"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
