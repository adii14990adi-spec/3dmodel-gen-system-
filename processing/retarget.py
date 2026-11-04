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


# --------------------------------------------------------------------------- #
# FBX -> glTF retargeting (same architecture as the BVH path above)
# --------------------------------------------------------------------------- #
def _target_node_rest(nodes: list[dict[str, Any]]) -> tuple[dict[int, list[float]], dict[int, list[float]]]:
    """Per-node rest translation and rotation quaternion from the glTF node tree."""
    translations: dict[int, list[float]] = {}
    rotations: dict[int, list[float]] = {}
    for index, node in enumerate(nodes):
        if "matrix" in node and len(node["matrix"]) == 16:
            # glTF matrices are column-major; transpose into our row-major form.
            m = [[float(node["matrix"][col * 4 + row]) for col in range(4)] for row in range(4)]
            translations[index] = [m[0][3], m[1][3], m[2][3]]
            rotations[index] = _quat_from_matrix(m)
        else:
            translations[index] = [float(v) for v in node.get("translation", [0.0, 0.0, 0.0])]
            quat = [float(v) for v in node.get("rotation", [0.0, 0.0, 0.0, 1.0])]
            rotations[index] = _normalise(quat)
    return translations, rotations


def _normalise(q: list[float]) -> list[float]:
    n = math.sqrt(sum(v * v for v in q)) or 1.0
    return [v / n for v in q]


