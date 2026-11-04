"""Forward kinematics and motion features for inspected animation sources.

The classifier must not trust file names, so every decision is grounded in
measurements taken from the animation curves themselves: how far the character
travels, how high the hips sit relative to the ground, whether both feet leave
the ground, how the torso ends up, whether the clip loops, where the hands are
and how much the pose changes over time.

All distances are metres and all matrices are Y-up (the FBX
``GlobalSettings`` up-axis is reported separately; the uploaded rigs are Y-up).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from processing import transform as tf
from processing.compatibility import load_mappings, normalise, role_for
from processing.fbx import FBXDocument, FBXModel, FBXStack, TICKS_PER_SECOND

# Roles used by the motion analyser. Missing roles simply disable the features
# that depend on them; nothing is fabricated.
_ROLE_PRIORITY = ("hips", "spine", "chest", "neck", "head",
                  "left_upper_arm", "left_lower_arm", "left_hand",
                  "right_upper_arm", "right_lower_arm", "right_hand",
                  "left_upper_leg", "left_lower_leg", "left_foot", "left_toe",
                  "right_upper_leg", "right_lower_leg", "right_foot", "right_toe")

MAX_SAMPLES = 600


@dataclass
class RestPose:
    order: list[str]
    local: dict[str, list[list[float]]]
    world: dict[str, list[list[float]]]
    positions: dict[str, list[float]]
    parent: dict[str, str | None]
    height_m: float = 0.0
    hip_height_m: float = 0.0


def hierarchy_order(doc: FBXDocument) -> list[FBXModel]:
    """Parents before children; deterministic for stable manifests and tests."""
    by_uid = {model.uid: model for model in doc.models}
    ordered: list[FBXModel] = []
    seen: set[int] = set()

    def visit(model: FBXModel, guard: int) -> None:
        if model.uid in seen or guard > len(by_uid):
            return
        seen.add(model.uid)
        ordered.append(model)
        for child in sorted(doc.children_of(model.uid), key=lambda m: m.name):
            visit(child, guard + 1)

    for root in sorted(doc.root_models(), key=lambda m: m.name):
        visit(root, 0)
    for model in doc.models:  # orphan/cycle safety: include anything left over
        if model.uid not in seen:
            ordered.append(model)
            seen.add(model.uid)
    return ordered


def rest_pose(doc: FBXDocument) -> RestPose:
    """Rest pose in metres, built from Lcl/Pre/Post rotation and scaling."""
    scale = doc.scale_to_metres
    order = [model.name for model in hierarchy_order(doc)]
    local: dict[str, list[list[float]]] = {}
    world: dict[str, list[list[float]]] = {}
    positions: dict[str, list[float]] = {}
    parent: dict[str, str | None] = {}
    by_uid = {model.uid: model for model in doc.models}
    for model in hierarchy_order(doc):
        translation = [v * scale for v in model.translation]
        rotation = tf.multiply(
            tf.euler_to_matrix(model.pre_rotation, model.rotation_order),
            tf.euler_to_matrix(model.rotation, model.rotation_order),
        )
        if any(abs(v) > 1e-9 for v in model.post_rotation):
            rotation = tf.multiply(
                rotation,
                tf.euler_to_matrix([-v for v in model.post_rotation], model.rotation_order),
            )
        matrix = tf.multiply(tf.translation(*translation),
                             tf.multiply(rotation, tf.scaling(*model.scaling)))
        local[model.name] = matrix
        parent_model = by_uid.get(doc.parent_of.get(model.uid, -1))
        parent[model.name] = parent_model.name if parent_model else None
        world[model.name] = (tf.multiply(world[parent_model.name], matrix)
                             if parent_model else matrix)
        positions[model.name] = tf.position_of(world[model.name])

    height = 0.0
    for name, position in positions.items():
        if normalise(name).startswith(("headtop", "mixamorig_headtop")):
            height = max(height, position[1])
    hip_height = 0.0
    roles = resolve_roles(list(positions))
    if roles.get("hips"):
        hip_height = positions[roles["hips"]][1]
    if height == 0.0 and positions:
        height = max(position[1] for position in positions.values())
    return RestPose(order=order, local=local, world=world, positions=positions,
                    parent=parent, height_m=height, hip_height_m=hip_height)


def resolve_roles(bone_names: list[str]) -> dict[str, str]:
    """Best (highest-confidence, first-seen) bone name per canonical role."""
    mappings = load_mappings()
    best: dict[str, tuple[float, str]] = {}
    for name in bone_names:
        role, confidence = role_for(name, mappings)
        if not role:
            continue
        current = best.get(role)
        if current is None or confidence > current[0]:
            best[role] = (confidence, name)
    return {role: name for role, (_, name) in best.items()}


def _curve_value_at(curve_times: list[float], values: list[float], t: float) -> float:
    if not curve_times:
        return 0.0
    if t <= curve_times[0]:
        return values[0]
    if t >= curve_times[-1]:
        return values[-1]
    lo, hi = 0, len(curve_times) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if curve_times[mid] <= t:
            lo = mid
        else:
            hi = mid
    span = curve_times[hi] - curve_times[lo]
    if span <= 0:
        return values[hi]
    factor = (t - curve_times[lo]) / span
    return values[lo] + (values[hi] - values[lo]) * factor


class ClipSampler:
    """Evaluate one animation stack at arbitrary times (real curve data only)."""

    def __init__(self, doc: FBXDocument, stack: FBXStack) -> None:
        self.doc = doc
        self.stack = stack
        self.rest = rest_pose(doc)
        self._curves: dict[int, tuple[list[float], list[float]]] = {}
        for uid, curve in doc.curves.items():
            self._curves[uid] = ([tick / TICKS_PER_SECOND for tick in curve.key_times],
                                 curve.values)
        self._by_uid = {model.uid: model for model in doc.models}
        self.bone_names = [model.name for model in hierarchy_order(doc)]

    # -- timing ----------------------------------------------------------- #
    def key_times(self) -> list[float]:
        times: set[float] = set()
        for model_uid in self.doc.animated_channels:
            for axis_map in self.doc.animated_channels[model_uid].values():
                for uid, _default in axis_map.values():
                    curve = self._curves.get(uid)
                    if curve:
                        times.update(curve[0])
        return sorted(times)

    def time_range(self) -> tuple[float, float]:
        if self.stack.stop_tick > self.stack.start_tick:
            return (self.stack.start_tick / TICKS_PER_SECOND,
                    self.stack.stop_tick / TICKS_PER_SECOND)
        times = self.key_times()
        return (times[0], times[-1]) if times else (0.0, 0.0)

    def sample_times(self, max_samples: int = MAX_SAMPLES) -> list[float]:
        start, end = self.time_range()
        times = [t for t in self.key_times() if start - 1e-9 <= t <= end + 1e-9]
        if not times:
            return [start]
        if len(times) > max_samples:
            step = (len(times) - 1) / (max_samples - 1)
            times = [times[round(i * step)] for i in range(max_samples)]
        if times[0] > start + 1e-9:
            times.insert(0, start)
        if times[-1] < end - 1e-9:
            times.append(end)
        return times

    # -- evaluation ------------------------------------------------------- #
    def _channels(self, model: FBXModel, prop: str, t: float) -> list[float | None]:
        channels = self.doc.animated_channels.get(model.uid, {})
        axes = channels.get(prop, {})
        resolved: list[float | None] = [None, None, None]
        for index, axis in enumerate(("d|X", "d|Y", "d|Z")):
            entry = axes.get(axis)
            if not entry:
                continue
            uid, default = entry
            curve = self._curves.get(uid)
            if curve is None:
                resolved[index] = default
                continue
            resolved[index] = _curve_value_at(curve[0], curve[1], t)
        return resolved

    def local_matrix(self, model: FBXModel, t: float, animate: bool = True) -> list[list[float]]:
        scale = self.doc.scale_to_metres
        translation = list(model.translation)
        rotation = list(model.rotation)
        scaling = list(model.scaling)
        if animate:
            for index, value in enumerate(self._channels(model, "Lcl Translation", t)):
                if value is not None:
                    translation[index] = value
            for index, value in enumerate(self._channels(model, "Lcl Rotation", t)):
                if value is not None:
                    rotation[index] = value
            for index, value in enumerate(self._channels(model, "Lcl Scaling", t)):
                if value is not None:
                    scaling[index] = value
        matrix = tf.multiply(
            tf.translation(*(v * scale for v in translation)),
            tf.multiply(tf.euler_to_matrix(model.pre_rotation, model.rotation_order),
                        tf.euler_to_matrix(rotation, model.rotation_order)),
        )
        return tf.multiply(matrix, tf.scaling(*scaling))

    def world_matrices(self, t: float, animate: bool = True) -> dict[str, list[list[float]]]:
        world: dict[str, list[list[float]]] = {}
        for model in hierarchy_order(self.doc):
            parent_model = self._by_uid.get(self.doc.parent_of.get(model.uid, -1))
            local = self.local_matrix(model, t, animate)
            world[model.name] = (tf.multiply(world[parent_model.name], local)
                                 if parent_model and parent_model.name in world else local)
        return world

    def rotations(self, t: float) -> dict[str, list[float]]:
        return {name: tf.quat_from_matrix(matrix)
                for name, matrix in self.world_matrices(t).items()}


# --------------------------------------------------------------------------- #
# Features
# --------------------------------------------------------------------------- #
def _distance(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def _horizontal(a: list[float], b: list[float]) -> float:
    return math.hypot(a[0] - b[0], a[2] - b[2])


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _angle_to_up(vector: list[float]) -> float:
    length = math.sqrt(sum(v * v for v in vector))
    if length < 1e-9:
        return 0.0
    cosine = max(-1.0, min(1.0, vector[1] / length))
    return math.degrees(math.acos(cosine))


def motion_features(doc: FBXDocument, stack: FBXStack) -> dict[str, Any]:
    """Measured motion descriptors for one FBX clip. Nothing is inferred from names."""
    return features_from_sampler(ClipSampler(doc, stack), doc.fps)


def features_from_sampler(sampler: Any, fps: float = 30.0) -> dict[str, Any]:
    """Measured motion descriptors for any clip sampler (FBX, BVH or glTF)."""
    rest = sampler.rest
    roles = resolve_roles(sampler.bone_names)
    times = sampler.sample_times()
    duration = max(0.0, sampler.time_range()[1] - sampler.time_range()[0])
    features: dict[str, Any] = {
        "duration_seconds": round(duration, 6),
        "sample_count": len(times),
        "frame_rate": round(fps, 4),
        "units": "metres",
        "roles": roles,
        "rest": {"hip_height_m": round(rest.hip_height_m, 6),
                 "height_m": round(rest.height_m, 6)},
    }
    if len(times) < 2:
        features["insufficient_samples"] = True
        return features

    samples: list[dict[str, list[float]]] = []
    rotations: list[dict[str, list[float]]] = []
    for t in times:
        world = sampler.world_matrices(t)
        samples.append({name: tf.position_of(matrix) for name, matrix in world.items()})
        rotations.append({name: tf.quat_from_matrix(matrix) for name, matrix in world.items()})

    role_positions = {
        role: [sample[bone] for sample in samples if bone in sample]
        for role, bone in roles.items()
    }

    # The floor reference is the rest pose, not the per-clip minimum: a clip that
    # is airborne start-to-finish must still measure as airborne. For the uploaded
    # rigs the rest toes sit exactly on y=0.
    rest_foot_roles = [role for role in ("left_toe", "right_toe", "left_foot", "right_foot")
                       if roles.get(role) in rest.positions]
    ground = min((rest.positions[roles[role]][1] for role in rest_foot_roles), default=0.0)
    features["ground_y_m"] = round(ground, 6)
    features["ground_reference"] = "rest_pose_foot_plane"

    hips = role_positions.get("hips") or [[0.0, 0.0, 0.0]] * len(samples)
    hip_heights = [position[1] - ground for position in hips]
    features["hip_height"] = {"mean_m": round(_mean(hip_heights), 6),
                              "min_m": round(min(hip_heights), 6),
                              "max_m": round(max(hip_heights), 6)}

    # Root motion: horizontal travel of the hips, plus direction of travel.
    travel = [_horizontal(hips[0], position) for position in hips]
    path = sum(_distance(hips[i], hips[i + 1]) for i in range(len(hips) - 1))
    net = travel[-1]
    delta = [hips[-1][0] - hips[0][0], hips[-1][2] - hips[0][2]]
    direction = math.degrees(math.atan2(delta[0], delta[1])) if net > 1e-6 else None
    step_speeds = []
    for i in range(1, len(hips)):
        dt = times[i] - times[i - 1]
        if dt > 1e-6:
            step_speeds.append(_distance(hips[i], hips[i - 1]) / dt)
    # 90th percentile rather than the absolute maximum: a single noisy key must
    # not turn a crouch-walk into a run. Sustained transitions still register.
    peak_speed = 0.0
    if step_speeds:
        ordered = sorted(step_speeds)
        peak_speed = ordered[min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))]
    features["root_motion"] = {
        "has_root_motion": bool(net >= 0.05),
        "peak_speed_mps": round(peak_speed, 4),
        "net_horizontal_m": round(net, 6),
        "path_horizontal_m": round(path, 6),
        "direction_deg": round(direction, 2) if direction is not None else None,
        "vertical_range_m": round(max(p[1] for p in hips) - min(p[1] for p in hips), 6),
        "speed_mps": round(net / duration, 4) if duration > 0 else 0.0,
    }

    # Feet: lift above the lowest contact point, alternation and contact share.
    foot_stats: dict[str, Any] = {}
    lifts: list[tuple[float, float]] = []
    for role in ("left_toe", "right_toe", "left_foot", "right_foot"):
        if role not in role_positions:
            continue
        heights = [position[1] - ground for position in role_positions[role]]
        foot_stats[role] = {"min_m": round(min(heights), 6), "max_m": round(max(heights), 6),
                            "contact_fraction": round(
                                sum(1 for h in heights if h <= 0.05) / len(heights), 4)}
        if role.endswith("toe"):
            lifts.append((role, max(heights)))
    alternation = 0
    if "left_toe" in role_positions and "right_toe" in role_positions:
        lower_is_left = [left[1] <= right[1] for left, right in
                         zip(role_positions["left_toe"], role_positions["right_toe"])]
        alternation = sum(1 for i in range(1, len(lower_is_left)) if lower_is_left[i] != lower_is_left[i - 1])
        airborne = [min(left[1], right[1]) - ground > 0.12 for left, right in
                    zip(role_positions["left_toe"], role_positions["right_toe"])]
        features["airborne_fraction"] = round(sum(airborne) / len(airborne), 4)
    stride = 0.0
    for side in ("left", "right"):
        toe_role, hip_source = f"{side}_toe", hips
        if toe_role not in role_positions:
            continue
        forward = [position[2] - hip[2] for position, hip in zip(role_positions[toe_role], hip_source)]
        stride += (max(forward) - min(forward)) / 2.0
    features["feet"] = {"per_role": foot_stats, "alternation_count": alternation,
                        "max_toe_lift_m": round(max((v for _, v in lifts), default=0.0), 6),
                        "stride_amplitude_m": round(stride, 6)}

    # Torso orientation: hips -> chest, independent of bone axis conventions.
    chest_positions = role_positions.get("chest") or role_positions.get("spine") or hips
    tilts = []
    for hip, chest in zip(hips, chest_positions):
        tilts.append(_angle_to_up([chest[0] - hip[0], chest[1] - hip[1], chest[2] - hip[2]]))
    head_positions = role_positions.get("head") or chest_positions
    head_above = [head[1] - hip[1] for head, hip in zip(head_positions, hips)]
    features["torso"] = {
        "tilt_deg_mean": round(_mean(tilts), 3),
        "tilt_deg_end": round(tilts[-1], 3),
        "tilt_deg_max": round(max(tilts), 3),
        "head_above_hips_end_m": round(head_above[-1], 6),
        "head_above_hips_min_m": round(min(head_above), 6),
    }

    # Hands: forward/side/height relative to the hips midline.
    hand_info: dict[str, Any] = {}
    for side in ("left", "right"):
        role = f"{side}_hand"
        if role not in role_positions:
            continue
        forward = [position[2] - hip[2] for position, hip in zip(role_positions[role], hips)]
        sideways = [position[0] - hip[0] for position, hip in zip(role_positions[role], hips)]
        above = [position[1] - hip[1] for position, hip in zip(role_positions[role], hips)]
        hand_info[side] = {
            "forward_mean_m": round(_mean(forward), 6),
            "forward_max_m": round(max(forward), 6),
            "side_mean_m": round(_mean(sideways), 6),
            "above_hips_mean_m": round(_mean(above), 6),
            "above_hips_max_m": round(max(above), 6),
        }
    if "left" in hand_info and "right" in hand_info:
        separations = [_distance(left, right) for left, right in
                       zip(role_positions["left_hand"], role_positions["right_hand"])]
        hand_info["separation_mean_m"] = round(_mean(separations), 6)
        hand_info["separation_min_m"] = round(min(separations), 6)
        hand_info["both_forward_min_m"] = round(min(
            min(info["forward_mean_m"] for info in (hand_info["left"], hand_info["right"])), 0.0), 6)
        hand_info["min_forward_m"] = round(min(
            min(left[2] - hip[2], right[2] - hip[2])
            for left, right, hip in zip(role_positions["left_hand"],
                                        role_positions["right_hand"], hips)), 6)
    features["hands"] = hand_info

    # Facing vs travel: measured, so forward/backward/strafe is not guessed.
    facing_vector = [0.0, 0.0]
    if "left_toe" in role_positions and "right_toe" in role_positions:
        for left, right, hip in zip(role_positions["left_toe"], role_positions["right_toe"], hips):
            facing_vector[0] += (left[0] + right[0]) / 2.0 - hip[0]
            facing_vector[1] += (left[2] + right[2]) / 2.0 - hip[2]
    facing_deg = None
    if abs(facing_vector[0]) + abs(facing_vector[1]) > 1e-9:
        facing_deg = math.degrees(math.atan2(facing_vector[0], facing_vector[1]))
    travel_deg = features["root_motion"]["direction_deg"]
    relative = None
    if facing_deg is not None and travel_deg is not None:
        relative = (travel_deg - facing_deg + 180.0) % 360.0 - 180.0
    features["facing"] = {"facing_deg": round(facing_deg, 2) if facing_deg is not None else None,
                          "travel_deg": travel_deg,
                          "travel_vs_facing_deg": round(relative, 2) if relative is not None else None}

    # Motion energy and loop closure from bone rotations.
    angle_steps = []
    for i in range(1, len(rotations)):
        per_bone = [tf.quat_angle_between(rotations[i - 1].get(name, [0, 0, 0, 1]),
                                          rotations[i].get(name, [0, 0, 0, 1]))
                    for name in roles.values() if name in rotations[i]]
        if per_bone:
            angle_steps.append(_mean(per_bone))
    loop_angles = [tf.quat_angle_between(rotations[0].get(name, [0, 0, 0, 1]),
                                         rotations[-1].get(name, [0, 0, 0, 1]))
                   for name in roles.values() if name in rotations[0]]
    features["motion"] = {
        "mean_step_deg": round(_mean(angle_steps), 4),
        "max_step_deg": round(max(angle_steps), 4) if angle_steps else 0.0,
        "pose_change_deg_per_s": round(_mean(angle_steps) / max(1e-6, duration / max(1, len(angle_steps))), 3),
        "loop_error_deg": round(max(loop_angles), 4) if loop_angles else 0.0,
        "max_bone_travel_m": round(max(
            (_distance(samples[0][name], samples[-1][name]) for name in roles.values()
             if name in samples[0] and name in samples[-1]), default=0.0), 6),
        "mean_bone_travel_m": round(_mean([
            _distance(samples[0][name], samples[-1][name]) for name in roles.values()
            if name in samples[0] and name in samples[-1]]), 6),
    }

    # Yaw change of the hips (turning in place / turns).
    hips_rotations = [rotation.get(roles.get("hips", ""), None) for rotation in rotations]
    hips_rotations = [q for q in hips_rotations if q]
    if hips_rotations:
        features["yaw_change_deg"] = round(
            tf.quat_angle_between(hips_rotations[0], hips_rotations[-1]), 3)
    features["hips_vertical_oscillation_m"] = round(
        max(hip_heights) - min(hip_heights), 6)
    return features
