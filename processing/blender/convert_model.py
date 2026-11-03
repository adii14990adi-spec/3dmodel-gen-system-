"""Convert an imported source model to a self-contained runtime GLB (processed copy)."""
from __future__ import annotations

import sys
from processing.blender._common import finish, guard

guard()
import bpy

argv = sys.argv
tail = argv[argv.index("--") + 1:]
source, target = tail[0], tail[1]
bpy.ops.wm.read_factory_settings(use_empty=True)
ext = source.rsplit(".", 1)[-1].lower()
if ext == "fbx": bpy.ops.import_scene.fbx(filepath=source, use_anim=False)
elif ext == "obj": bpy.ops.wm.obj_import(filepath=source)
elif ext == "stl": bpy.ops.wm.stl_import(filepath=source)
elif ext == "ply": bpy.ops.wm.ply_import(filepath=source)
elif ext == "dae": bpy.ops.wm.collada_import(filepath=source)
elif ext in ("glb", "gltf"): bpy.ops.import_scene.gltf(filepath=source)
else: raise ValueError(f"unsupported source: {ext}")
# Never discard skeletons or animations; export everything present.
bpy.ops.export_scene.gltf(filepath=target, export_format="GLB",
                          export_animations=True, export_skins=True,
                          export_materials="EXPORT", export_apply=False)
finish({"source": source, "target": target, "exported": True})