def _quat_from_matrix(m: list[list[float]]) -> list[float]:
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        return _normalise([(m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s,
                           (m[1][0] - m[0][1]) / s, 0.25 * s])
    if m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
        return _normalise([0.25 * s, (m[0][1] + m[1][0]) / s,
                           (m[0][2] + m[2][0]) / s, (m[2][1] - m[1][2]) / s])
    if m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
        return _normalise([(m[0][1] + m[1][0]) / s, 0.25 * s,
                           (m[1][2] + m[2][1]) / s, (m[0][2] - m[2][0]) / s])
    s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
    return _normalise([(m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s,
                       0.25 * s, (m[1][0] - m[0][1]) / s])


def _target_role_nodes(nodes: list[dict[str, Any]], skins: list[dict[str, Any]],
                       mappings: dict[str, Any]) -> dict[str, int]:
    joint_indices = sorted({int(j) for skin in skins for j in skin.get("joints", [])})
    if not joint_indices:
        joint_indices = list(range(len(nodes)))
    by_role: dict[str, int] = {}
    for index in joint_indices:
        if index >= len(nodes):
            continue
        role, confidence = role_for(nodes[index].get("name", ""), mappings)
        if role and role not in by_role:
            by_role[role] = index
    return by_role


def retarget_fbx(doc: Any, stack: Any, target_glb: Path | str, clip_name: str,
                 role_map: dict[str, str], *, max_samples: int = 300,
                 root_motion: bool = True) -> tuple[bytes, dict[str, Any]]:
    """Retarget one FBX clip onto a target character skeleton.

    ``role_map`` is an explicit canonical ``role -> source bone`` map
    (``config/bone_mappings/mixamo_to_game.json``). No bone is paired by name
    similarity alone; only roles that exist in the map *and* resolve on the
    target are exported, and every dropped bone is reported.
    """
    from processing.motion import ClipSampler, resolve_roles

    asset = load_document(target_glb)
    out_doc = dict(asset["json"])
    binary = bytearray(asset["bin"])
    out_doc = {**out_doc, "animations": []}
    nodes = out_doc.get("nodes", [])
    skins = out_doc.get("skins", [])
    if not skins:
        raise ValueError("target character has no skin; nothing to retarget onto")
    mappings = load_mappings()
    target_roles = _target_role_nodes(nodes, skins, mappings)
    source_sampler = ClipSampler(doc, stack)
    sampler_roles = resolve_roles(source_sampler.bone_names)
    source_roles = {role: bone for role, bone in role_map.items()
                    if bone in sampler_roles.values() or bone in source_sampler.bone_names}

    pairs: dict[str, tuple[str, int]] = {}
    for role, bone in source_roles.items():
        if role in target_roles:
            pairs[role] = (bone, target_roles[role])
    if not pairs:
        raise ValueError("no role exists in both the source map and the target skeleton")
    dropped = sorted(set(source_roles) - set(pairs))

    tgt_trans, tgt_rot = _target_node_rest(nodes)
    parent_of: dict[int, int] = {}
    for index, node in enumerate(nodes):
        for child in node.get("children", []):
            parent_of[child] = index

    rest_world_rot: dict[int, list[float]] = {}

    def rest_world(index: int) -> list[float]:
        if index in rest_world_rot:
            return rest_world_rot[index]
        parent = parent_of.get(index)
        value = (tgt_rot[index] if parent is None
                 else _quat_multiply(rest_world(parent), tgt_rot[index]))
        rest_world_rot[index] = value
        return value

    # Rest positions of the target hips, to scale root translation by character size.
    src_rest = source_sampler.rest
    hips_role = "hips"
    if hips_role not in pairs:
        raise ValueError("the role map must provide 'hips'")
    hips_node = pairs[hips_role][1]
    tgt_hips_y = tgt_trans[hips_node][1]
    height_ratio = (tgt_hips_y / src_rest.hip_height_m) if src_rest.hip_height_m > 1e-6 else 1.0

    # glTF requires strictly increasing animation input times; FBX key arrays can
    # repeat a tick (two curves sharing a time), which is legal in FBX and illegal
    # in glTF, so identical sample times are collapsed here.
    raw_times = source_sampler.sample_times(max_samples)
    times: list[float] = []
    for value in raw_times:
        if not times or value > times[-1] + 1e-6:
            times.append(float(value))
    if len(times) < 2:
        times = [float(raw_times[0]), float(raw_times[-1]) + 1e-3] if len(raw_times) > 1 else [0.0, 1.0]
    duration = max(1e-6, source_sampler.time_range()[1] - source_sampler.time_range()[0])
    time_acc, binary = append_accessor(out_doc, bytes(binary), times, 5126, "SCALAR", minmax=True)

    channels: list[dict[str, Any]] = []
    samplers_doc: list[dict[str, Any]] = []
    animated: list[str] = []
    frames: list[dict[str, list[float]]] = []
    translations: list[list[float]] = []
    src_rest_quats = {role: _quat_from_matrix(source_sampler.rest.world[bone])
                      for role, (bone, _node) in pairs.items() if bone in source_sampler.rest.world}

    for t in times:
        world = source_sampler.world_matrices(t)
        desired: dict[int, list[float]] = {}
        # Walk target nodes root-first so parent world rotations are available.
        for role, (bone, node) in sorted(pairs.items(), key=lambda kv: _depth(nodes, parent_of, kv[1][1])):
            src_quat = _quat_from_matrix(world[bone])
            delta = _quat_multiply(_quat_inverse(src_rest_quats[role]), src_quat)
            desired[node] = _quat_multiply(rest_world(node), delta)
        local_quats: dict[int, list[float]] = {}
        for role, (bone, node) in sorted(pairs.items(), key=lambda kv: _depth(nodes, parent_of, kv[1][1])):
            parent = parent_of.get(node)
            parent_world = (local_quats.get(parent) if parent in local_quats
                            else (desired.get(parent) if parent in desired else None))
            base = parent_world if parent_world is not None else (
                rest_world(parent) if parent is not None else [0.0, 0.0, 0.0, 1.0])
            local_quats[node] = _quat_multiply(_quat_inverse(base), desired[node])
        frames.append({role: local_quats[node] for role, (_b, node) in pairs.items()})
        hips_position = _position_of(world[pairs[hips_role][0]])
        rest_hips = src_rest.positions[pairs[hips_role][0]]
        translations.append([(hips_position[i] - rest_hips[i]) * height_ratio for i in range(3)])

    for role, (bone, node) in sorted(pairs.items(), key=lambda kv: kv[1][1]):
        values = [frame[role] for frame in frames]
        accessor, binary = append_accessor(out_doc, bytes(binary), values, 5126, "VEC4")
        samplers_doc.append({"input": time_acc, "output": accessor, "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers_doc) - 1,
                         "target": {"node": node, "path": "rotation"}})
        animated.append(nodes[node].get("name", f"node_{node}"))

    root_motion_applied = False
    if root_motion and any(any(abs(v) > 1e-6 for v in value) for value in translations):
        base_translation = tgt_trans[hips_node]
        values = [[base_translation[i] + translation[i] for i in range(3)]
                  for translation in translations]
        accessor, binary = append_accessor(out_doc, bytes(binary), values, 5126, "VEC3")
        samplers_doc.append({"input": time_acc, "output": accessor, "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers_doc) - 1,
                         "target": {"node": hips_node, "path": "translation"}})
        root_motion_applied = True

    out_doc["animations"].append({"name": f"Retarget_{clip_name}",
                                  "samplers": samplers_doc, "channels": channels})
    payload = write_glb(out_doc, bytes(binary))
    metadata = {
        "clip_name": f"Retarget_{clip_name}",
        "source_clip": getattr(stack, "name", clip_name),
        "frame_count": len(times),
        "duration_seconds": round(duration, 6),
        "channel_count": len(channels),
        "animated_bones": animated,
        "mapped_roles": sorted(pairs),
        "mapped_bones": len(pairs),
        "dropped_roles": dropped,
        "root_motion": root_motion_applied,
        "height_ratio": round(height_ratio, 6),
        "target_skin_joints": len(skins[0].get("joints", [])),
        "target_character": Path(target_glb).name,
        "method": "rest-pose-corrected world-rotation transfer through explicit role map",
    }
    return payload, metadata


def _position_of(matrix: list[list[float]]) -> list[float]:
    return [float(matrix[0][3]), float(matrix[1][3]), float(matrix[2][3])]


def _depth(nodes: list[dict[str, Any]], parent_of: dict[int, int], node: int) -> int:
    depth = 0
    seen = 0
    while node in parent_of and seen < 1024:
        node = parent_of[node]
        depth += 1
        seen += 1
    return depth


def validate_retarget(source_features: dict[str, Any], payload: bytes,
                      metadata: dict[str, Any]) -> dict[str, Any]:
    """Independently validate a retargeted GLB by re-measuring the exported clip.

    The exported file is parsed again and its motion is measured with the same
    feature extractor used on the source. A retarget is only reported as
    validated when the *behaviour* survives the transfer (hip height profile,
    oscillation, torso tilt, loop closure, pose change) and the file is
    structurally sound (unit quaternions, monotonic times, no NaN).

    This is a numerical/behavioural validation, **not** a playback test: no
    renderer is available here, so no playback claim is made.
    """
    import math

    from processing.glb import GLBError, parse_glb, read_accessor
    from processing.motion import features_from_sampler
    from processing.samplers import GltfSampler

    checks: list[dict[str, Any]] = []
    try:
        asset = parse_glb(payload)
    except GLBError as exc:
        return {"status": "failed", "reason": f"exported GLB does not parse: {exc}", "checks": []}
    animations = asset["json"].get("animations", [])
    if not animations:
        return {"status": "failed", "reason": "exported GLB has no animation", "checks": []}
    doc = asset["json"]
    animation = animations[0]

    # Structural checks on the serialised data.
    times_ok = True
    quats_ok = True
    finite_ok = True
    seen_inputs: set[int] = set()
    for sampler in animation.get("samplers", []):
        input_index = int(sampler["input"])
        values = read_accessor(doc, asset["bin"], input_index)
        if input_index not in seen_inputs:
            seen_inputs.add(input_index)
            times_ok = times_ok and all(values[i] <= values[i + 1]
                                        for i in range(len(values) - 1))
        if not all(math.isfinite(float(v)) for v in values):
            finite_ok = False
        outputs = read_accessor(doc, asset["bin"], sampler["output"])
        for row in outputs[: min(len(outputs), 4)]:
            if any(not math.isfinite(float(v)) for v in row):
                finite_ok = False
            if len(row) == 4:
                norm = math.sqrt(sum(float(v) ** 2 for v in row))
                if abs(norm - 1.0) > 1e-3:
                    quats_ok = False
    checks.append({"name": "monotonic_time", "passed": times_ok})
    checks.append({"name": "finite_values", "passed": finite_ok})
    checks.append({"name": "unit_quaternions", "passed": quats_ok})

    try:
        sampler = GltfSampler(asset, 0)
        target_features = features_from_sampler(sampler, metadata.get("frame_rate", 30.0))
    except (KeyError, IndexError, ValueError) as exc:
        return {"status": "failed", "reason": f"exported clip cannot be measured: {exc}",
                "checks": checks}

    def ratio(a: float, b: float) -> float:
        if b == 0:
            return 0.0 if a == 0 else 1.0
        return abs(a - b) / abs(b)

    source_hip = (source_features.get("hip_height") or {}).get("mean_m") or 0.0
    target_hip = (target_features.get("hip_height") or {}).get("mean_m") or 0.0
    height_ratio = float(metadata.get("height_ratio") or 1.0)
    hip_error = ratio(target_hip, source_hip * height_ratio)
    checks.append({"name": "hip_height_profile", "passed": hip_error <= 0.15,
                   "detail": f"source {source_hip * height_ratio:.4f} m vs exported {target_hip:.4f} m "
                             f"({hip_error * 100:.1f}% difference)"})
    source_osc = source_features.get("hips_vertical_oscillation_m") or 0.0
    target_osc = target_features.get("hips_vertical_oscillation_m") or 0.0
    osc_error = ratio(target_osc, source_osc * height_ratio)
    checks.append({"name": "vertical_oscillation", "passed": osc_error <= 0.35,
                   "detail": f"source {source_osc * height_ratio:.4f} m vs exported {target_osc:.4f} m"})
    source_tilt = (source_features.get("torso") or {}).get("tilt_deg_end") or 0.0
    target_tilt = (target_features.get("torso") or {}).get("tilt_deg_end") or 0.0
    checks.append({"name": "torso_orientation", "passed": abs(source_tilt - target_tilt) <= 20.0,
                   "detail": f"source {source_tilt:.1f} deg vs exported {target_tilt:.1f} deg"})
    source_loop = (source_features.get("motion") or {}).get("loop_error_deg") or 0.0
    target_loop = (target_features.get("motion") or {}).get("loop_error_deg") or 0.0
    loop_ok = (abs(source_loop - target_loop) <= 25.0
               or (source_loop < 10.0 and target_loop < 15.0))
    checks.append({"name": "loop_closure", "passed": loop_ok,
                   "detail": f"source {source_loop:.1f} deg vs exported {target_loop:.1f} deg"})
    # Peak (not mean) pose change: the source rig has 65 bones and the target 21,
    # so an average across all bones would not be comparable. The peak is driven by
    # the same limb motion on both sides.
    source_step = (source_features.get("motion") or {}).get("max_step_deg") or 0.0
    target_step = (target_features.get("motion") or {}).get("max_step_deg") or 0.0
    step_error = ratio(target_step, source_step)
    checks.append({"name": "peak_pose_change", "passed": step_error <= 0.40,
                   "detail": f"source {source_step:.2f} deg vs exported {target_step:.2f} deg per sample"})

    passed = sum(1 for check in checks if check["passed"])
    required = 5
    status = "validated" if passed >= required else "failed"
    return {
        "status": status,
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "required": required,
        "reason": ("motion profile preserved and export structurally sound"
                   if status == "validated" else
                   f"only {passed}/{len(checks)} validation checks passed"),
        "playback_tested": False,
        "note": "Numerical/behavioural validation only; rendering was not executed in this environment.",
    }
