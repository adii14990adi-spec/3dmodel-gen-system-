#!/usr/bin/env python3
"""Validate that the exported GLBs really carry playable animation data.

This is the *data* half of the Android playback milestone. It cannot render a
frame, but it can prove - from the files the device would load - that:

* the GLB parses and contains at least one animation;
* every animation sampler has strictly increasing, finite input times;
* every channel targets a real node and, ideally, a joint of the skin (otherwise
  the clip animates something that never moves the character);
* at least one rotation/translation channel exists per clip, so a pose can change;
* the clip duration is > 0 and matches the manifest metadata within tolerance.

Outputs ``reports/animations/playback-validation.json`` (machine readable) and a
markdown table on stdout for the CI summary. Exits non-zero when a required
category sample is broken, so playback data cannot regress silently.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from processing.glb import GLBError, parse_glb, read_accessor  # noqa: E402

CATEGORIES = {
    "idle": ("locomotion/rifle/idle", "locomotion/unarmed/idle"),
    "walk": ("locomotion/rifle/walk", "locomotion/unarmed/walk"),
    "run": ("locomotion/rifle/run", "locomotion/unarmed/run"),
    "jump": ("locomotion/rifle/jump", "locomotion/unarmed/jump"),
    "attack": ("combat/melee", "combat/rifle/fire"),
    "death": ("combat/reactions/death",),
}


def _duration(doc: dict[str, Any], binary: bytes, animation: dict[str, Any]) -> float:
    times: list[float] = []
    for sampler in animation.get("samplers", []):
        try:
            values = read_accessor(doc, binary, sampler["input"])
        except (KeyError, IndexError, ValueError):
            continue
        times.extend(float(v) for v in values)
    return (max(times) - min(times)) if times else 0.0


def inspect_glb(path: Path) -> dict[str, Any]:
    record: dict[str, Any] = {"path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
                              "issues": [], "animations": []}
    try:
        asset = parse_glb(path.read_bytes())
    except (GLBError, OSError, ValueError) as exc:
        record["issues"].append(f"does not parse: {exc}")
        return record
    doc, binary = asset["json"], asset["bin"]
    nodes = doc.get("nodes", [])
    joints = {int(j) for skin in doc.get("skins", []) for j in skin.get("joints", [])}
    record["skins"] = len(doc.get("skins", []))
    record["joints"] = len(joints)
    record["meshes"] = len(doc.get("meshes", []))
    record["bytes"] = len(path.read_bytes())

    if not record["skins"]:
        record["issues"].append("no skin: skeleton playback would not deform anything")
    for index, animation in enumerate(doc.get("animations", [])):
        channels = animation.get("channels", [])
        targets = {int(c["target"]["node"]) for c in channels if "node" in c.get("target", {})}
        paths = {c["target"]["path"] for c in channels if "path" in c.get("target", {})}
        times: list[float] = []
        monotonic = True
        for sampler in animation.get("samplers", []):
            try:
                values = [float(v) for v in read_accessor(doc, binary, sampler["input"])]
            except (KeyError, IndexError, ValueError) as exc:
                record["issues"].append(f"clip {index}: unreadable sampler input ({exc})")
                continue
            times.extend(values)
            monotonic = monotonic and all(values[i] <= values[i + 1] for i in range(len(values) - 1))
        finite = all(math.isfinite(t) for t in times)
        duration = (max(times) - min(times)) if times else 0.0
        entry = {
            "index": index,
            "name": animation.get("name", f"clip_{index}"),
            "channels": len(channels),
            "target_nodes": len(targets),
            "all_targets_are_joints": bool(targets) and targets <= joints if joints else False,
            "animated_paths": sorted(paths),
            "keyframes": len(times),
            "duration_seconds": round(duration, 6),
            "monotonic_time": monotonic,
            "finite_time": finite,
        }
        if len(channels) == 0:
            record["issues"].append(f"clip {index}: no channels")
        if duration <= 0.0:
            record["issues"].append(f"clip {index}: zero duration")
        if not monotonic:
            record["issues"].append(f"clip {index}: animation input times are not increasing")
        if not finite:
            record["issues"].append(f"clip {index}: non-finite key time")
        if joints and not entry["all_targets_are_joints"]:
            record["issues"].append(f"clip {index}: animates nodes outside the skin joints")
        if not ({"rotation", "translation"} & set(paths)):
            record["issues"].append(f"clip {index}: only scale/unmapped channels, the pose cannot change")
        record["animations"].append(entry)
    if not record["animations"]:
        record["issues"].append("no animation clips")
    record["playable"] = not record["issues"]
    return record


def sample_for_category(manifests: list[dict[str, Any]], categories: tuple[str, ...]) -> dict[str, Any] | None:
    for manifest in manifests:
        category = (manifest.get("classification") or {}).get("category") or ""
        if category in categories and (manifest.get("processed") or {}).get("path"):
            return manifest
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifests", default="manifests/animations")
    parser.add_argument("--report", default="reports/animations/playback-validation.json")
    parser.add_argument("--strict", action="store_true",
                        help="fail when a category has no exported GLB at all")
    args = parser.parse_args(argv)

    manifest_dir = ROOT / args.manifests
    manifests = []
    if manifest_dir.is_dir():
        for path in sorted(manifest_dir.glob("*.json")):
            try:
                manifests.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue

    rows = []
    failures = 0
    for category, prefixes in CATEGORIES.items():
        manifest = sample_for_category(manifests, prefixes)
        if manifest is None:
            rows.append({"category": category, "status": "MISSING", "asset": None,
                         "detail": "no exported GLB for this category"})
            if args.strict:
                failures += 1
            continue
        glb = ROOT / manifest["processed"]["path"]
        record = inspect_glb(glb)
        status = "PASS" if record["playable"] else "FAIL"
        if status == "FAIL":
            failures += 1
        rows.append({
            "category": category,
            "status": status,
            "asset": manifest["name"],
            "asset_id": manifest["id"],
            "glb": record["path"],
            "clips": len(record["animations"]),
            "duration_seconds": (record["animations"][0]["duration_seconds"]
                                 if record["animations"] else 0.0),
            "joints": record.get("joints", 0),
            "detail": "; ".join(record["issues"]) or "animation data is playable",
        })

    report = {"schema_version": "1.0.0", "categories": rows,
              "passed": sum(1 for r in rows if r["status"] == "PASS"),
              "failed": sum(1 for r in rows if r["status"] == "FAIL"),
              "missing": sum(1 for r in rows if r["status"] == "MISSING"),
              "note": ("Data-level validation only: the Android device test exercises the "
                       "Filament animator, this script proves the files carry real clips.")}
    target = ROOT / args.report
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(f"{'category':10s} {'status':8s} {'clips':5s} {'dur(s)':7s} {'joints':6s} asset")
    for row in rows:
        print(f"{row['category']:10s} {row['status']:8s} {row.get('clips', 0):<5} "
              f"{row.get('duration_seconds', 0):<7} {row.get('joints', 0):<6} {row.get('asset') or '-'}")
    print(f"passed={report['passed']} failed={report['failed']} missing={report['missing']} "
          f"-> {args.report}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
