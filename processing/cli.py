"""Command-line pipeline: model processing, animation processing, validation, indexing."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from processing.common import (ROOT, SUPPORTED_ANIMATIONS, SUPPORTED_MODELS, asset_id,
                               category_of, load_config, log, path_for, sha256_bytes,
                               sha256_file, validate_source)
from processing.classify import classify
from processing.compatibility import check_compatibility, load_manual_map
from processing.glb import GLBError, inspect_document, load_document, write_glb
from processing.manifest import build_manifest
from processing.publish import build_index, publish_asset, record_job
from processing.report import build_report, to_markdown
from processing.validate import validate_glb, validator_available
from processing import __version__


def _discover(kind: str) -> list[Path]:
    root = path_for("assets")
    base = root / ("models" if kind == "model" else "animation")
    allowed = SUPPORTED_MODELS if kind == "model" else SUPPORTED_ANIMATIONS
    if not base.is_dir():
        return []
    # assets/animation/Upload is the read-only archive of the raw uploads: the
    # organizer copies every supported file into its category folder, and those
    # copies are the canonical sources for processing. Scanning the archive as
    # well would publish the same bytes twice under two different asset ids.
    archive = base / "Upload"
    mapping = _organization_mapping()
    # The organizer's plan names one canonical destination per content hash. That
    # set - not "every file that looks like an animation" - is what gets processed:
    # sources that were re-homed are skipped in favour of their copy, byte-identical
    # twins are skipped in favour of the canonical one, and a file the organiser has
    # never seen (freshly dropped in) is still processed so nothing goes silent.
    # The plan itself is generated into ``reports/`` (git-ignored), so on a fresh
    # checkout - CI, a clean clone - there is no plan at all and the loop below would
    # select every path. ``_dedupe_by_content()`` enforces the one-source-per-hash
    # invariant independently of the plan.
    canonical = {asset["destination_path"] for asset in mapping.get("assets", [])
                 if asset.get("destination_path") and (ROOT / asset["destination_path"]).is_file()}
    # A source the planner left in place *is* its own canonical copy.
    canonical |= {entry["source_path"] for entry in mapping.get("skipped", [])
                  if entry.get("reason") == "already_in_place"
                  and (ROOT / entry["source_path"]).is_file()}
    mapped_sources = {asset["source_path"] for asset in mapping.get("assets", [])}
    duplicate_sources = {entry["source_path"] for entry in mapping.get("skipped", [])
                         if entry.get("reason") == "duplicate_content"}
    rehomed = (mapped_sources | duplicate_sources) - canonical
    selected = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in allowed:
            continue
        if base.name == "animation" and _is_relative_to(path, archive):
            continue
        try:
            relative = str(path.relative_to(ROOT))
        except ValueError:
            relative = str(path)
        if relative in canonical:
            selected.append(path)  # the canonical copy for this content hash
            continue
        if relative in rehomed:
            continue  # re-homed or byte-identical to a canonical copy
        selected.append(path)
    return _dedupe_by_content(selected, canonical)


def _relative_name(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _dedupe_by_content(paths: list[Path], canonical: set[str]) -> list[Path]:
    """Keep exactly one source per content hash, preferring the organized copy.

    ``asset_id()`` mixes the source path into the id, so two byte-identical paths
    would be published twice under two different asset ids. That must not depend on
    the organizer's plan existing, because the plan lives in the git-ignored
    ``reports/`` tree: without this pass a clean checkout would process the archive
    copies and their twins.
    """
    ordered = sorted(paths, key=lambda path: (0 if _relative_name(path) in canonical else 1,
                                              str(path)))
    kept: dict[str, Path] = {}
    for path in ordered:
        try:
            key = sha256_file(path)
        except OSError:
            key = f"unreadable:{path}"
        kept.setdefault(key, path)
    duplicates = len(ordered) - len(kept)
    if duplicates:
        log("DISCOVER", f"skipped {duplicates} byte-identical source(s) already covered "
                        f"by another path")
    return sorted(kept.values())


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _blender_available() -> bool:
    return shutil.which("blender") is not None


def _run_blender(script: str, *params: str) -> dict[str, Any]:
    env = dict(os.environ, PYTHONPATH=str(ROOT), BLENDER_DISABLE_AUTOEXEC="1")
    cmd = ["blender", "--background", "--factory-startup", "--python",
           str(ROOT / "processing" / "blender" / script), "--", *params]
    timeout = int(load_config()["limits"]["processing_timeout_seconds"])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"Blender failed ({proc.returncode}): {(proc.stderr or '')[-800:]}")
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise RuntimeError(f"Blender returned no JSON: {proc.stdout[-300:]}") from exc


def _pass_checks(kind: str, metadata: dict[str, Any], source_format: str) -> list[dict[str, Any]]:
    g = metadata.get("geometry") or {}
    checks = [
        {"severity": "PASS", "code": "FORMAT_DETECTED", "description": f"Source format {source_format} recognised",
         "asset": "", "location": "source", "suggested_fix": ""},
        {"severity": "PASS", "code": "INTEGRITY_CHECKED", "description": "Source bytes hashed and size-checked",
         "asset": "", "location": "source", "suggested_fix": ""},
        {"severity": "PASS", "code": "GEOMETRY_INSPECTED",
         "description": f"{g.get('mesh_count', 0)} mesh(es), {g.get('vertex_count', 0)} vertices, {g.get('triangle_count', 0)} triangles",
         "asset": "", "location": "glTF.meshes", "suggested_fix": ""},
        {"severity": "PASS", "code": "MATERIALS_INSPECTED",
         "description": f"{g.get('material_count', 0)} material(s), {g.get('texture_count', 0)} texture(s) inspected",
         "asset": "", "location": "glTF.materials", "suggested_fix": ""},
    ]
    sk = metadata.get("skeleton") or {"present": False, "bone_count": 0, "skeleton_count": 0}
    checks.append({"severity": "PASS",
                   "code": "SKELETON_INSPECTED" if sk.get("present") else "STATIC_MODEL_NO_SKELETON",
                   "description": (f"{sk.get('bone_count', 0)} bones, {sk.get('skeleton_count', 0)} skin(s) inspected"
                                   if sk.get("present") else "Static model: no skeleton present (none fabricated)"),
                   "asset": "", "location": "glTF.skins", "suggested_fix": ""})
    anims = metadata.get("animations") or {"clip_count": 0}
    if anims.get("clip_count"):
        checks.append({"severity": "PASS", "code": "ANIMATIONS_DETECTED",
                       "description": f"{anims['clip_count']} embedded animation clip(s) detected",
                       "asset": "", "location": "glTF.animations", "suggested_fix": ""})
    return checks


def _to_runtime_glb(path: Path, kind: str) -> tuple[bytes | None, str]:
    """Return a self-contained GLB processed copy; originals are never modified."""
    suffix = path.suffix.lower()
    if suffix == ".glb":
        return path.read_bytes(), "byte-identical copy of source GLB"
    if suffix == ".gltf":
        asset = load_document(path)
        return write_glb(asset["json"], asset["bin"]), "external .gltf buffers packed into GLB"
    if _blender_available():
        tmp = path_for("processed") / "_blender" / f"{path.stem}.glb"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        _run_blender("convert_model.py", str(path), str(tmp))
        return tmp.read_bytes(), f"converted from {suffix} by headless Blender"
    return None, f"conversion from {suffix} requires headless Blender"


def process_model(path: Path) -> dict[str, Any]:
    cfg = load_config()
    rel = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
    validate_source(path, "model")
    source_hash = sha256_file(path)
    aid = asset_id("model", rel, source_hash)
    checks: list[dict[str, Any]] = []
    glb: bytes | None = None
    note = ""
    try:
        glb, note = _to_runtime_glb(path, "model")
    except (GLBError, RuntimeError, ValueError) as exc:
        checks.append({"severity": "ERROR", "code": "CONVERSION_FAILED", "description": str(exc),
                       "asset": rel, "location": "conversion", "suggested_fix": "Install Blender or fix the source file."})
    if glb is None and not any(c["severity"] == "ERROR" for c in checks):
        checks.append({"severity": "WARNING", "code": "BLENDER_UNAVAILABLE",
                       "description": note or "No GLB runtime copy produced",
                       "asset": rel, "location": "conversion",
                       "suggested_fix": "Install Blender 4.2 LTS (or use the asset-processing workflow)."})

    metadata: dict[str, Any] = {"geometry": {}, "materials": [], "textures": [],
                                "skeleton": {"present": False, "bones": [], "skins": []},
                                "animations": {"clip_count": 0, "clips": []}, "issues": []}
    if glb is not None:
        try:
            metadata = inspect_document(load_document_from_bytes(glb), path.name)
            for issue in metadata["issues"]:
                checks.append({**issue, "asset": path.name, "component": "glTF"})
        except (GLBError, KeyError, ValueError, json.JSONDecodeError) as exc:
            checks.append({"severity": "ERROR", "code": "GLB_PARSE_FAILED", "description": str(exc),
                           "asset": path.name, "location": "glTF", "suggested_fix": "Re-export a valid GLB."})
            glb = None
    checks.extend(_pass_checks("model", metadata, path.suffix.upper().lstrip(".")))
    if glb is not None:
        processed_path = path_for("processed", "models", aid, f"{aid}.glb")
        processed_path.parent.mkdir(parents=True, exist_ok=True)
        processed_path.write_bytes(glb)
        for c in validate_glb(processed_path):
            checks.append(c)
    else:
        processed_path = None

    report = build_report(aid, "model", rel, checks,
                          context={"processing_version": cfg["studio"]["processing_version"],
                                   "runtime_format": "GLB", "conversion": note})
    for c in report["checks"]:
        if c["asset"] == "":
            c["asset"] = path.name
    manifest = build_manifest(
        asset_id=aid, kind="model", name=path.stem, category=category_of(rel),
        source_path=rel, source_hash=source_hash, source_format=path.suffix.upper().lstrip("."),
        processed_path=str(processed_path.relative_to(ROOT)) if processed_path else None,
        processed_hash=sha256_bytes(glb) if glb else None, processed_bytes=len(glb) if glb else 0,
        processing_version=cfg["studio"]["processing_version"],
        validation_status=report["status"], report=f"reports/models/{aid}.json",
        metadata=metadata, download_reference=f"assets/{aid}/download",
    )
    paths = publish_asset(aid, "model", glb, metadata, report, to_markdown(report), manifest)
    log("MODEL", f"{aid}: {report['status']} ({report['summary']['WARNING']}W/{report['summary']['ERROR']}E) -> {paths['manifest']}")
    return {"asset_id": aid, "status": report["status"], "processed": str(processed_path) if processed_path else None}


def load_document_from_bytes(data: bytes) -> dict[str, Any]:
    from processing.glb import parse_glb
    return parse_glb(data)


def _model_manifests() -> list[dict[str, Any]]:
    from processing.common import read_json
    directory = path_for("manifests", "models")
    if not directory.is_dir():
        return []
    return [read_json(p) for p in sorted(directory.glob("*.json"))]


_MAPPING_CACHE: dict[str, Any] | None = None


def _organization_mapping() -> dict[str, Any]:
    """Source -> destination mapping written by the ``organize`` command (if any)."""
    global _MAPPING_CACHE
    if _MAPPING_CACHE is None:
        from processing.common import read_json
        path = path_for("reports", "animations", "library", "organization-mapping.json")
        _MAPPING_CACHE = read_json(path) if path.is_file() else {}
    return _MAPPING_CACHE


def _organized_path_for(relative_source: str) -> str | None:
    document = _organization_mapping()
    for asset in document.get("assets", []):
        if asset.get("source_path") == relative_source:
            return asset.get("destination_path")
    return None


def _skeleton_id(bones: list[dict[str, Any]]) -> str:
    from processing.library import skeleton_id as compute
    try:
        return compute(bones)
    except (KeyError, TypeError, ValueError):
        return ""


def library_inventory(*, include_archive: bool = True) -> dict[str, Any]:
    """Scan the animation library, classify it and write the inventory reports."""
    from processing.library import (build_inventory, compatibility_report, mapping_document,
                                    plan_organization, save_json)
    inventory = build_inventory(include_archive=include_archive)
    compatibility = compatibility_report(inventory)
    plan = plan_organization(inventory)
    save_json(path_for("reports", "animations", "library", "inventory.json"), inventory)
    save_json(path_for("reports", "animations", "library", "skeleton-compatibility.json"),
              compatibility)
    save_json(path_for("reports", "animations", "library", "organization-plan.json"),
              {**plan, "mapping": mapping_document(inventory, plan)})
    log("LIBRARY", f"{inventory['totals']['files']} file(s), "
                   f"{len(inventory['skeleton_groups'])} skeleton group(s), "
                   f"{inventory['totals']['duplicate_groups']} duplicate group(s)")
    return {"totals": inventory["totals"], "skeleton_groups": len(inventory["skeleton_groups"]),
            "planned_copies": plan["counts"]["copy"], "skipped": plan["counts"]["skipped"],
            "report": "reports/animations/library/inventory.json"}


def organize_animations(*, apply: bool = False, skip_unclassified: bool = False) -> dict[str, Any]:
    """Copy classified animation sources into the category tree (never moves/deletes)."""
    from processing.library import (apply_organization, build_inventory, mapping_document,
                                    plan_organization, save_json)
    inventory = build_inventory(include_archive=True)
    plan = plan_organization(inventory, skip_unclassified=skip_unclassified)
    applied = apply_organization(plan, dry_run=not apply)
    document = mapping_document(inventory, plan, applied)
    save_json(path_for("reports", "animations", "library", "organization-mapping.json"), document)
    save_json(path_for("reports", "animations", "library", "organization-plan.json"),
              {**plan, "applied": applied})
    from processing.library import tracked_library_documents
    tracked = ROOT / "docs" / "animation-library"
    tracked.mkdir(parents=True, exist_ok=True)
    for name, content in tracked_library_documents(inventory, document).items():
        (tracked / name).write_text(content, encoding="utf-8")
    log("ORGANIZE", f"{applied['copied']} copied, {applied['unchanged']} already organized, "
                    f"{len(plan['skipped'])} skipped, {applied['failures']} failure(s)")
    return {"applied": apply, "copied": applied["copied"], "unchanged": applied["unchanged"],
            "planned": applied["planned"], "failures": applied["failures"],
            "skipped": len(plan["skipped"]),
            "mapping": "docs/animation-library/organization-mapping.json"}


def process_animation(path: Path) -> dict[str, Any]:
    cfg = load_config()
    rel = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
    validate_source(path, "animation")
    source_hash = sha256_file(path)
    aid = asset_id("animation", rel, source_hash)
    checks: list[dict[str, Any]] = []
    suffix = path.suffix.lower()
    source_bones: list[dict[str, Any]] = []
    clips: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}
    glb: bytes | None = None
    retarget_status = "not_required"
    target_id = ""
    compatibility: list[dict[str, Any]] = []
    reason = ""

    bvh = None
    runtime_bytes: bytes | None = None
    if suffix == ".fbx":
        from processing.fbx import FBXError, inspect_fbx
        try:
            fbx_info = inspect_fbx(path)
        except (FBXError, ValueError, OSError) as exc:
            checks.append({"severity": "ERROR", "code": "FBX_INSPECTION_FAILED",
                           "description": str(exc), "asset": path.name, "location": "FBX",
                           "suggested_fix": "Re-export the FBX from the DCC tool, or inspect it with Blender."})
            metadata = {"animations": {"clip_count": 0, "clips": []}, "issues": []}
        else:
            source_bones = fbx_info["skeleton"]["bones"]
            clips = fbx_info["clips"]
            metadata = {"animations": {"clip_count": len(clips), "clips": clips},
                        "skeleton": {**fbx_info["skeleton"], "present": bool(source_bones),
                                     "skeleton_count": 1 if source_bones else 0},
                        "source_skeleton": fbx_info["skeleton"],
                        "up_axis": fbx_info["up_axis"], "linear_unit": fbx_info["linear_unit"],
                        "fbx_version": fbx_info["version"], "issues": fbx_info["issues"]}
            checks.append({"severity": "PASS", "code": "FBX_INSPECTED",
                           "description": (f"FBX {fbx_info['version']}, {len(source_bones)} bones, "
                                           f"{len(clips)} clip(s), {fbx_info['up_axis']}-up, "
                                           f"{fbx_info['linear_unit']}"),
                           "asset": path.name, "location": "FBX", "suggested_fix": ""})
            for clip in clips:
                if clip.get("frame_rate") and clip["frame_rate"] > 0:
                    checks.append({"severity": "PASS", "code": "CLIP_MEASURED",
                                   "description": (f"clip '{clip['name']}': {clip['duration_seconds']} s, "
                                                   f"{clip['frame_rate']} fps, {clip['key_count']} keys, "
                                                   f"{clip['channel_count']} channels, "
                                                   f"{len(clip['animated_bones'])} animated bones, "
                                                   f"root motion {clip['root_motion']}"),
                                   "asset": path.name, "location": "FBX.Takes",
                                   "suggested_fix": ""})
            for issue in fbx_info["issues"]:
                checks.append({**issue, "asset": path.name, "component": "FBX"})
    elif suffix == ".bvh":
        from processing.bvh import load_bvh, skeleton_summary
        bvh = load_bvh(path)
        summary = skeleton_summary(bvh)
        source_bones = summary["bones"]
        clips = [{"name": path.stem, "index": 0, "duration_seconds": round(bvh.duration, 6),
                  "frame_rate": round(bvh.fps, 4), "frame_start": 0,
                  "frame_end": round(bvh.duration, 6), "channel_count": len(bvh.order) * 3,
                  "animated_bones": bvh.order, "root_motion": any(
                      ch.lower().endswith("position") for j in bvh.joints.values() for ch in j.channels),
                  "interpolation": "LINEAR"}]
        metadata = {"animations": {"clip_count": 1, "clips": clips},
                    "source_skeleton": summary, "issues": []}
        checks.append({"severity": "PASS", "code": "BVH_PARSED",
                       "description": f"{len(bvh.order)} bones, {bvh.frame_count} frames, {bvh.fps:.2f} fps",
                       "asset": path.name, "location": "BVH.MOTION", "suggested_fix": ""})
    elif suffix in (".glb", ".gltf"):
        runtime, note = _to_runtime_glb(path, "animation")
        if runtime is None:
            checks.append({"severity": "WARNING", "code": "BLENDER_UNAVAILABLE", "description": note,
                           "asset": path.name, "location": "conversion", "suggested_fix": "Install Blender 4.2 LTS."})
        else:
            runtime_bytes = runtime
            doc = inspect_document(load_document_from_bytes(runtime), path.name)
            source_bones = doc["skeleton"]["bones"]
            clips = doc["animations"]["clips"]
            metadata = {"animations": doc["animations"], "source_skeleton": doc["skeleton"],
                        "issues": doc["issues"]}
            for issue in metadata["issues"]:
                checks.append({**issue, "asset": path.name, "component": "glTF"})
            checks.append({"severity": "PASS", "code": "GLTF_ANIMATIONS_INSPECTED",
                           "description": f"{len(clips)} embedded clip(s) inspected: {note}",
                           "asset": path.name, "location": "glTF.animations", "suggested_fix": ""})
            if not clips:
                checks.append({"severity": "WARNING", "code": "NO_ANIMATION_CLIPS",
                               "description": "Source glTF contains no animation clips",
                               "asset": path.name, "location": "glTF.animations",
                               "suggested_fix": "Use an asset with embedded actions."})
    else:
        note = f"{suffix} animation inspection requires headless Blender"
        if _blender_available():
            try:
                info = _run_blender("inspect_animation.py", str(path))
                clips = info.get("clips", [])
                source_bones = [{"name": b["name"], "parent": b["parent"], "translation": [0, 0, 0],
                                 "children": [], "rotation": [0, 0, 0, 1], "scale": [1, 1, 1],
                                 "global_matrix": [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]}
                                for b in info.get("bones", [])]
                metadata = {"animations": {"clip_count": len(clips), "clips": clips},
                            "source_skeleton": {"present": bool(source_bones), "bones": source_bones, "skins": []},
                            "issues": []}
                checks.append({"severity": "PASS", "code": "BLENDER_ANIMATION_INSPECTED",
                               "description": f"{len(clips)} action(s) found by Blender",
                               "asset": path.name, "location": "Blender.actions", "suggested_fix": ""})
            except (RuntimeError, ValueError) as exc:
                checks.append({"severity": "ERROR", "code": "BLENDER_INSPECTION_FAILED",
                               "description": str(exc), "asset": path.name, "location": "Blender",
                               "suggested_fix": "Check the animation file and Blender version."})
        else:
            checks.append({"severity": "WARNING", "code": "BLENDER_UNAVAILABLE", "description": note,
                           "asset": path.name, "location": "inspection",
                           "suggested_fix": "Install Blender 4.2 LTS or use the animation-processing workflow."})

    # Classification always comes from measured motion; names are only hints that
    # must be consistent with the measurements (see processing/classify.py).
    classification: dict[str, Any] | None = None
    features: dict[str, Any] | None = clips[0].get("features") if clips else None
    try:
        if features is None and bvh is not None:
            from processing.motion import features_from_sampler
            from processing.samplers import BVHSampler
            features = features_from_sampler(BVHSampler(bvh), bvh.fps)
        elif features is None and runtime_bytes and clips:
            from processing.motion import features_from_sampler
            from processing.samplers import GltfSampler
            features = features_from_sampler(GltfSampler(load_document_from_bytes(runtime_bytes)),
                                             clips[0].get("frame_rate") or 30.0)
    except (GLBError, KeyError, ValueError, IndexError):
        features = None
    if features:
        result = classify(features, path.stem)
        classification = result.as_dict()
        severity = "PASS" if str(result.category).split("/")[0] != "unclassified" else "WARNING"
        origin = ("filename" if any(w.get("code") in ("CLASSIFICATION_FROM_FILENAME",
                                                      "FILENAME_MEASUREMENT_MISMATCH")
                                    for w in result.warnings) else "measurement")
        checks.append({"severity": severity, "code": "CLASSIFIED",
                       "description": (f"{result.category} (action {result.action}, "
                                       f"{result.confidence:.2f} confidence, {origin}): "
                                       f"{'; '.join(result.evidence[:2])}"),
                       "asset": path.name, "location": "classification",
                       "suggested_fix": "" if severity == "PASS" else
                                        "Move the file manually or improve the clip."})
        for warning in classification.get("warnings", []):
            checks.append({"severity": "WARNING", "code": warning["code"],
                           "description": warning["description"], "asset": path.name,
                           "location": "classification", "suggested_fix": warning.get("fix", "")})
    else:
        checks.append({"severity": "WARNING", "code": "CLASSIFICATION_UNAVAILABLE",
                       "description": "No measurable motion data, so no category was assigned (nothing invented)",
                       "asset": path.name, "location": "classification",
                       "suggested_fix": "Provide a clip with keyed bones."})

    # Compatibility is checked against every real processed character skeleton.
    try:
        manual_map = load_manual_map((load_config().get("animation") or {}).get("manual_bone_map"))
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        manual_map = None
        checks.append({"severity": "ERROR", "code": "BONE_MAP_INVALID", "description": str(exc),
                       "asset": path.name, "location": "config",
                       "suggested_fix": "Fix config/bone_mappings/ or unset animation.manual_bone_map."})
    for model in _model_manifests():
        target_skeleton = model.get("skeleton") or {}
        target_bones = target_skeleton.get("bones", [])
        if not target_skeleton.get("present") or not target_bones:
            continue
        record = check_compatibility(source_bones, target_bones, manual_map)
        record.update({"target_character_id": model["id"], "target_name": model.get("name", ""),
                       "target_path": (model.get("processed") or {}).get("path")})
        compatibility.append(record)
        if record["status"] == "compatible" and not target_id:
            target_id = model["id"]
    if not compatibility:
        checks.append({"severity": "WARNING", "code": "NO_TARGET_CHARACTERS",
                       "description": "No processed rigged character skeleton is available for compatibility checking",
                       "asset": path.name, "location": "compatibility",
                       "suggested_fix": "Run process-models before process-animations."})
    elif not target_id:
        reasons = [r for rec in compatibility for r in rec.get("reasons", [])]
        reason = "; ".join(dict.fromkeys(reasons))[:600]
        checks.append({"severity": "WARNING", "code": "INCOMPATIBLE_SKELETON",
                       "description": f"No compatible target character: {reason}",
                       "asset": path.name, "location": "compatibility",
                       "suggested_fix": "Provide a manual bone mapping or use a humanoid target."})

    if target_id:
        target = next(m for m in compatibility if m["target_character_id"] == target_id)
        target_path = ROOT / target["target_path"]
        if suffix == ".bvh" and target_path.is_file():
            from processing.bvh import load_bvh
            from processing.retarget import retarget as do_retarget
            try:
                glb, rmeta = do_retarget(load_bvh(path), target_path, path.stem)
                retarget_status = "completed"
                metadata["retarget"] = rmeta
                checks.append({"severity": "PASS", "code": "RETARGET_COMPLETED",
                               "description": f"{rmeta['mapped_bones']} mapped bones, "
                                              f"{rmeta['channel_count']} channels, {rmeta['duration_seconds']} s",
                               "asset": path.name, "location": "retarget", "suggested_fix": ""})
            except ValueError as exc:
                retarget_status = "failed"
                reason = str(exc)
                checks.append({"severity": "ERROR", "code": "RETARGET_FAILED", "description": reason,
                               "asset": path.name, "location": "retarget",
                               "suggested_fix": "Provide a manual bone map in config/bone_mappings/."})
        elif suffix == ".fbx" and manual_map:
            from processing.fbx import load_fbx
            from processing.retarget import retarget_fbx, validate_retarget
            try:
                fbx_doc = load_fbx(path)
                glb, rmeta = retarget_fbx(fbx_doc, fbx_doc.stacks[0], target_path, path.stem,
                                          manual_map)
                rmeta = {**rmeta, "frame_rate": clips[0].get("frame_rate") if clips else 30.0}
                validation = validate_retarget(features or {}, glb, rmeta)
                rmeta["validation"] = validation
                metadata["retarget"] = rmeta
                checks.append({"severity": "PASS", "code": "RETARGET_COMPLETED",
                               "description": (f"{rmeta['mapped_bones']} mapped roles, "
                                               f"{rmeta['channel_count']} channels, "
                                               f"{rmeta['duration_seconds']} s, root motion "
                                               f"{rmeta['root_motion']}"),
                               "asset": path.name, "location": "retarget", "suggested_fix": ""})
                if validation["status"] == "validated":
                    retarget_status = "completed"
                    checks.append({"severity": "PASS", "code": "RETARGET_VALIDATED",
                                   "description": (f"{validation['passed']}/{validation['total']} behavioural "
                                                   f"checks passed; Khronos validation runs afterwards. "
                                                   f"{validation['note']}"),
                                   "asset": path.name, "location": "retarget",
                                   "suggested_fix": ""})
                else:
                    retarget_status = "failed"
                    glb = None
                    checks.append({"severity": "ERROR", "code": "RETARGET_VALIDATION_FAILED",
                                   "description": validation["reason"], "asset": path.name,
                                   "location": "retarget",
                                   "suggested_fix": "Inspect the retarget checks in the report before shipping."})
            except (ValueError, KeyError, IndexError) as exc:
                retarget_status = "failed"
                reason = str(exc)
                checks.append({"severity": "ERROR", "code": "RETARGET_FAILED", "description": reason,
                               "asset": path.name, "location": "retarget",
                               "suggested_fix": "Provide a manual bone map in config/bone_mappings/."})
        elif suffix == ".fbx":
            retarget_status = "manual_bone_map_required"
            checks.append({"severity": "WARNING", "code": "MANUAL_BONE_MAP_REQUIRED",
                           "description": "FBX skeleton cannot be paired with the target without an explicit bone map",
                           "asset": path.name, "location": "retarget",
                           "suggested_fix": "Add config/bone_mappings/mixamo_to_game.json and reference it from config/studio.yaml."})
        elif suffix in (".glb", ".gltf"):
            # Only an identical skeleton may be passed through; otherwise a bone
            # map is required. We never claim playback for an unverified pairing.
            src_names = {b["name"] for b in source_bones}
            tgt_bones = next(m for m in _model_manifests() if m["id"] == target_id)["skeleton"]["bones"]
            if src_names == {b["name"] for b in tgt_bones} and clips:
                runtime, _ = _to_runtime_glb(path, "animation")
                glb = runtime
                retarget_status = "passthrough_identical_skeleton"
            else:
                retarget_status = "manual_bone_map_required"
                checks.append({"severity": "WARNING", "code": "MANUAL_BONE_MAP_REQUIRED",
                               "description": "Embedded glTF animation cannot be safely retargeted without a bone map",
                               "asset": path.name, "location": "retarget",
                               "suggested_fix": "Add an explicit mapping in config/bone_mappings/."})
    else:
        retarget_status = "incompatible"

    if glb is not None:
        processed_path = path_for("processed", "animations", aid, f"{aid}.glb")
        processed_path.parent.mkdir(parents=True, exist_ok=True)
        processed_path.write_bytes(glb)
        for c in validate_glb(processed_path):
            checks.append(c)
    else:
        processed_path = None
    for issue in metadata.get("issues", []):
        checks.append({**issue, "asset": path.name, "component": "source"})

    report = build_report(aid, "animation", rel, checks, context={
        "processing_version": cfg["studio"]["processing_version"],
        "retarget_status": retarget_status, "target_character_id": target_id,
        "compatibility_checked": len(compatibility),
        "download_policy": "first compatible target by manifest id (see docs/animation.md)",
    })
    manifest = build_manifest(
        asset_id=aid, kind="animation", name=path.stem, category=category_of(rel),
        source_path=rel, source_hash=source_hash, source_format=suffix.upper().lstrip("."),
        processed_path=str(processed_path.relative_to(ROOT)) if processed_path else None,
        processed_hash=sha256_bytes(glb) if glb else None, processed_bytes=len(glb) if glb else 0,
        processing_version=cfg["studio"]["processing_version"],
        validation_status=report["status"], report=f"reports/animations/{aid}.json",
        metadata=metadata, target_character_id=target_id,
        target_name=target_id, retarget_status=retarget_status,
        compatibility=compatibility, download_reference=f"assets/{aid}/download" if processed_path else "",
        animation_asset={
            "original_filename": path.name,
            "organized_path": _organized_path_for(rel),
            "classification": classification,
            "skeleton_id": _skeleton_id(source_bones),
            "compatibility_group": (_skeleton_id(source_bones) if compatibility else ""),
            "root_motion": (clips[0].get("root_motion") if clips else None),
            "retarget_required": retarget_status not in ("not_required", "passthrough_identical_skeleton"),
            "retarget": {key: value for key, value in (metadata.get("retarget") or {}).items()
                         if key in ("mapped_bones", "dropped_roles", "channel_count",
                                    "duration_seconds", "root_motion", "height_ratio",
                                    "target_character", "method", "frame_count")} or None,
            "retarget_validation": (metadata.get("retarget") or {}).get("validation"),
        },
    )
    manifest["incompatibility_reason"] = reason or ("compatible with " + target_id if target_id else "")
    paths = publish_asset(aid, "animation", glb, metadata, report, to_markdown(report), manifest)
    log("ANIM", f"{aid}: {report['status']} retarget={retarget_status} target={target_id or '-'}")
    return {"asset_id": aid, "status": report["status"], "retarget_status": retarget_status,
            "target_character_id": target_id, "processed": str(processed_path) if processed_path else None}


def status() -> dict[str, Any]:
    info = {
        "processing_version": __version__, "python": sys.version.split()[0],
        "blender_binary": _blender_available(), "khronos_validator": validator_available(),
        "bpy_module": False, "java": shutil.which("java") is not None,
        "gradle": shutil.which("gradle") is not None,
        "android_sdk": bool(os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")),
    }
    try:
        import bpy
        info["bpy_module"] = bpy.app.version_string
    except Exception:
        pass
    return info


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="asset-studio", description="3D Asset Studio pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    for cmd in ("process-models", "process-animations"):
        p = sub.add_parser(cmd)
        p.add_argument("--all", action="store_true", help="process every discovered asset")
        p.add_argument("files", nargs="*", help="specific source files")
        p.add_argument("--job-id", default="")
    sub.add_parser("build-index")
    sub.add_parser("status")
    p = sub.add_parser("inventory")
    p.add_argument("--no-archive", action="store_true",
                   help="exclude assets/animation/Upload (the untouched archive)")
    p = sub.add_parser("organize")
    p.add_argument("--apply", action="store_true", help="copy files (default: dry run)")
    p.add_argument("--skip-unclassified", action="store_true",
                   help="keep unclassified sources in place instead of copying them")
    p = sub.add_parser("validate")
    p.add_argument("files", nargs="*")
    args = parser.parse_args(argv)

    if args.command == "status":
        print(json.dumps(status(), indent=2)); return 0
    if args.command == "build-index":
        index = build_index()
        try:
            from backend.database.sync import sync_from_manifests
            sync_from_manifests()
        except Exception as exc:
            log("WARN", f"database sync skipped: {exc}")
        print(json.dumps({"models": index["counts"]["models"],
                          "animations": index["counts"]["animations"]}, indent=2)); return 0
    if args.command == "inventory":
        print(json.dumps(library_inventory(include_archive=not args.no_archive), indent=2)); return 0
    if args.command == "organize":
        result = organize_animations(apply=args.apply, skip_unclassified=args.skip_unclassified)
        print(json.dumps(result, indent=2)); return 0 if result["failures"] == 0 else 1
    if args.command == "validate":
        files = [Path(f) for f in args.files] or sorted(path_for("processed").rglob("*.glb"))
        for f in files:
            checks = validate_glb(f)
            errors = sum(1 for c in checks if c["severity"] == "ERROR")
            warn = sum(1 for c in checks if c["severity"] == "WARNING")
            print(f"{f}: E{errors} W{warn} {[c['code'] for c in checks]}")
        return 0

    kind = "model" if args.command == "process-models" else "animation"
    files = [Path(f) for f in args.files] if args.files else (_discover(kind) if args.all else [])
    if not files:
        log("ERROR", "no source files found (use --all or pass paths)"); return 2
    job_id = args.job_id or datetime.now(timezone.utc).strftime("job-%Y%m%dT%H%M%SZ")
    fn = process_model if kind == "model" else process_animation
    results = []
    failures = 0
    for f in files:
        try:
            results.append(fn(f.resolve()))
        except Exception as exc:
            failures += 1
            log("ERROR", f"{f}: {exc}")
    record_job(job_id, kind, "failed" if failures else "completed",
               [r["asset_id"] for r in results], f"{failures} failure(s)")
    build_index()
    print(json.dumps({"job_id": job_id, "processed": len(results), "failures": failures,
                      "results": results}, indent=2))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
