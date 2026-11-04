"""Versioned publication manifests. Contains no credentials or tokens."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "1.1.0"


def build_manifest(*, asset_id: str, kind: str, name: str, category: str,
                   source_path: str, source_hash: str, source_format: str,
                   processed_path: str | None, processed_hash: str | None,
                   processed_bytes: int, processing_version: str,
                   validation_status: str, report: str, metadata: dict[str, Any] | None = None,
                   target_character_id: str = "", target_name: str = "",
                   retarget_status: str = "", compatibility: list[dict[str, Any]] | None = None,
                   download_reference: str = "", animation_asset: dict[str, Any] | None = None
                   ) -> dict[str, Any]:
    anim = (metadata or {}).get("animations") or {"clip_count": 0, "clips": []}
    skeleton = dict((metadata or {}).get("skeleton") or {})
    skeleton.setdefault("present", False)
    skeleton.setdefault("skeleton_count", 0)
    skeleton.setdefault("bone_count", len(skeleton.get("bones") or []))
    skeleton.setdefault("bones", [])
    skeleton.setdefault("skins", [])
    skeleton.setdefault("humanoid", False)
    skeleton.setdefault("humanoid_confidence", 0.0)
    manifest = {
        "schema_version": SCHEMA_VERSION, "id": asset_id, "kind": kind, "name": name,
        "category": category, "version": processing_version,
        "processing_version": processing_version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": {"path": source_path, "sha256": source_hash, "format": source_format},
        "processed": {"path": processed_path, "sha256": processed_hash,
                      "bytes": processed_bytes, "format": "GLB" if processed_path else None},
        "download_reference": download_reference or (f"assets/{asset_id}/download" if processed_path else ""),
        "validation": {"status": validation_status, "report": report},
        "geometry": (metadata or {}).get("geometry", {}),
        "materials": (metadata or {}).get("materials", []),
        "textures": (metadata or {}).get("textures", []),
        "skeleton": skeleton,
        "animations": anim,
        "target_character_id": target_character_id, "target_name": target_name,
        "retarget_status": retarget_status,
        "compatibility": compatibility or [],
        # Animation library metadata (schema 1.1.0, additive keys only).
        "original_filename": (animation_asset or {}).get("original_filename", ""),
        "organized_path": (animation_asset or {}).get("organized_path"),
        "classification": (animation_asset or {}).get("classification"),
        "skeleton_id": (animation_asset or {}).get("skeleton_id", ""),
        "compatibility_group": (animation_asset or {}).get("compatibility_group", ""),
        "root_motion": (animation_asset or {}).get("root_motion", None),
        "retarget_required": (animation_asset or {}).get("retarget_required", None),
        "retarget": (animation_asset or {}).get("retarget"),
        "retarget_validation": (animation_asset or {}).get("retarget_validation"),
        "metadata_path": f"processed/{'models' if kind == 'model' else 'animations'}/{asset_id}/{asset_id}.metadata.json",
    }
    serialised = str(manifest)
    for secret in ("password", "secret", "token", "credential", "access_key", "private_key"):
        if secret in serialised.lower():
            raise ValueError(f"manifest must not contain credentials ({secret})")
    return manifest
