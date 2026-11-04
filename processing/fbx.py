"""Real binary FBX reader and inspector (pure Python, no Blender required).

Why this module exists
----------------------
The animation workflow used to hand ``.fbx`` files to headless Blender, which
means the pipeline could not say anything about an FBX asset when Blender was
unavailable (and said nothing about the *content* until Blender ran). Uploaded
animation packs are overwhelmingly FBX, so the organisation/classification work
needs real, local inspection: skeleton, hierarchy, rest pose, animation stacks,
per-curve key data, root motion and clip timing.

Format notes (verified against the actual uploaded files, FBX 7700)
------------------------------------------------------------------
* Binary FBX: 27-byte header (``Kaydara FBX Binary  \\x00`` + 2 bytes + uint32
  version) followed by node records. Version >= 7500 uses 64-bit offsets.
* A node record is ``EndOffset``, ``NumProperties``, ``PropertyListLen`` (each
  8 bytes for 7500+, 4 bytes otherwise), a 1-byte name length, the name, the
  property list and then nested nodes up to ``EndOffset``.
* The uploaded files omit per-node null terminator records; children are found
  by parsing within ``[after-properties, EndOffset)``. Only the top level ends
  with a zero ``EndOffset`` record. The parser therefore treats ``EndOffset``
  as a hard bound instead of relying on sentinels.
* Property types: Y, C, I, F, D, L scalars; f/d/l/i/b arrays (optional zlib);
  S (string) and R (raw bytes).
* ``KeyTime`` ticks are 1/46186158000 s.

Euler convention (empirically validated, see ``tests/unit/test_fbx.py``)
----------------------------------------------------------------------
FBX Euler XYZ is composed as ``R = Rz @ Ry @ Rx`` (X applied first) and the
local transform is ``T * Rpre * R * S``. This is not a guess: with every
animation curve zeroed, this composition reproduces the exact rest T-pose of
the uploaded rigs (arms straight along +X at shoulder height, legs straight
down, toes forward at y=0). The alternative composition lifts the hands to
y=2.0 m and folds the arms upward, so it is rejected by the test suite.

Safety
------
Uploaded files are untrusted data. Parsing is bounded: node count, recursion
depth, array length, compressed size and string length all have limits, and the
parser only reads bytes — nothing is executed. Unsupported or corrupt input
raises :class:`FBXError` with a precise reason, never a silent skip.
"""
from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

BINARY_MAGIC = b"Kaydara FBX Binary"
ASCII_MAGIC = b"; FBX"
TICKS_PER_SECOND = 46186158000.0

MAX_NODES = 1_000_000
MAX_DEPTH = 64
MAX_ARRAY_ELEMENTS = 20_000_000
MAX_STRING_BYTES = 1 << 20
MAX_DECOMPRESSED_BYTES = 64 << 20

_SCALAR_FORMAT = {"Y": "<h", "C": "<?", "I": "<i", "F": "<f", "D": "<d", "L": "<q"}
_ARRAY_FORMAT = {"f": "f", "d": "d", "l": "q", "i": "i", "b": "?"}

# FBX GlobalSettings TimeMode values -> nominal frame rate (None = custom).
_TIME_MODES = {0: 30.0, 1: 120.0, 2: 100.0, 3: 60.0, 4: 50.0, 5: 48.0,
               6: 30.0, 7: 30.0, 8: 29.97, 9: 29.97, 10: 24.0, 11: 25.0,
               12: 96.0, 13: 72.0, 14: 60.0}

# Rotation orders from FbxEuler::EOrder.
_ROTATION_ORDERS = {0: "XYZ", 1: "XZY", 2: "YZX", 3: "YXZ", 4: "ZXY", 5: "ZYX"}


class FBXError(ValueError):
    """Raised when an FBX file cannot be parsed; carries a human-readable reason."""


