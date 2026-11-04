"""Tests for the GitHub Pages static-hosting pipeline.

Two properties matter and are both checked here:

* **the catalog is honest** — every URL is absolute https under the published base,
  every size/digest matches the bytes that will be served, and an asset whose
  payload is missing or whose digest disagrees is reported, never published;
* **the site is verifiable** — `verify_pages_deployment` really fetches the files
  (here from a throwaway HTTP server) and compares them with the catalog, so the
  `asset-studio.example.invalid` class of bug fails a build instead of an app.
"""
from __future__ import annotations

import functools
import hashlib
import http.server
import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from processing.glb import write_glb  # noqa: E402
from scripts.build_pages_site import (  # noqa: E402
    SiteError, build_catalog, glb_to_gltf, normalise_base_url, pages_base_url, write_site)
from scripts.verify_pages_deployment import (  # noqa: E402
    VerifyError,
    main,
    verify_catalog,
)

BASE = "https://example-owner.github.io/3dmodel-gen-system-"


def _glb_with_texture() -> bytes:
    """A minimal GLB whose image lives in the BIN chunk (a PNG payload)."""
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    binary = png + b"\x00\x00\x00\x00"  # image bytes, then a dummy buffer tail
    doc = {
        "asset": {"version": "2.0", "generator": "test"},
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "root", "mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(png)}],
        "images": [{"bufferView": 0, "mimeType": "image/png"}],
        "textures": [{"source": 0}],
    }
    return write_glb(doc, binary)


