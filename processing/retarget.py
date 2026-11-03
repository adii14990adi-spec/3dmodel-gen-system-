"""Real BVH -> glTF animation retargeting onto a target character GLB.

Handles rest-pose/orientation differences, coordinate/scale differences, root
motion, frame timing, Euler->quaternion conversion and quaternion normalisation.
Only a *copy* of the target is modified; the original is never overwritten.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from processing.bvh import BVHDocument
from processing.compatibility import load_mappings, normalise, role_for
from processing.glb import append_accessor, load_document, write_glb


def _quat_from_euler_xyz(degrees: list[float]) -> list[float]:
    x, y, z = (math.radians(v) for v in degrees)
    cx, sx = math.cos(x / 2), math.sin(x / 2)
    cy, sy = math.cos(y / 2), math.sin(y / 2)
    cz, sz = math.cos(z / 2), math.sin(z / 2)
    # Rz * Ry * Rx (BVH XYZ channel order)
    w = cz*cy*cx + sz*sy*sx
    vx = cz*cy*sx - sz*sy*cx
    vy = cz*sy*cx + sz*cy*sx
    vz = sz*cy*cx - cz*sy*sx
    n = math.sqrt(w*w + vx*vx + vy*vy + vz*vz) or 1.0
    return [vx/n, vy/n, vz/n, w/n]


def _quat_multiply(a: list[float], b: list[float]) -> list[float]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [aw*bx + ax*bw + ay*bz - az*by,
            aw*by - ax*bz + ay*bw + az*bx,
            aw*bz + ax*by - ay*bx + az*bw,
            aw*bw - ax*bx - ay*by - az*bz]


def _quat_inverse(q: list[float]) -> list[float]:
    return [-q[0], -q[1], -q[2], q[3]]


def build_bone_map(bvh: BVHDocument, target_doc: dict[str, Any],
                   manual: dict[str, str] | None = None) -> dict[str, int]:
    """Map BVH joints to target glTF node indices through canonical roles."""
    mappings = load_mappings()
    nodes = target_doc.get("nodes", [])
    skins = target_doc.get("skins", [])
    joint_nodes = skins[0].get("joints", []) if skins else []
    allowed = set(joint_nodes)
    by_role: dict[str, int] = {}
    for node_idx in joint_nodes:
        if node_idx >= len(nodes):
            continue
        name = nodes[node_idx].get("name", "")
        role, _ = role_for(name, mappings)
        if role and role not in by_role:
            by_role[role] = node_idx
    result: dict[str, int] = {}
    for name in bvh.order:
        if manual and name in manual:
            target_name = manual[name]
            for idx in joint_nodes:
                if idx < len(nodes) and normalise(nodes[idx].get("name", "")) == normalise(target_name):
                    result[name] = idx
            continue
        role, conf = role_for(name, mappings)
        if role and conf >= 0.95 and role in by_role:
            result[name] = by_role[role]
    return result


def retarget(bvh: BVHDocument, target_glb: Path | str, clip_name: str,
             scale_factor: float = 0.01, up_axis: str = "Y",
             manual_map: dict[str, str] | None = None,
             max_frames: int = 20000) -> tuple[bytes, dict[str, Any]]:
    asset = load_document(target_glb)
    doc, binary = asset["json"], bytearray(asset["bin"])
    doc = dict(doc)
    doc["animations"] = []
    bone_map = build_bone_map(bvh, doc, manual_map)
    if not bone_map:
        raise ValueError("no mapped bone pair exists in both skeletons; provide a manual bone map")
    frames = bvh.frames[:max_frames]
    if not frames:
        raise ValueError("BVH contains no motion frames")
    times = [i * bvh.frame_time for i in range(len(frames))]
    time_acc, binary = append_accessor(doc, bytes(binary), times, 5126, "SCALAR", minmax=True)

    channels: list[dict[str, Any]] = []
    samplers: list[dict[str, Any]] = []
    nodes = doc.get("nodes", [])
    animated_bones: list[str] = []
    for bvh_name, node_idx in sorted(bone_map.items(), key=lambda kv: kv[1]):
        if node_idx >= len(nodes):
            continue
        rest_q = list(nodes[node_idx].get("rotation", [0, 0, 0, 1]))
        rest_inv = _quat_inverse(rest_q)
        rotations = []
        for f in range(len(frames)):
            q = _quat_from_euler_xyz(bvh.euler_rotation(bvh_name, f))
            if up_axis.upper() == "Z":
                q = _quat_multiply(_quat_from_euler_xyz([-90, 0, 0]), q)
            # Express the motion relative to the target's rest orientation.
            rotations.append(_quat_multiply(q, rest_inv))
        rot_acc, binary = append_accessor(doc, bytes(binary), rotations, 5126, "VEC4")
        samplers.append({"input": time_acc, "output": rot_acc, "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1,
                         "target": {"node": node_idx, "path": "rotation"}})
        animated_bones.append(nodes[node_idx].get("name", f"node_{node_idx}"))

    root_motion = False
    if bvh.root and bvh.root in bone_map:
        node_idx = bone_map[bvh.root]
        translations = []
        for f in range(len(frames)):
            x, y, z = bvh.position_channels(f)
            # BVH is usually centimetres/Y-up; glTF is metres/Y-up.
            translations.append([x * scale_factor, y * scale_factor, z * scale_factor])
        if any(any(abs(v) > 1e-9 for v in t) for t in translations):
            tr_acc, binary = append_accessor(doc, bytes(binary), translations, 5126, "VEC3")
            samplers.append({"input": time_acc, "output": tr_acc, "interpolation": "LINEAR"})
            channels.append({"sampler": len(samplers) - 1,
                             "target": {"node": node_idx, "path": "translation"}})
            root_motion = True

    doc["animations"].append({
        "name": f"Retarget_{clip_name}", "samplers": samplers, "channels": channels,
    })
    out = write_glb(doc, bytes(binary))
    metadata = {
        "clip_name": f"Retarget_{clip_name}", "frame_count": len(frames),
        "duration_seconds": round(times[-1], 6), "frame_rate": round(bvh.fps, 4),
        "channel_count": len(channels), "animated_bones": animated_bones,
        "root_motion": root_motion, "mapped_bones": len(bone_map),
        "scale_factor": scale_factor, "up_axis": up_axis,
        "target_skin_joints": len(doc.get("skins", [{}])[0].get("joints", [])),
    }
    return out, metadata