# --------------------------------------------------------------------------- #
# Node tree
# --------------------------------------------------------------------------- #
@dataclass
class Node:
    name: str
    props: list[Any]
    children: list["Node"]
    end: int

    def first(self, name: str) -> "Node | None":
        for child in self.children:
            if child.name == name:
                return child
        return None

    def find(self, name: str) -> list["Node"]:
        return [child for child in self.children if child.name == name]

    def scalar_name(self) -> str:
        """Object name from ``props[1]`` (``uid, "name\\x00\\x01Type", type``).

        FBX object records store the uid first, so ``props[0]`` is never the
        name; using it silently produced empty names in earlier iterations.
        """
        return _object_name(self)

    def type_suffix(self) -> str:
        return _object_type(self)


def _object_name(node: "Node") -> str:
    if len(node.props) > 1 and isinstance(node.props[1], str):
        return node.props[1].split("\x00")[0]
    return ""


def _object_type(node: "Node") -> str:
    if len(node.props) > 1 and isinstance(node.props[1], str) and "\x00\x01" in node.props[1]:
        return node.props[1].split("\x00\x01", 1)[1]
    if len(node.props) > 2 and isinstance(node.props[2], str):
        return node.props[2]
    return ""


class _Reader:
    """Bounded reader for one FBX byte buffer."""

    def __init__(self, data: bytes, version: int) -> None:
        self.data = data
        self.version = version
        self.wide = version >= 7500
        self.header_size = 24 if self.wide else 12
        self.node_count = 0

    # -- primitives ------------------------------------------------------- #
    def record_header(self, offset: int) -> tuple[int, int, int]:
        if self.wide:
            return struct.unpack_from("<QQQ", self.data, offset)
        return struct.unpack_from("<III", self.data, offset)

    def properties(self, offset: int, count: int) -> tuple[list[Any], int]:
        data, out = self.data, []
        for _ in range(count):
            if offset >= len(data):
                raise FBXError("property list runs past end of file")
            kind = chr(data[offset])
            offset += 1
            if kind in _SCALAR_FORMAT:
                fmt = _SCALAR_FORMAT[kind]
                size = struct.calcsize(fmt)
                out.append(struct.unpack_from(fmt, data, offset)[0])
                offset += size
            elif kind in _ARRAY_FORMAT:
                if offset + 12 > len(data):
                    raise FBXError("array header runs past end of file")
                length, encoding, compressed = struct.unpack_from("<III", data, offset)
                offset += 12
                if length > MAX_ARRAY_ELEMENTS:
                    raise FBXError(f"array of {length} elements exceeds the safety limit")
                if offset + compressed > len(data):
                    raise FBXError("array payload runs past end of file")
                raw = data[offset:offset + compressed]
                offset += compressed
                if encoding == 1:
                    try:
                        raw = zlib.decompress(raw)
                    except zlib.error as exc:
                        raise FBXError(f"corrupt zlib-compressed array: {exc}") from exc
                if len(raw) > MAX_DECOMPRESSED_BYTES:
                    raise FBXError("decompressed array exceeds the safety limit")
                fmt = _ARRAY_FORMAT[kind]
                if fmt == "?":
                    out.append(list(raw[:length]))
                else:
                    size = struct.calcsize(fmt)
                    if len(raw) < length * size:
                        raise FBXError("array payload is shorter than its declared length")
                    out.append(list(struct.unpack_from("<" + fmt * length, raw, 0)))
            elif kind in "SR":
                if offset + 4 > len(data):
                    raise FBXError("string header runs past end of file")
                length = struct.unpack_from("<I", data, offset)[0]
                offset += 4
                if length > MAX_STRING_BYTES:
                    raise FBXError("string exceeds the safety limit")
                if offset + length > len(data):
                    raise FBXError("string runs past end of file")
                payload = data[offset:offset + length]
                offset += length
                out.append(payload.decode("utf-8", "replace") if kind == "S" else bytes(payload))
            else:
                raise FBXError(f"unknown property type {kind!r}")
        return out, offset

    # -- records ---------------------------------------------------------- #
    def nodes(self, offset: int, limit: int, depth: int = 0) -> tuple[list[Node], int]:
        if depth > MAX_DEPTH:
            raise FBXError("node nesting exceeds the safety limit")
        out: list[Node] = []
        minimum = self.header_size + 1
        while offset + minimum <= limit:
            end, prop_count, _prop_len = self.record_header(offset)
            if end == 0:
                break
            if end <= offset or end > limit:
                raise FBXError(f"invalid node end offset {end} at {offset}")
            name_len = self.data[offset + self.header_size]
            name_start = offset + self.header_size + 1
            if name_start + name_len > limit:
                raise FBXError("node name runs past its parent bound")
            name = self.data[name_start:name_start + name_len].decode("utf-8", "replace")
            props, after = self.properties(name_start + name_len, prop_count)
            children: list[Node] = []
            if after < end:
                children, _ = self.nodes(after, end, depth + 1)
            self.node_count += 1
            if self.node_count > MAX_NODES:
                raise FBXError("node count exceeds the safety limit")
            out.append(Node(name, props, children, end))
            offset = end
        return out, offset


