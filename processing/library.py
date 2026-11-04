"""Animation library discovery, inventory and safe copy-based organisation.

Responsibilities
----------------
* **Real discovery.** Files are found recursively and their format is detected
  from their *bytes*, never from the extension. Empty, corrupted and
  unsupported files are recorded explicitly — nothing is skipped silently.
* **Evidence-based classification.** Every asset is inspected (FBX, GLB/glTF or
  BVH) and classified by :mod:`processing.classify`.
* **Skeleton compatibility groups.** Animations are grouped only when their
  bone names, hierarchy and *rest-pose transforms* match; a matching bone count
  is never sufficient.
* **Safe organisation.** Originals are never moved, rewritten or deleted. The
  organiser copies into a deterministic destination, verifies SHA-256 of both
  sides, refuses to overwrite an existing file with different bytes, and writes
  a complete source→destination mapping.
"""
from __future__ import annotations

import json
import shutil
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from processing.classify import Classification, classify, category_root
from processing.common import (ROOT, SUPPORTED_ANIMATIONS, load_config, normalize_path,
                               sha256_file, asset_id)
from processing.compatibility import check_compatibility
from processing.fbx import FBXError, inspect_fbx
from processing.glb import GLBError, inspect_document, load_document

#: Default source archive directory (kept intact, excluded from automatic discovery).
SOURCE_ARCHIVE = "Upload"
#: Formats the animation pipeline can process.
SUPPORTED_CONTENT_FORMATS = {"FBX", "GLB", "GLTF", "BVH", "DAE"}

_TEXT_FORMATS = {"BVH": b"HIERARCHY", "DAE": b"<COLLADA", "OBJ": None, "PLY": None,
                 "STL_ASCII": None, "STL_BINARY": None}


class LibraryError(ValueError):
    """Raised when the library cannot be scanned or organised safely."""


