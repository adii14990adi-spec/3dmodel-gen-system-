"""Real glTF 2.0 / GLB reader, inspector and writer (pure Python, no fabricated data)."""
from __future__ import annotations

import json
import math
import struct
from pathlib import Path
from typing import Any

GLB_MAGIC = b"glTF"
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942
COMPONENT_SIZES = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
NUM_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


class GLBError(ValueError):
    pass


def parse_glb(data: bytes) -> dict[str, Any]:
    if len(data) < 20 or data[:4] != GLB_MAGIC:
        raise GLBError("not a GLB (bad magic or too short)")
    version, length = struct.unpack_from("<II", data, 4)
    if version != 2:
        raise GLBError(f"unsupported glTF version {version}")
    if length > len(data):
        raise GLBError("GLB header length exceeds file size (truncated)")
    offset, doc, binary = 12, None, b""
    while offset + 8 <= min(length, len(data)):
        chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        if offset + chunk_len > len(data):
            raise GLBError("GLB chunk extends past end of file")
        payload = data[offset:offset + chunk_len]
        if chunk_type == JSON_CHUNK:
            doc = json.loads(payload.rstrip(b"\x00 ").decode("utf-8"))
        elif chunk_type == BIN_CHUNK:
            binary = payload
        offset += chunk_len
    if doc is None:
        raise GLBError("GLB has no JSON chunk")
    return {"json": doc, "bin": binary, "raw": data}


def load_document(path: Path | str) -> dict[str, Any]:
    """Load .glb or .gltf; external .bin/.gltf buffers are resolved for inspection."""
    path = Path(path)
    raw = path.read_bytes()
    if raw[:4] == GLB_MAGIC:
        return parse_glb(raw)
    doc = json.loads(raw.decode("utf-8"))
    binary = b""
    for buf in doc.get("buffers", []):
        uri = buf.get("uri")
        if uri and not uri.startswith("data:"):
            binary = (path.parent / uri).read_bytes()
            break
    return {"json": doc, "bin": binary, "raw": raw}


def _strip_empty_lists(value: Any) -> Any:
    """glTF forbids empty array entities (Khronos EMPTY_ENTITY)."""
    if isinstance(value, dict):
        return {k: _strip_empty_lists(v) for k, v in value.items() if not (isinstance(v, list) and not v)}
    if isinstance(value, list):
        return [_strip_empty_lists(v) for v in value]
    return value


def write_glb(doc: dict[str, Any], binary: bytes = b"") -> bytes:
    """Serialise a glTF document into a binary GLB with 4-byte aligned chunks."""
    doc = _strip_empty_lists(json.loads(json.dumps(doc)))
    if binary:
        doc.setdefault("buffers", [])
        if not doc["buffers"]:
            doc["buffers"].append({})
        doc["buffers"][0]["byteLength"] = len(binary)
        doc["buffers"][0].pop("uri", None)
    json_bytes = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    json_pad = (-len(json_bytes)) % 4
    json_bytes += b" " * json_pad
    bin_pad = (-len(binary)) % 4
    bin_bytes = binary + b"\x00" * bin_pad
    total = 12 + 8 + len(json_bytes) + (8 + len(bin_bytes) if binary else 0)
    out = bytearray()
    out += struct.pack("<4sII", GLB_MAGIC, 2, total)
    out += struct.pack("<II", len(json_bytes), JSON_CHUNK)
    out += json_bytes
    if binary:
        out += struct.pack("<II", len(bin_bytes), BIN_CHUNK)
        out += bin_bytes
    return bytes(out)


def buffer_view_bytes(doc_view: dict[str, Any], data: bytes, offset: int = 0) -> bytes:
    start = doc_view.get("byteOffset", 0) + offset
    length = doc_view.get("byteLength", 0)
    if start + length > len(data):
        raise GLBError("bufferView exceeds binary chunk")
    return data[start:start + length]