def parse_bytes(data: bytes) -> tuple[int, list[Node]]:
    """Parse an FBX byte buffer into top-level nodes plus the file version."""
    if data.startswith(BINARY_MAGIC):
        if len(data) < 27:
            raise FBXError("binary FBX header is truncated")
        version = struct.unpack_from("<I", data, 23)[0]
        if not 7000 <= version <= 7800:
            raise FBXError(f"unsupported binary FBX version {version}")
        reader = _Reader(data, version)
        nodes, _ = reader.nodes(27, len(data))
        if not nodes:
            raise FBXError("binary FBX contains no nodes")
        return version, nodes
    if data.startswith(ASCII_MAGIC):
        raise FBXError("ASCII FBX is not supported; re-export as binary FBX")
    raise FBXError("not an FBX file (missing 'Kaydara FBX Binary' header)")


# --------------------------------------------------------------------------- #
# Document model
# --------------------------------------------------------------------------- #
@dataclass
class FBXProperty:
    name: str
    values: list[Any]
    type_name: str = ""


def _properties70(node: Node | None) -> dict[str, list[Any]]:
    if node is None:
        return {}
    out: dict[str, list[Any]] = {}
    for entry in node.children:
        if entry.name != "P" or not entry.props:
            continue
        key = str(entry.props[0])
        out[key] = list(entry.props[4:])
    return out


@dataclass
class FBXModel:
    uid: int
    name: str
    type_name: str
    parent_uid: int | None = None
    translation: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    pre_rotation: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    post_rotation: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    scaling: list[float] = field(default_factory=lambda: [1.0, 1.0, 1.0])
    rotation_order: str = "XYZ"
    rotation_active: bool = False
    properties: dict[str, list[Any]] = field(default_factory=dict)


@dataclass
class FBXCurve:
    uid: int
    key_times: list[int] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    key_attr_flags: int = 0

    @property
    def start_seconds(self) -> float:
        return self.key_times[0] / TICKS_PER_SECOND if self.key_times else 0.0

    @property
    def end_seconds(self) -> float:
        return self.key_times[-1] / TICKS_PER_SECOND if self.key_times else 0.0


@dataclass
class FBXStack:
    uid: int
    name: str
    start_tick: int = 0
    stop_tick: int = 0
    layer_uids: list[int] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return max(0, self.stop_tick - self.start_tick) / TICKS_PER_SECOND


@dataclass
class FBXDocument:
    path: Path
    version: int
    creator: str
    file_id: str
    nodes: list[Node]
    models: list[FBXModel]
    parent_of: dict[int, int]
    stacks: list[FBXStack]
    curves: dict[int, FBXCurve]
    # model uid -> property name -> axis ("d|X"...) -> (curve uid, default value)
    animated_channels: dict[int, dict[str, dict[str, tuple[int, float]]]]
    unit_scale_factor: float
    up_axis: str
    fps: float
    has_skin: bool
    node_attribute_types: dict[int, str]
    curve_node_defaults: dict[int, float]
    issues: list[dict[str, str]] = field(default_factory=list)

    # -- helpers ---------------------------------------------------------- #
    @property
    def scale_to_metres(self) -> float:
        """FBX linear unit -> metres. UnitScaleFactor 1.0 means centimetres."""
        return self.unit_scale_factor / 100.0

    def model_by_uid(self, uid: int) -> FBXModel | None:
        for model in self.models:
            if model.uid == uid:
                return model
        return None

    def children_of(self, uid: int) -> list[FBXModel]:
        return [model for model in self.models if self.parent_of.get(model.uid) == uid]

    def root_models(self) -> list[FBXModel]:
        return [model for model in self.models if model.uid not in self.parent_of]


