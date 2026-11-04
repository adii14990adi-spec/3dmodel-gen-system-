"""Clip samplers for BVH and glTF/GLB sources.

:func:`processing.motion.motion_features` only needs an object exposing
``bone_names``, ``rest``, ``time_range()``, ``sample_times()`` and
``world_matrices(t)``. The FBX source provides that through
``processing.motion.ClipSampler``; this module provides the same contract for
BVH and glTF so every supported format is classified from real motion data
instead of file names.

Nothing here invents data: times come from the file's own key frames, and
missing channels fall back to the rest transform that the file declares.
"""
from __future__ import annotations

import math
from typing import Any

from processing import transform as tf
from processing.bvh import BVHDocument
from processing.motion import MAX_SAMPLES, RestPose, resolve_roles


def _rest_from_world(world: dict[str, list[list[float]]], parent: dict[str, str | None],
                     order: list[str]) -> RestPose:
    positions = {name: tf.position_of(world[name]) for name in order}
    local = {name: [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
             for name in order}
    roles = resolve_roles(order)
    hip_height = positions[roles["hips"]][1] if roles.get("hips") in positions else 0.0
    height = max((position[1] for position in positions.values()), default=0.0)
    return RestPose(order=order, local=local, world=world, positions=positions,
                    parent=parent, height_m=height, hip_height_m=hip_height)


class BVHSampler:
    """Forward kinematics over a parsed BVH document (Y-up, metres)."""

    def __init__(self, doc: BVHDocument, unit_scale: float | None = None) -> None:
        self.doc = doc
        self.bone_names = list(doc.joints.keys())
        self.parent = {name: joint.parent for name, joint in doc.joints.items()}
        magnitude = max((math.sqrt(sum(v * v for v in joint.offset))
                         for joint in doc.joints.values()), default=0.0)
        # BVH carries no unit metadata. A character whose longest joint offset is
        # > 5 units is centimetres (a metre-scale rig would be ~0.5).
        self.unit_scale = unit_scale if unit_scale is not None else (0.01 if magnitude > 5 else 1.0)
        self._times = [i * doc.frame_time for i in range(doc.frame_count)]
        self.rest = _rest_from_world(self.world_matrices(0.0, animate=False),
                                     self.parent, self.bone_names)

    # -- contract --------------------------------------------------------- #
    def time_range(self) -> tuple[float, float]:
        return (self._times[0], self._times[-1]) if self._times else (0.0, 0.0)

    def sample_times(self, max_samples: int = MAX_SAMPLES) -> list[float]:
        times = list(self._times)
        if len(times) > max_samples:
            step = (len(times) - 1) / (max_samples - 1)
            times = [times[round(i * step)] for i in range(max_samples)]
        return times

    def world_matrices(self, t: float, animate: bool = True) -> dict[str, list[list[float]]]:
        index = 0
        if self._times:
            index = min(range(len(self._times)), key=lambda i: abs(self._times[i] - t))
        world: dict[str, list[list[float]]] = {}
        for name in self.bone_names:
            joint = self.doc.joints[name]
            offset = [v * self.unit_scale for v in joint.offset]
            if animate:
                if joint.parent is None and any(
                        ch.lower() in ("xposition", "yposition", "zposition") for ch in joint.channels):
                    # Root translation channels carry the absolute root position (the
                    # offsets are the rest pose), which is how the retargeter reads
                    # them; adding the offset would double-count the hip height.
                    offset = [self.doc.channel(name, f"{axis}position", index) * self.unit_scale
                              for axis in ("x", "y", "z")]
                else:
                    for axis in ("x", "y", "z"):
                        position_channel = f"{axis}position"
                        if any(ch.lower() == position_channel for ch in joint.channels):
                            offset["xyz".index(axis)] += (
                                self.doc.channel(name, position_channel, index) * self.unit_scale)
            rotation_channels = [ch for ch in joint.channels if ch.lower().endswith("rotation")]
            rotation = [0.0, 0.0, 0.0]
            if animate and rotation_channels:
                values = {"x": 0.0, "y": 0.0, "z": 0.0}
                for channel in rotation_channels:
                    values[channel[0].lower()] = self.doc.channel(name, channel, index)
                rotation = [values["x"], values["y"], values["z"]]
            order = "".join(ch[0].upper() for ch in rotation_channels) or "XYZ"
            local = tf.multiply(tf.translation(*offset),
                                tf.euler_to_matrix(rotation, order))
            parent = self.parent.get(name)
            world[name] = tf.multiply(world[parent], local) if parent and parent in world else local
        return world


class GltfSampler:
    """Sample the first animation of a glTF/GLB document (Y-up, metres)."""

    def __init__(self, asset: dict[str, Any], animation_index: int = 0) -> None:
        from processing.glb import read_accessor
        self.doc, self.binary = asset["json"], asset["bin"]
        self.nodes = self.doc.get("nodes", [])
        animations = self.doc.get("animations", [])
        self.animation = animations[animation_index] if animations else {}
        self.parent: dict[int, int] = {}
        for index, node in enumerate(self.nodes):
            for child in node.get("children", []):
                self.parent[child] = index
        # Bone list: skin joints when present, otherwise every node.
        skins = self.doc.get("skins", [])
        joint_indices = sorted({int(j) for skin in skins for j in skin.get("joints", [])}) \
            or list(range(len(self.nodes)))
        self.joint_indices = [i for i in joint_indices if i < len(self.nodes) and self.nodes[i].get("name")]
        self.bone_names = [self.nodes[i]["name"] for i in self.joint_indices]
        self.name_to_index = {name: i for i, name in zip(self.joint_indices, self.bone_names)}

        # Animated channels per (node, path).
        self.channels: dict[tuple[int, str], list[Any]] = {}
        times: set[float] = set()
        for channel in self.animation.get("channels", []):
            target = channel.get("target") or {}
            sampler = self.animation.get("samplers", [])[channel.get("sampler", 0)]
            node, path = target.get("node"), target.get("path")
            if node is None or path not in ("translation", "rotation", "scale"):
                continue
            try:
                inputs = read_accessor(self.doc, self.binary, sampler["input"])
                outputs = read_accessor(self.doc, self.binary, sampler["output"])
            except (KeyError, IndexError, ValueError):
                continue
            self.channels[(int(node), path)] = [list(map(float, inputs)), outputs]
            times.update(float(v) for v in inputs)
        self._times = sorted(times) or [0.0]
        self.rest = _rest_from_world(self.world_matrices(self._times[0], animate=False),
                                     {name: (self.nodes[self.parent[name_to]].get("name")
                                             if name_to in self.parent else None)
                                      for name, name_to in
                                      ((self.nodes[i]["name"], i) for i in self.joint_indices)},
                                     self.bone_names)

    # -- contract --------------------------------------------------------- #
    def time_range(self) -> tuple[float, float]:
        return (self._times[0], self._times[-1])

    def sample_times(self, max_samples: int = MAX_SAMPLES) -> list[float]:
        times = list(self._times)
        if len(times) > max_samples:
            step = (len(times) - 1) / (max_samples - 1)
            times = [times[round(i * step)] for i in range(max_samples)]
        return times

    def _value(self, node: int, path: str, t: float, default: list[float]) -> list[float]:
        entry = self.channels.get((node, path))
        if not entry:
            return default
        times, outputs = entry
        if not times:
            return default
        if t <= times[0]:
            return list(outputs[0])
        if t >= times[-1]:
            return list(outputs[-1])
        lo, hi = 0, len(times) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if times[mid] <= t:
                lo = mid
            else:
                hi = mid
        span = times[hi] - times[lo]
        factor = (t - times[lo]) / span if span > 0 else 0.0
        a, b = outputs[lo], outputs[hi]
        if path == "rotation":
            return list(tf.normalize_quat([a[i] + (b[i] - a[i]) * factor for i in range(4)]))
        return [a[i] + (b[i] - a[i]) * factor for i in range(len(a))]

    def world_matrices(self, t: float, animate: bool = True) -> dict[str, list[list[float]]]:
        world_by_index: dict[int, list[list[float]]] = {}

        def local_of(index: int) -> list[list[float]]:
            node = self.nodes[index]
            translation = list(node.get("translation", [0.0, 0.0, 0.0]))
            rotation = list(node.get("rotation", [0.0, 0.0, 0.0, 1.0]))
            scale = list(node.get("scale", [1.0, 1.0, 1.0]))
            if animate:
                translation = self._value(index, "translation", t, translation)
                rotation = self._value(index, "rotation", t, rotation)
                scale = self._value(index, "scale", t, scale)
            x, y, z, w = rotation
            rotation_matrix = [
                [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0.0],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0.0],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
            return tf.multiply(tf.translation(*translation),
                               tf.multiply(rotation_matrix, tf.scaling(*scale)))

        def compose(index: int, guard: int = 0) -> list[list[float]]:
            if index in world_by_index:
                return world_by_index[index]
            if guard > 512:
                return tf.identity()
            parent = self.parent.get(index)
            matrix = (tf.multiply(compose(parent, guard + 1), local_of(index))
                      if parent is not None else local_of(index))
            world_by_index[index] = matrix
            return matrix

        for index in range(len(self.nodes)):
            compose(index)
        return {self.nodes[i]["name"]: world_by_index[i] for i in self.joint_indices
                if i in world_by_index}