# --------------------------------------------------------------------------- #
# Format detection
# --------------------------------------------------------------------------- #
def detect_format(data: bytes, name: str = "") -> dict[str, Any]:
    """Detect a file's real format from its content.

    Returns ``{"format", "encoding", "supported", "status", "detail"}``.
    """
    suffix = Path(name).suffix.lower() if name else ""
    if not data:
        return {"format": "EMPTY", "encoding": None, "supported": False,
                "status": "empty", "detail": "file is zero bytes"}
    if data.startswith(b"Kaydara FBX Binary"):
        return {"format": "FBX", "encoding": "binary", "supported": True,
                "status": "ok", "detail": ""}
    if data.lstrip()[:5] == b"; FBX":
        return {"format": "FBX", "encoding": "ascii", "supported": False,
                "status": "unsupported", "detail": "ASCII FBX is not supported; re-export as binary FBX"}
    if data[:4] == b"glTF":
        return {"format": "GLB", "encoding": "binary", "supported": True,
                "status": "ok", "detail": ""}
    stripped = data.lstrip()[:4096]
    if stripped.startswith(b"{"):
        try:
            doc = json.loads(data.decode("utf-8", "strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"format": "GLTF", "encoding": "json", "supported": False,
                    "status": "corrupted", "detail": "JSON header present but the document does not parse"}
        if isinstance(doc, dict) and "asset" in doc:
            return {"format": "GLTF", "encoding": "json", "supported": True,
                    "status": "ok", "detail": ""}
        return {"format": "JSON", "encoding": "json", "supported": False,
                "status": "unsupported", "detail": "JSON document without a glTF asset section"}
    if stripped.startswith(b"HIERARCHY"):
        return {"format": "BVH", "encoding": "text", "supported": True,
                "status": "ok", "detail": ""}
    if b"<COLLADA" in stripped:
        return {"format": "DAE", "encoding": "xml", "supported": True,
                "status": "ok", "detail": ""}
    if stripped.startswith(b"solid") and b"facet normal" in data[:2048]:
        return {"format": "STL", "encoding": "ascii", "supported": False,
                "status": "unsupported", "detail": "STL is not an animation format"}
    if data.startswith(b"ply"):
        return {"format": "PLY", "encoding": "binary", "supported": False,
                "status": "unsupported", "detail": "PLY is not an animation format"}
    if suffix:
        return {"format": suffix.lstrip(".").upper() or "UNKNOWN", "encoding": None,
                "supported": False, "status": "unsupported",
                "detail": f"content is not a recognised animation format (extension {suffix})"}
    return {"format": "UNKNOWN", "encoding": None, "supported": False,
            "status": "unsupported", "detail": "content is not a recognised animation format"}


# --------------------------------------------------------------------------- #
# Skeleton identity
# --------------------------------------------------------------------------- #
def skeleton_fingerprint(bones: list[dict[str, Any]]) -> str:
    """Hash of bone names, hierarchy and rest translations (metres, 4 decimals).

    Two skeletons with the same bone count but different rest poses or parents
    produce different fingerprints, which is the point.
    """
    from processing.common import sha256_bytes
    lines = []
    for bone in bones:
        translation = ",".join(f"{float(v):.4f}" for v in (bone.get("translation") or [0, 0, 0])[:3])
        rotation = ",".join(f"{float(v):.4f}" for v in (bone.get("rotation") or [0, 0, 0, 1])[:4])
        lines.append(f"{bone.get('name','')}|{bone.get('parent') or ''}|{translation}|{rotation}")
    return sha256_bytes("\n".join(lines).encode("utf-8"))


def skeleton_id(bones: list[dict[str, Any]]) -> str:
    return "skel_" + skeleton_fingerprint(bones)[:10]


# --------------------------------------------------------------------------- #
# Per-file inspection
# --------------------------------------------------------------------------- #
def _relative(path: Path) -> str:
    try:
        return normalize_path(str(path.resolve().relative_to(ROOT.resolve())))
    except ValueError:
        return normalize_path(str(path))


def inspect_asset(path: Path, *, classify_clips: bool = True) -> dict[str, Any]:
    """Inspect one source file and return its inventory record.

    Never raises for malformed input: problems are reported in ``errors`` with
    the file's status set accordingly, so nothing disappears silently.
    """
    data = path.read_bytes()
    rel = _relative(path)
    detected = detect_format(data, path.name)
    record: dict[str, Any] = {
        "source_path": rel,
        "file_name": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": len(data),
        "sha256": sha256_file(path),
        "detected_format": detected["format"],
        "detected_encoding": detected["encoding"],
        "detected_detail": detected["detail"],
        "format_supported": detected["supported"],
        "status": detected["status"],
        "clips": [],
        "clip_count": 0,
        "skeleton": {"present": False, "bone_count": 0, "skeleton_id": None},
        "classification": None,
        "destination": None,
        "root_motion": False,
        "retargeting_required": None,
        "errors": [],
        "warnings": [],
    }
    if detected["status"] != "ok":
        if detected["status"] == "unsupported":
            record["errors"].append({
                "code": "UNSUPPORTED_FORMAT",
                "description": f"{path.name}: {detected['detail']}",
            })
        elif detected["status"] == "empty":
            record["errors"].append({"code": "EMPTY_FILE", "description": f"{path.name} is empty"})
        return record

    try:
        if detected["format"] == "FBX":
            info = inspect_fbx(path)
        elif detected["format"] in ("GLB", "GLTF"):
            asset = load_document(path)
            inspected = inspect_document(asset, path.name)
            from processing.motion import features_from_sampler
            from processing.samplers import GltfSampler
            gltf_features = None
            if asset["json"].get("animations"):
                try:
                    sampler = GltfSampler(asset, 0)
                    gltf_features = features_from_sampler(sampler, _gltf_frame_rate(asset))
                except (KeyError, IndexError, ValueError):
                    gltf_features = None
            info = {"_features": gltf_features,
                    "format": detected["format"], "clips": inspected["animations"]["clips"],
                    "clip_count": inspected["animations"]["clip_count"],
                    "skeleton": inspected["skeleton"], "issues": inspected["issues"],
                    "frame_rate": _gltf_frame_rate(asset),
                    "up_axis": "Y", "linear_unit": "metres"}
        elif detected["format"] == "BVH":
            from processing.bvh import load_bvh, skeleton_summary
            from processing.motion import features_from_sampler
            from processing.samplers import BVHSampler
            bvh = load_bvh(path)
            bvh_sampler = BVHSampler(bvh)
            summary = skeleton_summary(bvh)
            info = {"format": "BVH", "clip_count": 1,
                    "clips": [{"name": path.stem, "index": 0,
                               "duration_seconds": round(bvh.duration, 6),
                               "frame_rate": round(bvh.fps, 4), "frame_start": 0,
                               "frame_end": bvh.frame_count,
                               "channel_count": len(bvh.order) * 3,
                               "root_motion": any("position" in ch.lower()
                                                  for joint in bvh.joints.values()
                                                  for ch in joint.channels)}],
                    "skeleton": summary, "issues": [], "frame_rate": bvh.fps,
                    "up_axis": "Y", "linear_unit": "metres",
                    "_sampler": (bvh_sampler, features_from_sampler(bvh_sampler, bvh.fps))}
        else:
            raise LibraryError(f"{detected['format']} inspection is not implemented")
    except (FBXError, GLBError, LibraryError, ValueError, KeyError, OSError) as exc:
        record["status"] = "corrupted"
        record["errors"].append({"code": "PARSE_FAILED",
                                 "description": f"{path.name}: {exc}"})
        return record

    # Internal helpers (samplers, cached features) never reach the published inventory.
    internal = {k: info.pop(k, None) for k in ("_sampler", "_features")}
    record["inspection"] = {k: v for k, v in info.items() if k not in ("clips", "skeleton")}
    skeleton = info.get("skeleton") or {}
    bones = skeleton.get("bones") or []
    record["skeleton"] = {
        "present": bool(skeleton.get("present") and bones),
        "bone_count": skeleton.get("bone_count") or len(bones),
        "skeleton_id": skeleton_id(bones) if bones else None,
        "root_bone": skeleton.get("root_bone"),
        "fingerprint": skeleton_fingerprint(bones) if bones else None,
        "has_skin_deformer": skeleton.get("has_skin_deformer", False),
    }
    record["_bones"] = bones  # kept out of the published inventory; used for grouping
    extra_features = internal.get("_features")
    record["clips"] = info.get("clips") or []
    record["clip_count"] = len(record["clips"])
    record["root_motion"] = any(bool(clip.get("root_motion")) for clip in record["clips"])
    for issue in info.get("issues") or []:
        target = "errors" if issue.get("severity") == "ERROR" else "warnings"
        record[target].append({"code": issue.get("code", ""),
                               "description": issue.get("description", "")})

    if record["clip_count"] == 0:
        record["warnings"].append({
            "code": "NO_ANIMATION_CLIPS",
            "description": f"{path.name} contains a skeleton but no animation clips",
        })

    primary = _primary_clip(record["clips"])
    if primary and classify_clips:
        features = primary.get("features") or extra_features
        sampler_pair = internal.get("_sampler")
        if features is None and sampler_pair:
            features = sampler_pair[1]
        if features is None and detected["format"] == "FBX":
            from processing.fbx import load_fbx
            from processing.motion import motion_features
            doc = load_fbx(path)
            features = motion_features(doc, doc.stacks[int(primary.get("index", 0))])
            primary["features"] = features
        if features:
            result = classify(features, path.stem)
            record["classification"] = _classification_dict(result, primary)
            for warning in result.warnings:
                record["warnings"].append({"code": warning["code"],
                                           "description": warning["description"]})
            record["status"] = "ok"
        else:
            record["warnings"].append({
                "code": "NO_MOTION_FEATURES",
                "description": f"{path.name}: clip '{primary.get('name')}' has no measurable motion",
            })
    if record["status"] == "ok" and record["clip_count"] > 1:
        record["warnings"].append({
            "code": "MULTI_CLIP_SOURCE",
            "description": f"{path.name} contains {record['clip_count']} clips; the library "
                           "classifies the longest clip and keeps the file intact",
        })
    return record


def _primary_clip(clips: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Longest clip (ties: first by index) — used for classification."""
    if not clips:
        return None
    return max(clips, key=lambda clip: (float(clip.get("duration_seconds") or 0.0),
                                        -int(clip.get("index") or 0)))


def _classification_dict(result: Classification, clip: dict[str, Any]) -> dict[str, Any]:
    payload = result.as_dict()
    payload["clip_name"] = clip.get("name")
    payload["clip_duration_seconds"] = clip.get("duration_seconds")
    return payload


def _gltf_frame_rate(asset: dict[str, Any]) -> float:
    """Estimate the sample rate of the first glTF animation from its input accessor."""
    try:
        from processing.glb import read_accessor
        doc, binary = asset["json"], asset["bin"]
        for animation in doc.get("animations", []):
            for sampler in animation.get("samplers", []):
                times = read_accessor(doc, binary, sampler["input"])
                if len(times) > 1:
                    delta = times[1] - times[0]
                    if delta > 0:
                        return round(1.0 / delta, 4)
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    return 0.0


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def discover(root: Path | None = None, *, include_archive: bool = False) -> list[Path]:
    """Recursively find candidate files under the animation root.

    The ``Upload/`` archive is skipped by default because it is the immutable
    source copy of files that are organised elsewhere; processing both would
    duplicate every asset. ``include_archive=True`` includes it.
    """
    base = Path(root) if root is not None else ROOT / "assets" / "animation"
    if not base.is_dir():
        return []
    found = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if not include_archive and SOURCE_ARCHIVE in path.relative_to(base).parts:
            continue
        if path.name.startswith("."):
            continue
        found.append(path)
    return found


def build_inventory(root: Path | None = None, *, include_archive: bool = False,
                    classify_clips: bool = True) -> dict[str, Any]:
    """Scan a directory tree and produce the full inventory."""
    base = Path(root) if root is not None else ROOT / "assets" / "animation"
    paths = discover(base, include_archive=include_archive)
    assets: list[dict[str, Any]] = []
    bones_by_group: dict[str, list[dict[str, Any]]] = {}
    for path in paths:
        record = inspect_asset(path, classify_clips=classify_clips)
        record["asset_id"] = asset_id("animation", record["source_path"], record["sha256"])
        fingerprint = record["skeleton"].get("fingerprint")
        if fingerprint and fingerprint not in bones_by_group:
            bones_by_group[fingerprint] = record.pop("_bones", [])
        else:
            record.pop("_bones", None)
        assets.append(record)

    # Content duplicates: identical bytes under more than one path.
    by_hash: dict[str, list[str]] = {}
    for record in assets:
        by_hash.setdefault(record["sha256"], []).append(record["source_path"])
    duplicates = {digest: sorted(paths_) for digest, paths_ in by_hash.items() if len(paths_) > 1}
    for record in assets:
        group = duplicates.get(record["sha256"])
        if not group:
            continue
        # Exactly one member of the group (the first by path) stays canonical so
        # the content is organised once; the rest point at it. Marking every
        # member as a duplicate would skip all of them and drop the asset.
        # Prefer a member that is not in the read-only archive: the organised copy
        # in the category tree is canonical, the archive file is its duplicate.
        keeper = next((path for path in group if "/Upload/" not in path), group[0])
        if record["source_path"] == keeper:
            record["duplicate_group"] = group
            continue
        record["duplicate_of"] = keeper
        record["warnings"].append({
            "code": "DUPLICATE_CONTENT",
            "description": f"{record['source_path']} is byte-identical to {keeper}",
        })

    # Skeleton groups (rest pose + hierarchy, not bone count).
    groups: dict[str, dict[str, Any]] = {}
    for record in assets:
        fingerprint = record["skeleton"].get("fingerprint")
        if not fingerprint:
            continue
        group = groups.setdefault(fingerprint, {
            "fingerprint": fingerprint,
            "skeleton_id": record["skeleton"]["skeleton_id"],
            "bone_count": record["skeleton"]["bone_count"],
            "root_bone": record["skeleton"].get("root_bone"),
            "members": [],
        })  # bones are attached once, below
        group["members"].append(record["asset_id"])
    for fingerprint, group in groups.items():
        group["members"].sort()
        group["member_count"] = len(group["members"])
        group["bones"] = bones_by_group.get(fingerprint, [])

    categories: dict[str, list[str]] = {}
    for record in assets:
        classification = record.get("classification") or {}
        category = classification.get("category")
        if category:
            categories.setdefault(category, []).append(record["asset_id"])
    for value in categories.values():
        value.sort()

    formats: dict[str, int] = {}
    for record in assets:
        formats[record["detected_format"]] = formats.get(record["detected_format"], 0) + 1

    totals = {
        "files": len(assets),
        "supported": sum(1 for r in assets if r["format_supported"] and r["status"] == "ok"),
        "unsupported": sum(1 for r in assets if r["status"] == "unsupported"),
        "empty": sum(1 for r in assets if r["status"] == "empty"),
        "corrupted": sum(1 for r in assets if r["status"] == "corrupted"),
        "with_errors": sum(1 for r in assets if r["errors"]),
        "duplicate_files": sum(len(v) for v in duplicates.values()) - len(duplicates),
        "duplicate_groups": len(duplicates),
        "clips": sum(r["clip_count"] for r in assets),
        "root_motion_clips": sum(1 for r in assets for c in r["clips"] if c.get("root_motion")),
        "by_format": dict(sorted(formats.items())),
        "by_category": {key: len(value) for key, value in sorted(categories.items())},
        "skeleton_groups": len(groups),
    }
    return {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": _relative(base),
        "archive_included": include_archive,
        "totals": totals,
        "assets": assets,
        "duplicate_groups": [{"sha256": digest, "paths": paths_} for digest, paths_ in
                             sorted(duplicates.items())],
        "skeleton_groups": sorted(groups.values(), key=lambda g: g["skeleton_id"] or ""),
        "categories": dict(sorted(categories.items())),
    }


# --------------------------------------------------------------------------- #
# Skeleton compatibility across groups and targets
# --------------------------------------------------------------------------- #
def compatibility_report(inventory: dict[str, Any],
                         model_manifests: Iterable[dict[str, Any]] | None = None,
                         manual_map: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Compare each source skeleton group with every rigged target character."""
    if model_manifests is None:
        from processing.common import path_for, read_json
        directory = path_for("manifests", "models")
        model_manifests = ([read_json(p) for p in sorted(directory.glob("*.json"))]
                           if directory.is_dir() else [])
    records: list[dict[str, Any]] = []
    for group in inventory.get("skeleton_groups", []):
        source_bones = group.get("bones") or []
        for model in model_manifests:
            skeleton = model.get("skeleton") or {}
            target_bones = skeleton.get("bones") or []
            if not skeleton.get("present") or not target_bones:
                continue
            if source_bones:
                verdict = check_compatibility(source_bones, target_bones, manual_map)
            else:
                verdict = {"status": "unknown", "score": 0.0,
                           "reasons": ["source rest-pose bone data unavailable"]}
            records.append({
                "skeleton_id": group["skeleton_id"],
                "target_character_id": model.get("id"),
                "target_name": model.get("name", ""),
                "target_bone_count": len(target_bones),
                "source_bone_count": group["bone_count"],
                "status": verdict["status"],
                "role_coverage": verdict.get("role_coverage", 0.0),
                "mapped_bones": verdict.get("mapped_bones", 0),
                "manual_map_applied": bool(manual_map),
                "reasons": verdict.get("reasons", []),
                "retargeting_required": bool(
                    verdict["status"] != "compatible"
                    or {b.get("name") for b in source_bones} != {b.get("name") for b in target_bones}),
            })
    return records


# --------------------------------------------------------------------------- #
# Organisation
# --------------------------------------------------------------------------- #
def safe_file_name(name: str) -> str:
    """Keep the original name; neutralise path separators, NUL bytes and dot names.

    Unicode, spaces and punctuation are preserved (the pipeline has to keep the
    name traceable), but a value that could act as a path component is never
    returned: separators become ``_`` and ``.``/``..`` fall back to ``asset``.
    """
    cleaned = name.replace("\x00", "").replace("/", "_").replace("\\", "_").strip()
    if cleaned in ("", ".", ".."):
        return "asset"
    return cleaned


def plan_organization(inventory: dict[str, Any], *, target_root: Path | None = None,
                      skip_unclassified: bool = False) -> dict[str, Any]:
    """Build a deterministic copy plan (no filesystem changes)."""
    base = Path(target_root) if target_root is not None else ROOT / "assets" / "animation"
    used: dict[str, str] = {}
    operations: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for record in sorted(inventory["assets"], key=lambda r: r["source_path"]):
        classification = record.get("classification") or {}
        category = classification.get("category")
        if record["status"] != "ok" or not category:
            reason_code = record["status"] if record["status"] != "ok" else "no_category"
            skipped.append({"source_path": record["source_path"], "reason": reason_code,
                            "detail": "; ".join(e["description"] for e in record["errors"])})
            continue
        if skip_unclassified and category_root(category) == "unclassified":
            skipped.append({"source_path": record["source_path"], "reason": "unclassified"})
            continue
        if record.get("duplicate_of"):
            skipped.append({"source_path": record["source_path"], "reason": "duplicate_content",
                            "detail": f"identical to {record['duplicate_of']}"})
            continue
        name = safe_file_name(record["file_name"])
        destination = f"{category}/{name}"
        if destination in used and used[destination] != record["source_path"]:
            # Deterministic collision suffix from the content hash.
            stem = Path(name).stem
            suffix = Path(name).suffix
            destination = f"{category}/{stem}__{record['sha256'][:8]}{suffix}"
            counter = 2
            while destination in used:
                destination = f"{category}/{stem}__{record['sha256'][:8]}_{counter}{suffix}"
                counter += 1
        if Path(destination).is_absolute() or ".." in Path(destination).parts:
            skipped.append({"source_path": record["source_path"], "reason": "unsafe_path",
                            "detail": destination})
            continue
        resolved_destination = normalize_path(str(Path(_relative(base)) / destination))
        if resolved_destination == record["source_path"]:
            # A source that already lives at its canonical destination is not a
            # copy candidate; planning it would be a no-op write onto itself.
            skipped.append({"source_path": record["source_path"], "reason": "already_in_place",
                            "detail": destination})
            continue
        used[destination] = record["source_path"]
        operations.append({
            "asset_id": record["asset_id"],
            "source_path": record["source_path"],
            "destination_path": normalize_path(str(Path(_relative(base)) / destination)),
            "relative_destination": destination,
            "sha256": record["sha256"],
            "size_bytes": record["size_bytes"],
            "category": category,
        })
    return {"operations": operations, "skipped": skipped,
            "target_root": _relative(base),
            "counts": {"copy": len(operations), "skipped": len(skipped)}}


def apply_organization(plan: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    """Execute a copy plan with hash verification; originals are never touched.

    * an existing destination with identical bytes is left alone (idempotent);
    * an existing destination with different bytes is an error, never an overwrite;
    * every copy is verified byte-for-byte by SHA-256 afterwards;
    * the source hash is re-checked to prove the original was not modified.
    """
    results: list[dict[str, Any]] = []
    failures = 0
    for operation in plan["operations"]:
        source = ROOT / operation["source_path"]
        destination = ROOT / operation["destination_path"]
        entry = {"asset_id": operation["asset_id"], "source": operation["source_path"],
                 "destination": operation["destination_path"], "action": "", "verified": False}
        try:
            if not source.is_file():
                raise LibraryError("source file disappeared")
            source_hash = sha256_file(source)
            if source_hash != operation["sha256"]:
                raise LibraryError("source bytes changed since the scan")
            if destination.exists():
                if sha256_file(destination) == source_hash:
                    entry["action"] = "already_organized"
                    entry["verified"] = True
                else:
                    raise LibraryError("destination exists with different content; refusing to overwrite")
            else:
                if dry_run:
                    entry["action"] = "would_copy"
                    entry["verified"] = True
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                    if sha256_file(destination) != source_hash:
                        raise LibraryError("copy verification failed (hash mismatch)")
                    if sha256_file(source) != source_hash:
                        raise LibraryError("source hash changed during the copy")
                    entry["action"] = "copied"
                    entry["verified"] = True
        except (LibraryError, OSError) as exc:
            entry["action"] = "failed"
            entry["error"] = str(exc)
            failures += 1
        results.append(entry)
    return {"dry_run": dry_run, "results": results, "failures": failures,
            "copied": sum(1 for r in results if r["action"] == "copied"),
            "unchanged": sum(1 for r in results if r["action"] == "already_organized"),
            "planned": sum(1 for r in results if r["action"] == "would_copy")}


def mapping_document(inventory: dict[str, Any], plan: dict[str, Any],
                     applied: dict[str, Any] | None = None) -> dict[str, Any]:
    """Source→destination mapping kept alongside the library (committed)."""
    executed = {r["asset_id"]: r for r in (applied or {}).get("results", [])}
    assets = []
    for operation in plan["operations"]:
        outcome = executed.get(operation["asset_id"], {})
        assets.append({**operation,
                       "action": outcome.get("action", "planned"),
                       "verified": outcome.get("verified", False),
                       "error": outcome.get("error", "")})
    return {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_archive": f"{inventory['root']}/{SOURCE_ARCHIVE}",
        "target_root": plan["target_root"],
        "policy": {
            "originals": "never modified, moved or deleted",
            "copy": "copy2 + SHA-256 verification of both sides",
            "overwrite": "refused when the destination bytes differ",
            "duplicates": "byte-identical sources are recorded, not copied twice",
        },
        "counts": plan["counts"],
        "assets": assets,
        "skipped": plan["skipped"],
    }


def save_json(path: Path, value: Any) -> None:
    from processing.common import write_json
    write_json(path, value)


def tracked_library_documents(inventory: dict[str, Any], mapping: dict[str, Any],
                              *, compact: bool = True) -> dict[str, str]:
    """Human-reviewable, committed copies of the library artefacts.

    ``reports/`` is generated output and never committed, but the mapping from an
    original upload to its organised destination and the SHA-256 of every original
    must be reviewable in the pull request and checkable in CI, so compact copies
    are written under ``docs/animation-library/``.
    """
    import json

    def dumps_json(value: Any) -> str:
        return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    assets = []
    for record in inventory["assets"]:
        classification = record.get("classification") or {}
        assets.append({
            "asset_id": record["asset_id"],
            "original_filename": record["file_name"],
            "source_path": record["source_path"],
            "sha256": record["sha256"],
            "bytes": record["size_bytes"],
            "format": record["detected_format"],
            "status": record["status"],
            "clips": record["clip_count"],
            "duration_seconds": round(sum(float(c.get("duration_seconds") or 0)
                                          for c in record.get("clips") or []), 4),
            "skeleton": {"bone_count": (record.get("skeleton") or {}).get("bone_count", 0),
                         "skeleton_id": (record.get("skeleton") or {}).get("skeleton_id")},
            "category": classification.get("category"),
            "action": classification.get("action"),
            "confidence": classification.get("confidence"),
            "root_motion": record.get("root_motion"),
            "warnings": sorted({w["code"] for w in record.get("warnings") or []}),
            "errors": sorted({e["code"] for e in record.get("errors") or []}),
        })
    inventory_doc = {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": inventory["root"],
        "totals": inventory["totals"],
        "skeleton_groups": inventory["skeleton_groups"],
        "assets": assets,
    }
    checksum_lines = [f"{record['sha256']}  {record['source_path']}"
                      for record in inventory["assets"]]
    documents = {
        "inventory.json": dumps_json(inventory_doc),
        "organization-mapping.json": dumps_json(mapping),
        "sha256sums.txt": "\n".join(sorted(set(checksum_lines))) + "\n",
    }
    return documents
