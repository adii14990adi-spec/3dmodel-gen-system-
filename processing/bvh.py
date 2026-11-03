"""Real Biovision BVH parser: hierarchy, channels, frame count, fps, rest offsets."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BVHJoint:
    name: str
    parent: str | None
    offset: list[float]
    channels: list[str]
    children: list[str] = field(default_factory=list)
    depth: int = 0


@dataclass
class BVHDocument:
    joints: dict[str, BVHJoint]
    order: list[str]
    root: str | None
    frames: list[list[float]]
    frame_time: float
    channel_offsets: dict[str, int]

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def fps(self) -> float:
        return 1.0 / self.frame_time if self.frame_time > 0 else 0.0

    @property
    def duration(self) -> float:
        return self.frame_count * self.frame_time

    def channel(self, joint: str, kind: str, frame: int) -> float:
        j = self.joints.get(joint)
        if j is None or frame >= len(self.frames):
            return 0.0
        base = self.channel_offsets.get(joint, 0)
        for i, ch in enumerate(j.channels):
            if ch.lower() == kind.lower():
                return self.frames[frame][base + i]
        return 0.0

    def position_channels(self, frame: int) -> list[float]:
        j = self.joints.get(self.root or "")
        if j is None:
            return [0.0, 0.0, 0.0]
        base = 0
        out = [0.0, 0.0, 0.0]
        for i, ch in enumerate(j.channels):
            if ch.lower() in ("xposition", "yposition", "zposition"):
                out["xyz".index(ch[0].lower())] = self.frames[frame][base + i]
        return out

    def euler_rotation(self, joint: str, frame: int) -> list[float]:
        return [self.channel(joint, "Xrotation", frame),
                self.channel(joint, "Yrotation", frame),
                self.channel(joint, "Zrotation", frame)]


class BVHError(ValueError):
    pass


def parse_bvh(text: str) -> BVHDocument:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0].upper() != "HIERARCHY":
        raise BVHError("BVH must start with HIERARCHY")
    joints: dict[str, BVHJoint] = {}
    order: list[str] = []
    root: str | None = None
    stack: list[tuple[str, int]] = []
    i = 1
    channel_index = 0
    channel_offsets: dict[str, int] = {}
    while i < len(lines):
        parts = lines[i].split()
        head = parts[0].upper()
        if head in ("ROOT", "JOINT"):
            name = " ".join(parts[1:]).strip()
            if not name:
                raise BVHError(f"unnamed {head} at line {i}")
            if name in joints:
                raise BVHError(f"duplicate joint name {name}")
            parent = stack[-1][0] if stack else None
            depth = len(stack)
            if parent is None:
                root = name
            joints[name] = BVHJoint(name, parent, [0, 0, 0], [], [], depth)
            order.append(name)
            if parent:
                joints[parent].children.append(name)
            i += 1
            if i >= len(lines) or lines[i] != "{":
                raise BVHError(f"expected '{{' after {name}")
            stack.append((name, depth))
            i += 1
        elif head == "END" and "SITE" in [p.upper() for p in parts]:
            if not stack:
                raise BVHError("END SITE without an open joint")
            i += 1
            if i >= len(lines) or lines[i] != "{":
                raise BVHError("expected '{' after END SITE")
            i += 2  # brace + OFFSET line (end sites carry no channels)
            if i < len(lines) and lines[i] == "}":
                i += 1
            continue
        elif head == "OFFSET" and stack:
            try:
                joints[stack[-1][0]].offset = [float(x) for x in parts[1:4]]
            except ValueError as exc:
                raise BVHError(f"invalid OFFSET at line {i}") from exc
            i += 1
        elif head == "CHANNELS" and stack:
            try:
                count = int(parts[1])
            except (ValueError, IndexError) as exc:
                raise BVHError(f"invalid CHANNELS count at line {i}") from exc
            chans = parts[2:2 + count]
            if len(chans) != count:
                raise BVHError(f"CHANNELS declares {count} but lists {len(chans)}")
            name = stack[-1][0]
            joints[name].channels = chans
            channel_offsets[name] = channel_index
            channel_index += count
            i += 1
        elif head == "}":
            if not stack:
                raise BVHError("unmatched closing brace")
            stack.pop()
            i += 1
            if not stack:
                break
        elif head == "MOTION":
            break
        else:
            raise BVHError(f"unexpected BVH token at line {i}: {parts[0]}")
    frames: list[list[float]] = []
    frame_time = 1.0 / 30.0
    j = i
    while j < len(lines) and lines[j].upper() != "MOTION":
        j += 1
    if j == len(lines):
        raise BVHError("BVH has no MOTION section")
    j += 1
    while j < len(lines):
        parts = lines[j].split()
        key = parts[0].lower()
        if key == "frames:":
            pass
        elif key == "frame" and len(parts) >= 3 and parts[1].lower() == "time:":
            try:
                frame_time = float(parts[2])
            except ValueError as exc:
                raise BVHError("invalid Frame Time") from exc
        elif parts and _is_float(parts[0]):
            try:
                frames.append([float(x) for x in parts])
            except ValueError as exc:
                raise BVHError(f"invalid motion frame at line {j}") from exc
        j += 1
    if channel_index == 0:
        raise BVHError("BVH defines no channels")
    for f in frames:
        if len(f) != channel_index:
            raise BVHError(f"frame has {len(f)} values, expected {channel_index}")
    return BVHDocument(joints, order, root, frames, frame_time, channel_offsets)


def _is_float(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def load_bvh(path: Path | str) -> BVHDocument:
    return parse_bvh(Path(path).read_text(encoding="utf-8", errors="replace"))


def skeleton_summary(doc: BVHDocument) -> dict:
    return {
        "present": True, "skeleton_count": 1,
        "root_bone": doc.root, "bone_count": len(doc.joints),
        "bones": [{"name": name, "index": i, "parent": j.parent, "children": j.children,
                   "translation": j.offset, "rotation": [0, 0, 0, 1], "scale": [1, 1, 1],
                   "global_matrix": _compose(j.offset), "length": math.sqrt(sum(v*v for v in j.offset))}
                  for i, (name, j) in enumerate(doc.joints.items())],
        "skins": [], "humanoid": False, "humanoid_confidence": 0.0,
    }


def _compose(t: list[float]) -> list[float]:
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, t[0], t[1], t[2], 1]