def _manifest(asset_id: str, kind_path: str, glb: bytes, name: str = "Scout") -> dict:
    return {
        "schema_version": "1.1.0",
        "id": asset_id, "kind": "model" if kind_path == "models" else "animation",
        "name": name, "category": "character",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "source": {"path": f"assets/{kind_path}/{asset_id}.glb", "sha256": "s" * 64, "format": "GLB"},
        "processed": {"path": f"processed/{kind_path}/{asset_id}/{asset_id}.glb",
                      "sha256": hashlib.sha256(glb).hexdigest(), "bytes": len(glb),
                      "format": "GLB"},
        "validation": {"status": "PASS", "report": f"reports/{kind_path}/{asset_id}.json"},
        "skeleton": {"humanoid": True},
        "animations": {"clips": [{"name": "walk", "duration_seconds": 1.0}]},
    }


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    """A miniature pipeline output: one model, one animation, one broken asset."""
    glb = _glb_with_texture()
    for kind_path, asset_id in (("models", "model_a"), ("animations", "anim_a")):
        manifest = _manifest(asset_id, kind_path, glb, name=asset_id)
        manifest_dir = tmp_path / "manifests" / kind_path
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / f"{asset_id}.json").write_text(json.dumps(manifest), encoding="utf-8")
        out = tmp_path / "processed" / kind_path / asset_id
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{asset_id}.glb").write_bytes(glb)
        (out / f"{asset_id}.metadata.json").write_text(
            json.dumps({"geometry": {"mesh_count": 1}, "issues": []}), encoding="utf-8")

    # An asset whose processed payload does not exist must be skipped, not published.
    broken = _manifest("model_missing", "models", glb)
    (tmp_path / "manifests" / "models" / "model_missing.json").write_text(
        json.dumps(broken), encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------------------- #
# URL derivation
# --------------------------------------------------------------------------- #
def test_pages_base_url_is_derived_from_the_repository_slug(monkeypatch):
    # CI exports GITHUB_REPOSITORY; the fallback below must be tested without it.
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert pages_base_url("adii14990adi-spec/3dmodel-gen-system-") == \
        "https://adii14990adi-spec.github.io/3dmodel-gen-system-"
    assert pages_base_url("owner/repo.git") == "https://owner.github.io/repo"
    assert pages_base_url("Owner/Repo/") == "https://owner.github.io/Repo"
    with pytest.raises(SiteError):
        pages_base_url("not-a-slug")
    with pytest.raises(SiteError):
        pages_base_url("")


def test_placeholder_hosts_are_refused():
    for bad in ("https://asset-studio.example.invalid/api/v1/",
                "https://example.com/catalog", "http://owner.github.io/repo"):
        with pytest.raises(SiteError):
            normalise_base_url(bad)
    assert normalise_base_url("https://owner.github.io/repo/") == "https://owner.github.io/repo"
    # --allow-http exists only for local smoke tests and still bans placeholders.
    with pytest.raises(SiteError):
        normalise_base_url("https://asset-studio.example.invalid", allow_http=True)


# --------------------------------------------------------------------------- #
# GLB -> glTF + BIN + PNG
# --------------------------------------------------------------------------- #
def test_glb_splits_into_gltf_bin_and_texture():
    split = glb_to_gltf(_glb_with_texture())
    assert split, "a GLB with a BIN chunk must be splittable"
    assert split["bin"], "the BIN chunk becomes scene.bin"
    assert list(split["textures"]) == ["textures/0.png"]
    assert split["textures"]["textures/0.png"].startswith(b"\x89PNG")
    document = split["gltf"]
    assert document["buffers"][0]["uri"] == "scene.bin"
    assert document["images"][0]["uri"] == "textures/0.png"
    assert "bufferView" not in document["images"][0]
    assert document["asset"]["version"] == "2.0"


def test_non_glb_input_is_not_split():
    assert glb_to_gltf(b"not a glb at all") == {}


# --------------------------------------------------------------------------- #
# Catalog building
# --------------------------------------------------------------------------- #
def test_catalog_publishes_absolute_https_urls_and_digests(tree: Path):
    built = build_catalog(tree / "manifests", tree / "processed", BASE)
    catalog = built["catalog"]

    assert catalog["kind"] == "asset-studio-static-catalog"
    assert catalog["base_url"] == BASE.rstrip("/")
    assert catalog["counts"] == {"models": 1, "animations": 1}
    model = catalog["models"][0]
    assert model["id"] == "model_a"
    assert model["download_url"] == f"{BASE.rstrip('/')}/assets/models/model_a/model_a.glb"
    assert model["manifest_url"] == f"{BASE.rstrip('/')}/catalog/models/model_a.json"
    assert model["download_url"].startswith("https://")
    assert model["bytes"] == len(_glb_with_texture())
    assert model["sha256"] == hashlib.sha256(_glb_with_texture()).hexdigest()

    # glTF + BIN + PNG are published together, all under the base URL.
    assert model["gltf_url"].endswith("/assets/models/model_a/scene.gltf")
    roles = sorted(dep["role"] for dep in model["dependencies"])
    assert roles == ["buffer", "texture"]
    for dependency in model["dependencies"]:
        assert dependency["url"].startswith(BASE.rstrip("/") + "/assets/")
        assert dependency["sha256"] and len(dependency["sha256"]) == 64
    texture = next(d for d in model["dependencies"] if d["role"] == "texture")
    assert texture["path"].endswith("textures/0.png")
    assert texture["bytes"] == len(built["files"][texture["path"]])


def test_catalog_documents_carry_the_manifest_metadata_and_urls(tree: Path):
    built = build_catalog(tree / "manifests", tree / "processed", BASE)
    document = json.loads(built["files"]["catalog/models/model_a.json"])
    assert document["id"] == "model_a"
    assert document["metadata"]["geometry"]["mesh_count"] == 1
    assert document["assets"]["format"] == "GLB"
    assert document["assets"]["glb"]["url"].startswith(BASE.rstrip("/"))
    assert document["assets"]["gltf"]["path"] == "assets/models/model_a/scene.gltf"
    uris = {dep["path"] for dep in document["assets"]["dependencies"]}
    assert uris == {"assets/models/model_a/scene.bin",
                    "assets/models/model_a/textures/0.png"}


def test_assets_without_payload_are_reported_not_published(tree: Path):
    built = build_catalog(tree / "manifests", tree / "processed", BASE)
    assert [entry["id"] for entry in built["skipped"]] == ["model_missing"]
    assert "no processed payload" in built["skipped"][0]["reason"]
    assert all(asset["id"] != "model_missing" for asset in built["catalog"]["models"])


def test_a_digest_mismatch_is_never_published(tree: Path):
    manifest_path = tree / "manifests" / "models" / "model_a.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["processed"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    built = build_catalog(tree / "manifests", tree / "processed", BASE)
    assert built["catalog"]["counts"]["models"] == 0
    assert "sha256" in built["skipped"][0]["reason"]


def test_site_root_has_nojekyll_and_a_human_browsable_index(tree: Path):
    built = build_catalog(tree / "manifests", tree / "processed", BASE)
    assert built["files"][".nojekyll"] == b""
    html = built["files"]["index.html"].decode()
    assert "static catalog" in html
    assert "catalog/index.json" in html
    assert "example.invalid" not in html
    assert "scene.gltf" in html, "the page should link the glTF variant too"


def test_written_site_round_trips(tree: Path, tmp_path: Path):
    built = build_catalog(tree / "manifests", tree / "processed", BASE)
    stats = write_site(built, tmp_path / "site")
    assert stats["files"] == len(built["files"])
    assert (tmp_path / "site" / "catalog" / "index.json").is_file()
    assert (tmp_path / "site" / "assets" / "models" / "model_a" / "scene.bin").is_file()


# --------------------------------------------------------------------------- #
# Deployment verification (real HTTP)
# --------------------------------------------------------------------------- #
class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args, **kwargs):  # keep the pytest output readable
        pass


