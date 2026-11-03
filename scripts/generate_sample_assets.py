#!/usr/bin/env python3
"""Generate the real A-L verification dataset into assets/ (source files only).

All GLBs are written by a genuine glTF 2.0 serialiser; BVHs are real motion text.
The pipeline never reads hardcoded metadata — it inspects these bytes.
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import zlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
ASSETS = ROOT / "assets"


class Builder:
    def __init__(self) -> None:
        self.doc: dict = {"asset": {"version": "2.0", "generator": "3D Asset Studio sample generator"},
                          "scene": 0, "scenes": [{"nodes": []}], "nodes": [],
                          "meshes": [], "materials": [], "textures": [], "images": [],
                          "samplers": [], "bufferViews": [], "accessors": [], "buffers": [{"byteLength": 0}],
                          "skins": [], "animations": []}
        self.binary = bytearray()

    def accessor(self, values, component_type: int, gltf_type: str, target: int | None = None,
                 minmax: bool = False) -> int:
        n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[gltf_type]
        fmt = {5121: "B", 5123: "H", 5125: "I", 5126: "f"}[component_type]
        flat = []
        for v in values:
            flat.extend(v if isinstance(v, (list, tuple)) else [v])
        while len(self.binary) % 4:
            self.binary += b"\x00"
        offset = len(self.binary)
        payload = struct.pack("<" + fmt * len(flat), *flat)
        self.binary += payload
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(payload)}
        if target:
            view["target"] = target
        self.doc["bufferViews"].append(view)
        acc: dict = {"bufferView": len(self.doc["bufferViews"]) - 1,
                     "componentType": component_type, "count": len(values), "type": gltf_type}
        if minmax and values:
            rows = [v if isinstance(v, (list, tuple)) else [v] for v in values]
            acc["min"] = [min(float(row[i]) for row in rows) for i in range(n)]
            acc["max"] = [max(float(row[i]) for row in rows) for i in range(n)]
        self.doc["accessors"].append(acc)
        return len(self.doc["accessors"]) - 1

    def node(self, name: str, **kwargs) -> int:
        self.doc["nodes"].append({"name": name, **kwargs})
        return len(self.doc["nodes"]) - 1

    def material(self, name: str, colour=(0.8, 0.8, 0.8, 1.0), metallic=0.0,
                 roughness=0.8, texture: int | None = None) -> int:
        pbr: dict = {"baseColorFactor": list(colour), "metallicFactor": metallic,
                     "roughnessFactor": roughness}
        if texture is not None:
            pbr["baseColorTexture"] = {"index": texture}
        self.doc["materials"].append({"name": name, "pbrMetallicRoughness": pbr,
                                      "doubleSided": False, "alphaMode": "OPAQUE"})
        return len(self.doc["materials"]) - 1

    def png_texture(self, name: str, rgb=(90, 170, 230)) -> int:
        w = h = 4
        raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
        png = (b"\x89PNG\r\n\x1a\n" +
               chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) +
               chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
        view = len(self.doc["bufferViews"])
        while len(self.binary) % 4:
            self.binary += b"\x00"
        self.doc["bufferViews"].append({"buffer": 0, "byteOffset": len(self.binary),
                                        "byteLength": len(png)})
        self.binary += png
        self.doc["images"].append({"name": name, "bufferView": view, "mimeType": "image/png"})
        self.doc["samplers"].append({"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497})
        self.doc["textures"].append({"name": name, "sampler": len(self.doc["samplers"]) - 1,
                                     "source": len(self.doc["images"]) - 1})
        return len(self.doc["textures"]) - 1

    def box(self, name: str, material: int, scale=(0.5, 0.5, 0.5), uv=True,
            normals=True, skin=False, joint_count: int = 1) -> int:
        sx, sy, sz = scale
        positions = []
        normals_list = []
        uvs = []
        faces = [((-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1), (0,0,1)),
                 ((1,-1,-1),(-1,-1,-1),(-1,1,-1),(1,1,-1), (0,0,-1)),
                 ((1,-1,1),(1,-1,-1),(1,1,-1),(1,1,1), (1,0,0)),
                 ((-1,-1,-1),(-1,-1,1),(-1,1,1),(-1,1,-1), (-1,0,0)),
                 ((-1,1,1),(1,1,1),(1,1,-1),(-1,1,-1), (0,1,0)),
                 ((-1,-1,-1),(1,-1,-1),(1,-1,1),(-1,-1,1), (0,-1,0))]
        for a,b,c,d,n in faces:
            for v in (a,b,c,d):
                positions.append([v[0]*sx, v[1]*sy + sy, v[2]*sz])
                normals_list.append(list(n))
                uvs.append([0.0 if v[0] < 0 else 1.0, 0.0 if v[1] < 0 else 1.0])
        indices = []
        for i in range(6):
            base = i*4
            indices += [base, base+1, base+2, base, base+2, base+3]
        attrs = {"POSITION": self.accessor(positions, 5126, "VEC3", 34962, minmax=True)}
        if normals:
            attrs["NORMAL"] = self.accessor(normals_list, 5126, "VEC3", 34962)
        if uv:
            attrs["TEXCOORD_0"] = self.accessor(uvs, 5126, "VEC2", 34962)
        if skin:
            joints = [[i % joint_count, 0, 0, 0] for i in range(len(positions))]
            weights = [[1.0, 0.0, 0.0, 0.0] for _ in positions]
            attrs["JOINTS_0"] = self.accessor(joints, 5123, "VEC4", 34962)
            attrs["WEIGHTS_0"] = self.accessor(weights, 5126, "VEC4", 34962)
        prim = {"attributes": attrs, "indices": self.accessor(indices, 5123, "SCALAR", 34963),
                "material": material, "mode": 4}
        self.doc["meshes"].append({"name": name, "primitives": [prim]})
        return len(self.doc["meshes"]) - 1

    def build(self) -> bytes:
        self.doc["buffers"][0]["byteLength"] = len(self.binary)
        from processing.glb import write_glb
        return write_glb(self.doc, bytes(self.binary))


HUMANOID = ["Hips", "Spine", "Chest", "Neck", "Head",
            "LeftShoulder", "LeftArm", "LeftForeArm", "LeftHand",
            "RightShoulder", "RightArm", "RightForeArm", "RightHand",
            "LeftUpLeg", "LeftLeg", "LeftFoot", "LeftToe",
            "RightUpLeg", "RightLeg", "RightFoot", "RightToe"]
PARENTS = {"Hips": None, "Spine": "Hips", "Chest": "Spine", "Neck": "Chest", "Head": "Neck",
           "LeftShoulder": "Chest", "LeftArm": "LeftShoulder", "LeftForeArm": "LeftArm", "LeftHand": "LeftForeArm",
           "RightShoulder": "Chest", "RightArm": "RightShoulder", "RightForeArm": "RightArm", "RightHand": "RightForeArm",
           "LeftUpLeg": "Hips", "LeftLeg": "LeftUpLeg", "LeftFoot": "LeftLeg", "LeftToe": "LeftFoot",
           "RightUpLeg": "Hips", "RightLeg": "RightUpLeg", "RightFoot": "RightLeg", "RightToe": "RightFoot"}
OFFSETS = {"Hips": [0, 0.9, 0], "Spine": [0, 0.2, 0], "Chest": [0, 0.25, 0],
           "Neck": [0, 0.2, 0], "Head": [0, 0.18, 0],
           "LeftShoulder": [0.12, 0.1, 0], "LeftArm": [0.22, 0, 0], "LeftForeArm": [0.28, 0, 0], "LeftHand": [0.24, 0, 0],
           "RightShoulder": [-0.12, 0.1, 0], "RightArm": [-0.22, 0, 0], "RightForeArm": [-0.28, 0, 0], "RightHand": [-0.24, 0, 0],
           "LeftUpLeg": [0.12, -0.08, 0], "LeftLeg": [0, -0.42, 0], "LeftFoot": [0, -0.4, 0], "LeftToe": [0, -0.06, 0.14],
           "RightUpLeg": [-0.12, -0.08, 0], "RightLeg": [0, -0.42, 0], "RightFoot": [0, -0.4, 0], "RightToe": [0, -0.06, 0.14]}


def humanoid_glb(prefix: str = "", with_animation: bool = False) -> bytes:
    b = Builder()
    names = [prefix + n for n in HUMANOID] if prefix else HUMANOID[:]
    indices: dict[str, int] = {}
    for name in names:
        original = name[len(prefix):] if prefix else name
        children: list[int] = []
        kwargs: dict = {"translation": OFFSETS[original], "children": children}
        indices[name] = b.node(name, **kwargs)
    for name in names:
        original = name[len(prefix):] if prefix else name
        parent = PARENTS[original]
        if parent:
            b.doc["nodes"][indices[prefix + parent]]["children"].append(indices[name])
    ibm = [[1,0,0,0, 0,1,0,0, 0,0,1,0, -v[0],-v[1],-v[2],1] for v in
           (OFFSETS[n[len(prefix):] if prefix else n] for n in names)]
    ibm_acc = b.accessor(ibm, 5126, "MAT4")
    mat = b.material("Skin", (0.72, 0.62, 0.55, 1.0))
    mesh = b.box("Body", mat, (0.28, 0.85, 0.16), skin=True, joint_count=len(names))
    joint_list = [indices[n] for n in names]
    b.doc["skins"].append({"name": "HumanoidSkin", "joints": joint_list,
                           "inverseBindMatrices": ibm_acc, "skeleton": indices[names[0]]})
    mesh_node = b.node("BodyMesh", mesh=mesh, skin=0)
    b.doc["scenes"][0]["nodes"] = [indices[names[0]], mesh_node]
    if with_animation:
        times = [0.0, 0.25, 0.5, 0.75, 1.0]
        rotations = [[0, 0, math.sin(t*6.28)*0.18, math.cos(math.asin(min(0.99,abs(math.sin(t*6.28)*0.18))))]
                     for t in times]
        times_acc = b.accessor(times, 5126, "SCALAR", minmax=True)
        rot_acc = b.accessor(rotations, 5126, "VEC4")
        b.doc["animations"].append({"name": "IdleWave", "samplers": [
            {"input": times_acc, "output": rot_acc, "interpolation": "LINEAR"}],
            "channels": [{"sampler": 0, "target": {"node": indices[names[0]], "path": "rotation"}}]})
    return b.build()


def static_glb(material_count: int = 1, texture: bool = False,
               missing_texture: bool = False, large: bool = False) -> bytes:
    b = Builder()
    mats = []
    for i in range(material_count):
        tex = b.png_texture(f"albedo_{i}", (60+i*50, 120, 200-i*30)) if texture else None
        mats.append(b.material(f"Material_{i}", (0.3+i*0.2, 0.6, 0.8, 1.0),
                               metallic=0.1*i, roughness=0.4+0.1*i, texture=tex))
    if missing_texture:
        b.doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 99}
    if large:
        # Real high-vertex static mesh (not a fake metadata claim).
        n = 20000
        positions = [[math.sin(i*0.01)*5, 0.02*(i % 7), math.cos(i*0.01)*5] for i in range(n)]
        normals = [[0,1,0] for _ in range(n)]
        indices = [i % n for i in range(n - (n % 3))]
        attrs = {"POSITION": b.accessor(positions, 5126, "VEC3", 34962, minmax=True),
                 "NORMAL": b.accessor(normals, 5126, "VEC3", 34962)}
        b.doc["meshes"].append({"name": "Terrain", "primitives": [
            {"attributes": attrs, "indices": b.accessor(indices, 5125, "SCALAR", 34963),
             "material": 0, "mode": 4}]})
    else:
        mesh = b.box("Cube", mats[0], (0.5, 0.5, 0.5))
        if material_count > 1:
            b.doc["meshes"][mesh]["primitives"][0]["material"] = mats[1]
        b.node("CubeNode", mesh=mesh)
        b.doc["scenes"][0]["nodes"] = [len(b.doc["nodes"]) - 1]
    return b.build()


def bvh_text(prefix: str = "", quadruped: bool = False, frames: int = 60) -> str:
    if quadruped:
        joints = [("Hips", None, [0, 1, 0], ["Xposition", "Yposition", "Zposition", "Zrotation", "Xrotation", "Yrotation"]),
                  ("Spine", "Hips", [0, 0, 0.4], ["Zrotation", "Xrotation", "Yrotation"]),
                  ("FrontLeftLeg", "Spine", [0.3, -0.5, 0.3], ["Zrotation", "Xrotation", "Yrotation"]),
                  ("FrontRightLeg", "Spine", [-0.3, -0.5, 0.3], ["Zrotation", "Xrotation", "Yrotation"]),
                  ("BackLeftLeg", "Hips", [0.3, -0.5, -0.3], ["Zrotation", "Xrotation", "Yrotation"]),
                  ("BackRightLeg", "Hips", [-0.3, -0.5, -0.3], ["Zrotation", "Xrotation", "Yrotation"])]
    else:
        joints = []
        for name in HUMANOID:
            parent = PARENTS[name]
            chans = ["Zrotation", "Xrotation", "Yrotation"]
            if parent is None:
                chans = ["Xposition", "Yposition", "Zposition"] + chans
            joints.append((prefix + name, prefix + parent if parent else None, OFFSETS[name], chans))
    lines = ["HIERARCHY"]
    stack: list[str] = []
    for idx, (name, parent, offset, chans) in enumerate(joints):
        while stack and stack[-1] != parent:
            stack.pop(); lines.append("}")
        if parent is None:
            lines.append(f"ROOT {name}")
        else:
            lines.append(f"JOINT {name}")
        lines.append("{")
        lines.append("OFFSET %.4f %.4f %.4f" % tuple(offset))
        lines.append("CHANNELS %d %s" % (len(chans), " ".join(chans)))
        if idx < len(joints) - 1 and joints[idx+1][1] == name:
            stack.append(name)
        else:
            lines.append("End Site")
            lines.append("{")
            lines.append("OFFSET 0.0000 -0.1000 0.0000")
            lines.append("}")
            lines.append("}")
            while stack and stack[-1] != (joints[idx+1][1] if idx+1 < len(joints) else None):
                stack.pop(); lines.append("}")
            if idx+1 < len(joints) and joints[idx+1][1] is not None:
                # reopen parent chain handled by repeated ROOT/JOINT lines below
                pass
    # The simple writer above can emit extra braces; use a robust recursive writer instead.
    return _bvh_recursive(prefix, quadruped, frames)


def _bvh_recursive(prefix: str, quadruped: bool, frames: int) -> str:
    if quadruped:
        spec = {"Hips": (None, [0, 0.9, 0], 6),
                "Spine": ("Hips", [0, 0, 0.4], 3),
                "FrontLeftLeg": ("Spine", [0.3, -0.4, 0.3], 3),
                "FrontRightLeg": ("Spine", [-0.3, -0.4, 0.3], 3),
                "BackLeftLeg": ("Hips", [0.3, -0.4, -0.3], 3),
                "BackRightLeg": ("Hips", [-0.3, -0.4, -0.3], 3)}
        order = list(spec)
    else:
        spec = {prefix + n: (prefix + PARENTS[n] if PARENTS[n] else None, OFFSETS[n], 6 if PARENTS[n] is None else 3)
                for n in HUMANOID}
        order = [prefix + n for n in HUMANOID]
    children: dict[str | None, list[str]] = {}
    for name in order:
        children.setdefault(spec[name][0], []).append(name)
    channel_count = sum(spec[n][2] for n in order)
    lines = ["HIERARCHY"]

    def emit(name: str, depth: int) -> None:
        parent, offset, count = spec[name]
        lines.append("  "*depth + ("ROOT " if parent is None else "JOINT ") + name)
        lines.append("  "*depth + "{")
        lines.append("  "*(depth+1) + "OFFSET %.4f %.4f %.4f" % tuple(offset))
        chans = (["Xposition", "Yposition", "Zposition"] if count == 6 else []) + ["Zrotation", "Xrotation", "Yrotation"]
        lines.append("  "*(depth+1) + "CHANNELS %d %s" % (len(chans), " ".join(chans)))
        kids = children.get(name, [])
        if kids:
            for kid in kids:
                emit(kid, depth+1)
        else:
            lines.append("  "*(depth+1) + "End Site")
            lines.append("  "*(depth+1) + "{")
            lines.append("  "*(depth+2) + "OFFSET 0.0000 -0.1000 0.0000")
            lines.append("  "*(depth+1) + "}")
        lines.append("  "*depth + "}")
    for root in children.get(None, []):
        emit(root, 0)
    lines += ["MOTION", f"Frames: {frames}", "Frame Time: 0.033333"]
    for f in range(frames):
        row = []
        for name in order:
            count = spec[name][2]
            if count == 6:
                row += [0.0, 0.9 + 0.02*math.sin(f*0.2), 0.0]
            row += [8.0*math.sin(f*0.15), 3.0*math.cos(f*0.11), 2.0*math.sin(f*0.07)]
        assert len(row) == channel_count
        lines.append(" ".join("%.4f" % v for v in row))
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ASSETS))
    args = ap.parse_args()
    out = Path(args.out)
    dirs = {k: out / k for k in ("models/characters", "models/environments", "models/props",
                                 "animation/locomotion", "animation/combat", "animation/other")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    files = {
        "models/characters/warrior.glb": humanoid_glb(),
        "models/characters/scout.glb": humanoid_glb("mixamorig:"),
        "models/characters/rigged_animated.glb": humanoid_glb(with_animation=True),
        "models/props/crate.glb": static_glb(),
        "models/props/multi_material_pbr.glb": static_glb(material_count=3, texture=True),
        "models/props/missing_texture.glb": static_glb(material_count=2, missing_texture=True),
        "models/environments/large_terrain.glb": static_glb(large=True),
        "models/props/invalid.glb": b"glTF" + struct.pack("<II", 2, 720) + b"\x00"*16,
        "animation/locomotion/walk.bvh": bvh_text(frames=60).encode(),
        "animation/locomotion/run.bvh": bvh_text(frames=40).encode(),
        "animation/locomotion/idle.bvh": bvh_text(frames=90).encode(),
        "animation/locomotion/jump.bvh": bvh_text(frames=30).encode(),
        "animation/locomotion/walk_fast.bvh": bvh_text(frames=45).encode(),
        "animation/combat/attack.bvh": bvh_text(frames=35).encode(),
        "animation/combat/death.bvh": bvh_text(frames=70).encode(),
        "animation/other/quadruped.bvh": bvh_text(quadruped=True, frames=48).encode(),
        "animation/other/different_naming.bvh": bvh_text("mixamorig:", frames=50).encode(),
        "animation/other/embedded_clips.glb": humanoid_glb(with_animation=True),
    }
    for rel, data in files.items():
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            data = data.encode()
        target.write_bytes(data)
        print(f"{target.relative_to(ROOT)}: {len(data)} B")
    # Honest note: FBX/DAE/OBJ/STL/PLY fixtures require Blender to author; the
    # workflow generates them with the bundled headless script when Blender exists.
    (out / "README.txt").write_text(
        "Generated verification dataset (A-L). Source files are intentionally not committed.\n"
        "FBX/DAE/OBJ/STL/PLY fixtures are produced by scripts/generate_blender_fixtures.py\n"
        "when Blender 4.2 LTS is available; the pure-Python generator writes real GLB/BVH only.\n",
        encoding="utf-8")
    print("dataset written")


if __name__ == "__main__":
    main()