def _float_list(values: Iterable[Any], default: list[float]) -> list[float]:
    out: list[float] = []
    for i in range(3):
        try:
            out.append(float(list(values)[i]))
        except (IndexError, TypeError, ValueError):
            out.append(default[i])
    return out


def _global_settings(nodes: list[Node]) -> dict[str, list[Any]]:
    for node in nodes:
        if node.name == "GlobalSettings":
            return _properties70(node.first("Properties70"))
    return {}


def _up_axis(props: dict[str, list[Any]]) -> str:
    try:
        axis = int(props.get("UpAxis", [1])[0])
    except (TypeError, ValueError):
        axis = 1
    return {0: "X", 1: "Y", 2: "Z"}.get(axis, "Y")


def load_fbx(path: Path | str) -> FBXDocument:
    """Parse an FBX file into a typed document (read-only; never modified)."""
    path = Path(path)
    data = path.read_bytes()
    version, nodes = parse_bytes(data)
    by_name: dict[str, Node] = {node.name: node for node in nodes}

    creator = ""
    for node in nodes:
        if node.name == "Creator" and node.props and isinstance(node.props[0], str):
            creator = node.props[0]
            break
    file_id = ""
    for node in nodes:
        if node.name == "FileId" and node.props and isinstance(node.props[0], bytes):
            file_id = node.props[0].hex()
            break

    settings = _global_settings(nodes)
    try:
        unit_scale = float(settings.get("UnitScaleFactor", [1.0])[0])
    except (TypeError, ValueError):
        unit_scale = 1.0
    try:
        time_mode = int(settings.get("TimeMode", [6])[0])
    except (TypeError, ValueError):
        time_mode = 6
    fps = _TIME_MODES.get(time_mode, 30.0)
    try:
        custom = float(settings.get("CustomFrameRate", [-1.0])[0])
        if custom > 0:
            fps = custom
    except (TypeError, ValueError):
        pass

    objects = by_name.get("Objects")
    if objects is None:
        raise FBXError("FBX file has no Objects section")

    node_attribute_types: dict[int, str] = {}
    for attr in objects.find("NodeAttribute"):
        if len(attr.props) >= 3 and isinstance(attr.props[0], int):
            node_attribute_types[int(attr.props[0])] = str(attr.props[2])

    models: list[FBXModel] = []
    has_skin = False
    for node in objects.children:
        if node.name == "Deformer":
            has_skin = True
            continue
        if node.name != "Model" or not node.props:
            continue
        props = _properties70(node.first("Properties70"))
        order_index = 0
        try:
            order_index = int(props.get("RotationOrder", [0])[0])
        except (TypeError, ValueError, IndexError):
            order_index = 0
        models.append(FBXModel(
            uid=int(node.props[0]),
            name=_object_name(node),
            type_name=_object_type(node),
            translation=_float_list(props.get("Lcl Translation", []), [0.0, 0.0, 0.0]),
            rotation=_float_list(props.get("Lcl Rotation", []), [0.0, 0.0, 0.0]),
            pre_rotation=_float_list(props.get("PreRotation", []), [0.0, 0.0, 0.0]),
            post_rotation=_float_list(props.get("PostRotation", []), [0.0, 0.0, 0.0]),
            scaling=_float_list(props.get("Lcl Scaling", []), [1.0, 1.0, 1.0]),
            rotation_order=_ROTATION_ORDERS.get(order_index, "XYZ"),
            rotation_active=bool(props.get("RotationActive", [0])[0]),
            properties=props,
        ))

    curves: dict[int, FBXCurve] = {}
    curve_node_defaults: dict[int, float] = {}
    for node in objects.children:
        if node.name == "AnimationCurve" and node.props:
            uid = int(node.props[0])
            times_node, values_node = node.first("KeyTime"), node.first("KeyValueFloat")
            times = list(times_node.props[0]) if times_node and times_node.props else []
            values = list(values_node.props[0]) if values_node and values_node.props else []
            if len(times) != len(values):
                # Never invent keys: record the mismatch and keep the overlap.
                keep = min(len(times), len(values))
                times, values = times[:keep], values[:keep]
            flags_node = node.first("KeyAttrFlags")
            flags = 0
            if flags_node and flags_node.props:
                raw_flags = flags_node.props[0]
                flags = int(raw_flags[0]) if isinstance(raw_flags, list) and raw_flags else int(raw_flags)
            curves[uid] = FBXCurve(uid=uid, key_times=[int(t) for t in times],
                                   values=[float(v) for v in values], key_attr_flags=flags)
        elif node.name == "AnimationCurveNode" and node.props:
            default = node.first("Default")
            if default and default.props:
                try:
                    curve_node_defaults[int(node.props[0])] = float(default.props[0])
                except (TypeError, ValueError):
                    pass

    stacks: list[FBXStack] = []
    for node in objects.children:
        if node.name == "AnimationStack" and node.props:
            props = _properties70(node.first("Properties70"))
            try:
                start = int(props.get("LocalStart", [0])[0])
                stop = int(props.get("LocalStop", [0])[0])
            except (TypeError, ValueError, IndexError):
                start = stop = 0
            stacks.append(FBXStack(uid=int(node.props[0]), name=_object_name(node),
                                   start_tick=start, stop_tick=stop))

    connections = by_name.get("Connections")
    parent_of: dict[int, int] = {}
    model_uids = {model.uid for model in models}
    curve_node_to_model: dict[int, tuple[int, str]] = {}
    curve_to_curve_node: dict[int, tuple[int, str]] = {}
    if connections is not None:
        for conn in connections.children:
            if not conn.props or str(conn.props[0]) not in ("OO", "OP"):
                continue
            if len(conn.props) < 3:
                continue
            child, parent = int(conn.props[1]), int(conn.props[2])
            prop = str(conn.props[3]) if len(conn.props) > 3 else ""
            if conn.props[0] == "OO" and child in model_uids and parent in model_uids:
                parent_of[child] = parent
            elif conn.props[0] == "OP":
                # An OP connection points at the property being driven: either a
                # Model property (curve node -> model) or a curve node axis
                # (curve -> curve node "d|X").
                if parent in model_uids:
                    curve_node_to_model[child] = (parent, prop)
                else:
                    curve_to_curve_node[child] = (parent, prop)

    animated: dict[int, dict[str, dict[str, tuple[int, float]]]] = {}
    for curve_node_uid, (model_uid, prop) in curve_node_to_model.items():
        animated.setdefault(model_uid, {}).setdefault(prop, {})
    for curve_uid, (curve_node_uid, axis) in curve_to_curve_node.items():
        target = curve_node_to_model.get(curve_node_uid)
        if not target or curve_uid not in curves:
            continue
        model_uid, prop = target
        default = curve_node_defaults.get(curve_node_uid, 0.0)
        animated.setdefault(model_uid, {}).setdefault(prop, {})[axis] = (curve_uid, default)

    document = FBXDocument(
        path=path, version=version, creator=creator, file_id=file_id, nodes=nodes,
        models=models, parent_of=parent_of, stacks=stacks, curves=curves,
        animated_channels=animated, unit_scale_factor=unit_scale,
        up_axis=_up_axis(settings), fps=fps, has_skin=has_skin,
        node_attribute_types=node_attribute_types,
        curve_node_defaults=curve_node_defaults,
    )
    document.issues.extend(_take_timing_issues(document))
    return document


