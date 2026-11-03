"""Configuration, hashing, safe I/O and resource limits shared by the pipeline."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
SUPPORTED_MODELS = {".glb", ".gltf", ".fbx", ".obj", ".stl", ".ply"}
SUPPORTED_ANIMATIONS = {".glb", ".gltf", ".fbx", ".bvh", ".dae"}
SUPPORTED_TEXTURES = {".png", ".jpg", ".jpeg", ".webp"}
UNSAFE_SUFFIXES = {".py", ".js", ".sh", ".exe", ".dll", ".so", ".blend", ".php", ".bat", ".ps1"}


def load_config() -> dict[str, Any]:
    path = ROOT / "config" / "studio.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    limits = cfg.setdefault("limits", {})
    for key, env in (
        ("max_source_bytes", "ASSET_STUDIO_MAX_SOURCE_BYTES"),
        ("max_vertex_count", "ASSET_STUDIO_MAX_VERTEX_COUNT"),
        ("processing_timeout_seconds", "ASSET_STUDIO_TIMEOUT"),
    ):
        if os.environ.get(env):
            limits[key] = int(os.environ[env])
    paths = cfg.setdefault("paths", {})
    for key, env in (("processed", "ASSET_STUDIO_PROCESSED_DIR"),
                     ("manifests", "ASSET_STUDIO_MANIFEST_DIR"),
                     ("reports", "ASSET_STUDIO_REPORT_DIR"),
                     ("database", "ASSET_STUDIO_DB")):
        if os.environ.get(env):
            paths[key] = os.environ[env]
    return cfg


def path_for(key: str, *parts: str) -> Path:
    cfg = load_config()
    value = Path(cfg["paths"][key])
    p = value if value.is_absolute() else ROOT / value
    return p.joinpath(*parts)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=False, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def asset_id(kind: str, relative_path: str, source_hash: str) -> str:
    stem = Path(relative_path).stem.lower()
    safe = "".join(c if c.isalnum() else "_" for c in stem)[:32]
    prefix = "model" if kind == "model" else "anim"
    return f"{prefix}_{safe}_{source_hash[:8]}"


class UnsafeAssetError(ValueError):
    """Raised when an uploaded file fails type/size/path safety validation."""


def validate_source(path: Path, kind: str, max_bytes: int | None = None) -> None:
    """Treat uploaded 3D files as untrusted: enforce suffix, size and path safety."""
    cfg = load_config()
    limit = max_bytes or int(cfg["limits"]["max_source_bytes"])
    suffix = path.suffix.lower()
    allowed = SUPPORTED_MODELS if kind == "model" else SUPPORTED_ANIMATIONS
    if suffix in UNSAFE_SUFFIXES or suffix not in allowed:
        raise UnsafeAssetError(f"unsupported or unsafe file type: {suffix or '(none)'}")
    resolved = path.resolve()
    if ROOT.resolve() not in resolved.parents and resolved != ROOT.resolve():
        raise UnsafeAssetError("path escapes the repository root")
    if not path.is_file():
        raise UnsafeAssetError("source file does not exist")
    size = path.stat().st_size
    if size == 0:
        raise UnsafeAssetError("source file is empty")
    if size > limit:
        raise UnsafeAssetError(f"source file {size} B exceeds the {limit} B limit")


def category_of(relative_path: str) -> str:
    parts = Path(relative_path).parts
    if len(parts) >= 3 and parts[0] == "assets":
        return parts[2]
    if len(parts) >= 2:
        return parts[-2]
    return "uncategorised"


def log(kind: str, message: str) -> None:
    print(f"[{kind}] {message}", flush=True)