@pytest.fixture()
def served_site(tree: Path, tmp_path: Path):
    """Serve a built site over real HTTP on an ephemeral port (Pages stand-in)."""
    site = tmp_path / "site"
    site.mkdir()
    handler = functools.partial(_QuietHandler, directory=str(site))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    built = build_catalog(tree / "manifests", tree / "processed", base, allow_http=True)
    write_site(built, site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield site, base
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_deployment_verification_passes_on_a_consistent_site(served_site):
    site, base = served_site
    result = verify_catalog(base, samples=5, allow_http=True)
    assert result["counts"] == {"models": 1, "animations": 1}
    assert {entry["id"] for entry in result["verified_assets"]} == {"model_a", "anim_a"}
    assert result["verified_dependencies"] >= 2, "the .bin and the .png must be fetched"


def test_deployment_verification_catches_a_tampered_asset(served_site):
    site, base = served_site
    victim = site / "assets" / "models" / "model_a" / "model_a.glb"
    victim.write_bytes(victim.read_bytes() + b"tampered")
    with pytest.raises(VerifyError) as error:
        verify_catalog(base, samples=5, allow_http=True)
    assert "byte" in str(error.value) or "SHA-256" in str(error.value)


def test_deployment_verification_catches_a_missing_catalog(served_site):
    site, base = served_site
    (site / "catalog" / "index.json").unlink()
    with pytest.raises(VerifyError) as error:
        verify_catalog(base, samples=1, allow_http=True)
    assert "404" in str(error.value)


def test_site_directory_verification_checks_urls_and_digests_without_network(tree, tmp_path):
    """Pre-deploy check: the exact bytes that will be published, at the exact paths."""
    built = build_catalog(tree / "manifests", tree / "processed", BASE)
    site = tmp_path / "site"
    write_site(built, site)

    result = verify_catalog(BASE, samples=5, site_dir=site)
    assert result["verified_from"].startswith("site directory")
    assert result["counts"] == {"models": 1, "animations": 1}
    assert result["verified_dependencies"] >= 2

    # A file whose bytes change after the catalog was written must be caught.
    victim = site / "assets" / "models" / "model_a" / "textures" / "0.png"
    victim.write_bytes(victim.read_bytes() + b"x")
    with pytest.raises(VerifyError):
        verify_catalog(BASE, samples=5, site_dir=site)

    # A URL that has no file behind it must be caught as well.
    (site / "assets" / "models" / "model_a" / "scene.bin").unlink()
    with pytest.raises(VerifyError) as error:
        verify_catalog(BASE, samples=5, site_dir=site)
    assert "missing" in str(error.value)


def test_deployment_verification_refuses_a_placeholder_base():
    with pytest.raises(VerifyError):
        verify_catalog("https://asset-studio.example.invalid/api/v1", samples=1)


def test_probe_mode_reports_the_http_evidence_per_url(served_site, capsys):
    """`--probe` turns every fetched URL into an annotation: status, bytes, digest."""
    site, base = served_site

    assert main(["--base-url", base, "--allow-http", "--samples", "5",
                 "--probe", "--report", str(site.parent / "report.json")]) == 0

    printed = capsys.readouterr().out
    notices = [line for line in printed.splitlines() if line.startswith("::notice title=Live HTTP")]
    assert notices, printed
    labels = {line.split("(", 1)[1].split(")")[0] for line in notices}
    # The catalog, a per-asset manifest, the GLB and the glTF sidecars are all covered.
    assert {"catalog", "manifest", "GLB"} <= labels
    assert any("-> HTTP 200," in line for line in notices)
    assert all("sha256=" in line for line in notices)
    report = json.loads((site.parent / "report.json").read_text())
    assert report["http"], "the report keeps the per-URL evidence"


def test_probe_mode_stays_quiet_for_a_site_directory_check(tree, tmp_path, capsys):
    """A pre-deploy check makes no HTTP requests, so it emits no HTTP annotations."""
    built = build_catalog(tree / "manifests", tree / "processed", BASE)
    site = tmp_path / "site"
    write_site(built, site)

    assert main(["--base-url", BASE, "--site-dir", str(site), "--probe"]) == 0
    assert "::notice title=Live HTTP" not in capsys.readouterr().out