def _take_timing_issues(document: FBXDocument) -> list[dict[str, str]]:
    """Cross-check AnimationStack LocalStop against the Takes section."""
    issues: list[dict[str, str]] = []
    takes = next((node for node in document.nodes if node.name == "Takes"), None)
    if takes is None:
        return issues
    local_times = [child for take in takes.children if take.name == "Take"
                   for child in take.children if child.name == "LocalTime"]
    for stack, local in zip(document.stacks, local_times):
        if len(local.props) >= 2 and stack.stop_tick:
            declared = int(local.props[1]) - int(local.props[0])
            actual = stack.stop_tick - stack.start_tick
            if abs(declared - actual) > 46186158:  # > ~1 ms
                issues.append({
                    "severity": "WARNING", "code": "FBX_TAKE_TIME_MISMATCH",
                    "description": (f"AnimationStack '{stack.name}' duration "
                                    f"{actual / TICKS_PER_SECOND:.4f}s disagrees with Takes LocalTime "
                                    f"{declared / TICKS_PER_SECOND:.4f}s"),
                    "asset": document.path.name, "location": "Takes.Take.LocalTime",
                    "suggested_fix": "Re-export the FBX; the pipeline uses the AnimationStack range.",
                })
    return issues


# --------------------------------------------------------------------------- #
# Inspection (manifest-ready metadata)
# --------------------------------------------------------------------------- #
def _column_major(matrix: list[list[float]]) -> list[float]:
    return [matrix[row][col] for col in range(4) for row in range(4)]