def read_accessor(doc: dict[str, Any], binary: bytes, index: int) -> list[Any]:
    acc = doc["accessors"][index]
    view = doc["bufferViews"][acc["bufferView"]]
    ct = acc["componentType"]
    n = NUM_COMPONENTS[acc["type"]]
    fmt = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}[ct]
    size = COMPONENT_SIZES[ct] * n
    stride = view.get("byteStride", size)
    raw = buffer_view_bytes(view, binary, acc.get("byteOffset", 0))
    count = acc["count"]
    values: list[Any] = []
    for i in range(count):
        start = i * stride
        flat = struct.unpack_from("<" + fmt * n, raw, start)
        values.append(list(flat) if n > 1 else flat[0])
    return values


def append_accessor(doc: dict[str, Any], binary: bytes, values: list[Any],
                    component_type: int, gltf_type: str, minmax: bool = False) -> tuple[int, bytes]:
    """Append an accessor and its bufferView; returns (accessor_index, new_binary)."""
    n = NUM_COMPONENTS[gltf_type]
    fmt = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}[component_type]
    flat: list[float] = []
    for v in values:
        flat.extend(v if isinstance(v, (list, tuple)) else [v])
    payload = struct.pack("<" + fmt * len(flat), *flat)
    while len(binary) % 4:
        binary += b"\x00"
    offset = len(binary)
    binary += payload
    doc.setdefault("bufferViews", []).append({"buffer": 0, "byteOffset": offset, "byteLength": len(payload)})
    acc: dict[str, Any] = {
        "bufferView": len(doc["bufferViews"]) - 1, "componentType": component_type,
        "count": len(values), "type": gltf_type,
    }
    if minmax and values:
        acc["min"] = [min(float(v[i]) for v in (x if isinstance(x, (list, tuple)) else [x] for x in values)) for i in range(n)]
        acc["max"] = [max(float(v[i]) for v in (x if isinstance(x, (list, tuple)) else [x] for x in values)) for i in range(n)]
    doc.setdefault("accessors", []).append(acc)
    return len(doc["accessors"]) - 1, binary


def _compose(t: list[float], r: list[float], s: list[float]) -> list[float]:
    """Column-major TRS matrix from a glTF node."""
    x, y, z, w = (list(r) + [0, 0, 0, 1])[:4]
    xx, yy, zz = x*x, y*y, z*z
    xy, xz, yz = x*y, x*z, y*z
    wx, wy, wz = w*x, w*y, w*z
    rot = [1-2*(yy+zz), 2*(xy+wz), 2*(xz-wy), 0,
           2*(xy-wz), 1-2*(xx+zz), 2*(yz+wx), 0,
           2*(xz+wy), 2*(yz-wx), 1-2*(xx+yy), 0,
           0, 0, 0, 1]
    m = []
    for col in range(4):
        for row in range(4):
            m.append(rot[col*4+row] * (s[col] if row < 3 and col < 3 else 1))
    m[12], m[13], m[14] = t[0], t[1], t[2]
    return m


def png_size(data: bytes) -> tuple[int | None, int | None]:
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack_from(">II", data, 16)
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1; continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                return struct.unpack_from(">HH", data, i + 7)[::-1]
            length = struct.unpack_from(">H", data, i + 2)[0]
            i += 2 + length
    return None, None


def _image_bytes(doc: dict[str, Any], binary: bytes, image_index: int) -> tuple[bytes | None, str | None]:
    images = doc.get("images", [])
    if image_index >= len(images):
        return None, None
    image = images[image_index]
    if "bufferView" in image:
        view = doc["bufferViews"][image["bufferView"]]
        return buffer_view_bytes(view, binary), image.get("mimeType", "image/png")
    return None, image.get("uri")


def _humanoid_score(bones: list[dict[str, Any]]) -> tuple[bool, float]:
    """Real role coverage from the configured humanoid mapping; no guessing."""
    if not bones:
        return False, 0.0
    try:
        from processing.compatibility import load_mappings, map_roles
        roles, ambiguous, confidence = map_roles(bones)
        required = set(load_mappings().get("required_roles", []))
        coverage = len(required & set(roles)) / max(1, len(required))
        return (coverage >= 0.75 and len(roles) >= 12 and not ambiguous,
                round(confidence * coverage, 4))
    except Exception:
        return False, 0.0


