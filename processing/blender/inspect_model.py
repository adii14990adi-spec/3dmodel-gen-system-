"""Inspect any Blender-importable model and emit real geometry/rig/material JSON."""
from __future__ import annotations

from processing.blender._common import finish, guard

guard()
import bpy

argv = __import__("sys").argv
source = argv[argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
ext = source.rsplit(".", 1)[-1].lower()
if ext == "fbx": bpy.ops.import_scene.fbx(filepath=source, use_anim=False)
elif ext == "obj": bpy.ops.wm.obj_import(filepath=source)
elif ext == "stl": bpy.ops.wm.stl_import(filepath=source)
elif ext == "ply": bpy.ops.wm.ply_import(filepath=source)
elif ext == "dae": bpy.ops.wm.collada_import(filepath=source)
elif ext in ("glb", "gltf"): bpy.ops.import_scene.gltf(filepath=source)
else: raise ValueError(f"unsupported source: {ext}")

meshes, armatures, materials, actions = [], [], [], []
for obj in bpy.data.objects:
    if obj.type == "MESH":
        meshes.append({"name": obj.name, "vertices": len(obj.data.vertices),
                       "polygons": len(obj.data.polygons), "uv_layers": [uv.name for uv in obj.data.uv_layers],
                       "has_normals": True, "materials": [m.name if m else None for m in obj.data.materials],
                       "vertex_groups": [g.name for g in obj.vertex_groups],
                       "modifiers": [m.type for m in obj.modifiers]})
    elif obj.type == "ARMATURE":
        armatures.append({"name": obj.name, "bones": [{"name": b.name, "parent": b.parent.name if b.parent else None,
                          "head": list(b.head_local), "tail": list(b.tail_local)} for b in obj.data.bones]})
for mat in bpy.data.materials:
    materials.append({"name": mat.name, "use_nodes": mat.use_nodes})
for action in bpy.data.actions:
    actions.append({"name": action.name, "frame_range": list(action.frame_range)})
finish({"source": source, "meshes": meshes, "armatures": armatures,
        "materials": materials, "actions": actions, "blender": bpy.app.version_string})