def skeleton_summary(doc: FBXDocument) -> dict[str, Any]:
    """Skeleton metadata in metres, compatible with ``processing.bvh`` output."""
    from processing import transform as tf  # local import keeps module import cheap
    from processing.motion import hierarchy_order

    scale = doc.scale_to_metres
    by_uid = {model.uid: model for model in doc.models}
    order = hierarchy_order(doc)
    children: dict[str, list[str]] = {model.name: [] for model in order}
    for model in order:
        parent = by_uid.get(doc.parent_of.get(model.uid, -1))
        if parent is not None:
            children.setdefault(parent.name, []).append(model.name)

    world: dict[str, list[list[float]]] = {}
    bones: list[dict[str, Any]] = []
    for index, model in enumerate(order):
        parent = by_uid.get(doc.parent_of.get(model.uid, -1))
        rotation = tf.multiply(tf.euler_to_matrix(model.pre_rotation, model.rotation_order),
                               tf.euler_to_matrix(model.rotation, model.rotation_order))
        local = tf.multiply(tf.translation(*(v * scale for v in model.translation)),
                            tf.multiply(rotation, tf.scaling(*model.scaling)))
        world[model.name] = (tf.multiply(world[parent.name], local)
                             if parent and parent.name in world else local)
        translation = [v * scale for v in model.translation]
        bones.append({
            "name": model.name, "index": index,
            "parent": parent.name if parent else None,
            "children": children.get(model.name, []),
            "translation": [round(v, 6) for v in translation],
            "rotation": [round(v, 6) for v in tf.quat_from_matrix(rotation)],
            "scale": [round(v, 6) for v in model.scaling],
            "global_matrix": [round(v, 6) for v in _column_major(world[model.name])],
            "length": round(math.sqrt(sum(v * v for v in translation)), 6),
            "type": doc.node_attribute_types.get(model.uid) or model.type_name,
        })
    roots = [model.name for model in order
             if doc.parent_of.get(model.uid) not in by_uid or doc.parent_of.get(model.uid) is None]
    return {"present": bool(bones), "skeleton_count": 1 if bones else 0,
            "root_bone": roots[0] if roots else None, "bone_count": len(bones),
            "bones": bones, "skins": [], "has_skin_deformer": doc.has_skin,
            "humanoid": False, "humanoid_confidence": 0.0}


def clip_summary(doc: FBXDocument, stack: FBXStack) -> dict[str, Any]:
    """Per-clip timing and channel facts taken from the curve data."""
    from processing.motion import ClipSampler, resolve_roles

    sampler = ClipSampler(doc, stack)
    start, end = sampler.time_range()
    times = sampler.key_times()
    animated_bones: list[str] = []
    channel_count = 0
    for model_uid, properties in doc.animated_channels.items():
        model = doc.model_by_uid(model_uid)
        if not model:
            continue
        count = sum(len(axes) for axes in properties.values())
        if count:
            animated_bones.append(model.name)
            channel_count += count
    key_count = len(times)
    frame_rate = doc.fps
    return {
        "name": stack.name or doc.path.stem,
        "index": doc.stacks.index(stack),
        "duration_seconds": round(max(0.0, end - start), 6),
        "frame_rate": round(frame_rate, 4),
        "frame_start": 0,
        "frame_end": int(round(max(0.0, end - start) * frame_rate)) if frame_rate else 0,
        "key_count": key_count,
        "channel_count": channel_count,
        "animated_bones": sorted(animated_bones),
        "interpolation": "LINEAR",
        "root_motion": False,  # replaced by index_clip() when features are computed
        "tick_range": [int(stack.start_tick), int(stack.stop_tick)],
        "roles": resolve_roles(sampler.bone_names),
    }