def inspect_document(asset: dict[str, Any], name: str = "") -> dict[str, Any]:
    """Extract real geometry/material/texture/skeleton/animation metadata and issues."""
    doc, binary = asset["json"], asset["bin"]
    issues: list[dict[str, str]] = []
    meshes = doc.get("meshes", [])
    materials_doc = doc.get("materials", [])
    textures_doc = doc.get("textures", [])
    nodes = doc.get("nodes", [])
    skins = doc.get("skins", [])

    mesh_count = primitive_count = vertex_count = triangle_count = index_count = morph_count = 0
    uv_sets: set[str] = set()
    has_normals = has_tangents = False
    bounds_min = [math.inf] * 3
    bounds_max = [-math.inf] * 3
    for mesh in meshes:
        mesh_count += 1
        for prim in mesh.get("primitives", []):
            primitive_count += 1
            attrs = prim.get("attributes", {})
            if "POSITION" in attrs:
                acc = doc["accessors"][attrs["POSITION"]]
                vertex_count += acc.get("count", 0)
                if acc.get("min") and acc.get("max"):
                    for i in range(3):
                        bounds_min[i] = min(bounds_min[i], acc["min"][i])
                        bounds_max[i] = max(bounds_max[i], acc["max"][i])
            if "NORMAL" in attrs: has_normals = True
            if "TANGENT" in attrs: has_tangents = True
            for key in attrs:
                if key.startswith("TEXCOORD_"): uv_sets.add(key)
            morph_count += len(prim.get("targets", []))
            if "indices" in prim:
                idx = doc["accessors"][prim["indices"]]
                index_count += idx.get("count", 0)
                triangle_count += idx.get("count", 0) // 3
            elif "POSITION" in attrs:
                n = doc["accessors"][attrs["POSITION"]].get("count", 0)
                triangle_count += n // 3
                index_count += n
    if primitive_count and not has_normals:
        issues.append({"severity": "WARNING", "code": "MISSING_NORMALS",
                       "description": "At least one primitive has no NORMAL attribute",
                       "asset": name, "location": "meshes[].primitives[].attributes",
                       "suggested_fix": "Generate vertex normals in the processing copy."})

    materials: list[dict[str, Any]] = []
    missing_texture_slots: list[str] = []
    for i, m in enumerate(materials_doc):
        pbr = m.get("pbrMetallicRoughness", {})
        slots: dict[str, Any] = {}
        for key, ref_key in (("baseColorTexture", "baseColorTexture"), ("metallicRoughnessTexture", "metallicRoughnessTexture"),
                             ("normalTexture", "normalTexture"), ("occlusionTexture", "occlusionTexture"),
                             ("emissiveTexture", "emissiveTexture")):
            ref = (pbr.get(ref_key) if ref_key in ("baseColorTexture", "metallicRoughnessTexture") else m.get(ref_key))
            if isinstance(ref, dict) and "index" in ref:
                slots[key] = {"texture": ref["index"], "tex_coord": ref.get("texCoord", 0),
                              "scale": ref.get("scale", 1.0), "strength": ref.get("strength", 1.0)}
        entry_missing = [s for s, v in slots.items() if v["texture"] >= len(textures_doc)]
        missing_texture_slots.extend(f"{m.get('name', f'material_{i}')}:{s}" for s in entry_missing)
        materials.append({
            "name": m.get("name", f"material_{i}"), "index": i,
            "base_color_factor": pbr.get("baseColorFactor", [1, 1, 1, 1]),
            "metallic_factor": pbr.get("metallicFactor", 1.0),
            "roughness_factor": pbr.get("roughnessFactor", 1.0),
            "emissive_factor": m.get("emissiveFactor", [0, 0, 0]),
            "alpha_mode": m.get("alphaMode", "OPAQUE"), "alpha_cutoff": m.get("alphaCutoff", 0.5),
            "double_sided": m.get("doubleSided", False), "textures": slots,
            "missing_textures": entry_missing,
        })

    textures: list[dict[str, Any]] = []
    for i, t in enumerate(textures_doc):
        src = t.get("source")
        raw, uri_or_mime = None, None
        mime = None
        if isinstance(src, int):
            img = doc.get("images", [])
            if src < len(img):
                mime = img[src].get("mimeType")
                raw, uri_or_mime = _image_bytes(doc, binary, src)
                if uri_or_mime and uri_or_mime.startswith("data:"):
                    mime = uri_or_mime.split(";")[0][5:]
        w, h = png_size(raw) if raw else (None, None)
        present = bool(raw) or (uri_or_mime is not None and not str(uri_or_mime).startswith("data:"))
        textures.append({"name": t.get("name", f"texture_{i}"), "index": i,
                         "uri": str(uri_or_mime) if uri_or_mime and not str(uri_or_mime).startswith(("data:", "image/")) else None,
                         "mime_type": mime, "width": w, "height": h,
                         "bytes": len(raw) if raw else None, "present": present})
    for label in missing_texture_slots:
        issues.append({"severity": "ERROR", "code": "UNRESOLVED_REFERENCE",
                       "description": f"Material references a missing texture ({label})",
                       "asset": name, "location": "materials[]",
                       "suggested_fix": "Supply the texture or remove the reference in the processed copy."})

    # --- Skeleton and skinning (only what is actually present) ---
    parents: dict[int, int | None] = {i: None for i in range(len(nodes))}
    for i, node in enumerate(nodes):
        for child in node.get("children", []):
            if child < len(nodes): parents[child] = i
    joint_names: set[str] = set()
    skin_infos: list[dict[str, Any]] = []
    for i, skin in enumerate(skins):
        joints = skin.get("joints", [])
        joint_names.update(joints)
        influence_counts: dict[str, int] = {}
        unweighted = invalid = 0
        has_joints = has_weights = False
        for mesh in meshes:
            for prim in mesh.get("primitives", []):
                attrs = prim.get("attributes", {})
                has_joints |= "JOINTS_0" in attrs
                has_weights |= "WEIGHTS_0" in attrs
                if "WEIGHTS_0" in attrs:
                    try:
                        weights = read_accessor(doc, binary, attrs["WEIGHTS_0"])
                    except (GLBError, KeyError, struct.error):
                        weights = []
                    for w in weights:
                        if not isinstance(w, list): continue
                        nonzero = sum(1 for x in w if x > 0)
                        influence_counts[str(nonzero)] = influence_counts.get(str(nonzero), 0) + 1
                        total = sum(w)
                        if total <= 0: unweighted += 1
                        if abs(total - 1.0) > 0.02: invalid += 1
        missing = [f"node_{j}" for j in joints if j >= len(nodes)]
        skin_infos.append({
            "name": skin.get("name", f"skin_{i}"), "joint_count": len(joints),
            "root_bone": nodes[joints[0]].get("name") if joints and joints[0] < len(nodes) else None,
            "skeleton_node": nodes[skin["skeleton"]].get("name") if isinstance(skin.get("skeleton"), int) and skin["skeleton"] < len(nodes) else None,
            "joints": [nodes[j].get("name", f"node_{j}") if j < len(nodes) else f"node_{j}" for j in joints],
            "has_inverse_bind_matrices": "inverseBindMatrices" in skin,
            "skinned_meshes": [nodes[n].get("name", f"node_{n}") for n, node in enumerate(nodes)
                               if isinstance(node.get("mesh"), int) and isinstance(node.get("skin"), int)],
            "vertex_influence_counts": influence_counts,
            "unweighted_vertices": unweighted, "invalid_weight_vertices": invalid,
            "missing_joints": missing,
            "has_joints_attribute": has_joints, "has_weights_attribute": has_weights,
        })
    bones: list[dict[str, Any]] = []
    for i, node in enumerate(nodes):
        nm = node.get("name", f"node_{i}")
        if i not in {j for s in skins for j in s.get("joints", [])} and not skins:
            continue
        if skins and nm not in joint_names and i not in {j for s in skins for j in s.get("joints", [])}:
            continue
        parent = parents.get(i)
        parent_name = nodes[parent].get("name") if parent is not None else None
        m = node.get("matrix")
        t, r, s = node.get("translation", [0, 0, 0]), node.get("rotation", [0, 0, 0, 1]), node.get("scale", [1, 1, 1])
        if m:
            t = [m[12], m[13], m[14]]
            s = [math.sqrt(m[0] ** 2 + m[1] ** 2 + m[2] ** 2),
                 math.sqrt(m[4] ** 2 + m[5] ** 2 + m[6] ** 2),
                 math.sqrt(m[8] ** 2 + m[9] ** 2 + m[10] ** 2)]
        bones.append({"name": nm, "index": i, "parent": parent_name,
                      "children": [nodes[c].get("name", f"node_{c}") for c in node.get("children", []) if c < len(nodes)],
                      "translation": t, "rotation": r, "scale": s,
                      "global_matrix": list(m) if m else _compose(t, r, s), "length": 0.0})
    root_bone = next((b["name"] for b in bones if b["parent"] not in {x["name"] for x in bones}), None) if bones else None
    humanoid, humanoid_confidence = _humanoid_score(bones)
    skeleton = {"present": bool(skins), "skeleton_count": len(skins), "bone_count": len(bones),
                "root_bone": root_bone or (skin_infos[0]["root_bone"] if skin_infos else None),
                "bones": bones, "skins": skin_infos, "humanoid": humanoid,
                "humanoid_confidence": humanoid_confidence}

    # --- Animations ---
    clips: list[dict[str, Any]] = []
    for i, anim in enumerate(doc.get("animations", [])):
        duration = 0.0
        frame_start, frame_end = math.inf, -math.inf
        animated: set[str] = set()
        root_motion = False
        interpolation = "LINEAR"
        for ch in anim.get("channels", []):
            sampler = anim.get("samplers", [{}])[ch.get("sampler", 0)]
            interpolation = sampler.get("interpolation", "LINEAR")
            input_acc = sampler.get("input")
            if input_acc is not None and input_acc < len(doc.get("accessors", [])):
                acc = doc["accessors"][input_acc]
                if acc.get("min"): frame_start = min(frame_start, float(acc["min"][0]))
                if acc.get("max"): frame_end = max(frame_end, float(acc["max"][0]))
                try:
                    times = read_accessor(doc, binary, input_acc)
                    if times: duration = max(duration, float(max(times)) - float(min(times)))
                except (GLBError, struct.error, KeyError):
                    pass
            target = ch.get("target", {})
            node_idx = target.get("node")
            if isinstance(node_idx, int) and node_idx < len(nodes):
                animated.add(nodes[node_idx].get("name", f"node_{node_idx}"))
                if target.get("path") == "translation" and node_idx == (skins[0].get("joints", [None])[0] if skins else None):
                    root_motion = True
        if frame_start is math.inf: frame_start = 0.0
        if frame_end == -math.inf: frame_end = duration
        clips.append({"name": anim.get("name", f"clip_{i}"), "index": i,
                      "duration_seconds": round(duration, 6), "frame_start": frame_start,
                      "frame_end": frame_end, "frame_rate": None,
                      "channel_count": len(anim.get("channels", [])),
                      "animated_bones": sorted(animated), "root_motion": root_motion,
                      "interpolation": interpolation})
    if clips:
        total_frames = max(1, sum(1 for _ in range(int(frame_end - frame_start) + 1)))
        for c in clips:
            if c["duration_seconds"] > 0:
                c["frame_rate"] = round(total_frames / c["duration_seconds"], 3)

    if math.inf in bounds_min:
        bounds_min, bounds_max = [0, 0, 0], [0, 0, 0]
    geometry = {
        "mesh_count": mesh_count, "primitive_count": primitive_count, "vertex_count": vertex_count,
        "triangle_count": triangle_count, "index_count": index_count,
        "material_count": len(materials_doc), "texture_count": len(textures_doc),
        "uv_sets": sorted(uv_sets), "has_normals": has_normals, "has_tangents": has_tangents,
        "morph_target_count": morph_count,
        "bounding_box": {"min": [round(v, 6) for v in bounds_min], "max": [round(v, 6) for v in bounds_max]},
        "dimensions": [round(bounds_max[i] - bounds_min[i], 6) for i in range(3)],
    }
    return {"geometry": geometry, "materials": materials, "textures": textures,
            "skeleton": skeleton, "animations": {"clip_count": len(clips), "clips": clips},
            "issues": issues}