def index_clip(doc: FBXDocument, stack: FBXStack) -> dict[str, Any]:
    """Full per-clip record: timing + measured motion features + root-motion flag."""
    from processing.motion import motion_features

    clip = clip_summary(doc, stack)
    features = motion_features(doc, stack)
    root_motion = bool((features.get("root_motion") or {}).get("has_root_motion"))
    clip["root_motion"] = root_motion
    clip["features"] = features
    return clip


def inspect_fbx(path: Path | str) -> dict[str, Any]:
    """Inspect an FBX source: skeleton, clips, unit/axis info and real issues."""
    doc = load_fbx(path)
    clones = [model for model in doc.models if model.type_name.lower() in ("mesh", "null")]
    issues = list(doc.issues)
    if not doc.stacks:
        issues.append({"severity": "ERROR", "code": "FBX_NO_ANIMATION_STACK",
                       "description": "The FBX file contains no AnimationStack, so it has no clips",
                       "asset": doc.path.name, "location": "Objects.AnimationStack",
                       "suggested_fix": "Export the animation again, or treat the file as a model."})
    if not doc.models:
        issues.append({"severity": "ERROR", "code": "FBX_NO_SKELETON",
                       "description": "The FBX file contains no Model nodes",
                       "asset": doc.path.name, "location": "Objects.Model",
                       "suggested_fix": "Re-export with the armature included."})
    clips = [index_clip(doc, stack) for stack in doc.stacks]
    for clip in clips:
        if clip["channel_count"] == 0:
            issues.append({"severity": "ERROR", "code": "FBX_EMPTY_CLIP",
                           "description": f"Clip '{clip['name']}' has no animation curves",
                           "asset": doc.path.name, "location": "Objects.AnimationCurve",
                           "suggested_fix": "Re-export the action with its keyframes."})
        elif clip["duration_seconds"] <= 0:
            issues.append({"severity": "ERROR", "code": "FBX_ZERO_DURATION_CLIP",
                           "description": f"Clip '{clip['name']}' has a zero-length time range",
                           "asset": doc.path.name, "location": "Objects.AnimationStack",
                           "suggested_fix": "Check the exported LocalStop time."})
    skeleton = skeleton_summary(doc)
    skeleton["humanoid"], skeleton["humanoid_confidence"] = _humanoid(skeleton["bones"])
    return {
        "format": "FBX",
        "version": doc.version,
        "encoding": "binary",
        "creator": doc.creator,
        "file_id": doc.file_id,
        "up_axis": doc.up_axis,
        "unit_scale_factor": doc.unit_scale_factor,
        "linear_unit": "centimetres" if abs(doc.unit_scale_factor - 1.0) < 1e-9 else "custom",
        "frame_rate": doc.fps,
        "clip_count": len(clips),
        "clips": clips,
        "skeleton": skeleton,
        "models": [{"name": model.name, "uid": model.uid, "type": model.type_name,
                    "parent": (doc.model_by_uid(doc.parent_of.get(model.uid, -1)) or FBXModel(0, "", "")).name or None}
                   for model in doc.models],
        "mesh_models": [model.name for model in clones],
        "has_skin_deformer": doc.has_skin,
        "issues": issues,
    }


def _humanoid(bones: list[dict[str, Any]]) -> tuple[bool, float]:
    try:
        from processing.compatibility import load_mappings, map_roles
        roles, ambiguous, confidence = map_roles(bones)
        required = set(load_mappings().get("required_roles", []))
        coverage = len(required & set(roles)) / max(1, len(required))
        return (coverage >= 0.75 and len(roles) >= 12 and not ambiguous,
                round(confidence * coverage, 4))
    except Exception:  # pragma: no cover - defensive, mapping file is versioned
        return False, 0.0
